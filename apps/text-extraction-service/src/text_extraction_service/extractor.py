"""Extraction backends: PyMuPDF (plain text) and Mistral OCR (markdown + layout), both per page."""

from __future__ import annotations

import base64
import gc
import logging
from dataclasses import dataclass, field
from mimetypes import guess_type
from pathlib import Path
from typing import Any, Literal

import pymupdf
from mistralai.client import Mistral
from pydantic import BaseModel, Field

from text_extraction_service.constants import DEFAULT_IMAGE_FORMATS, DEFAULT_MISTRAL_MODEL
from text_extraction_service.exceptions import (
    FileNotFoundOnDiskError,
    MistralAPIError,
    OCRRateLimitError,
    TextExtractionFailedError,
)
from text_extraction_service.formatting import inline_tables, strip_image_refs

logger = logging.getLogger(__name__)

__all__ = (
    "ExtractedPage",
    "ExtractionOutput",
    "MistralOptions",
    "extract_text_pymupdf",
    "extract_ocr_mistral",
    "join_pages",
)


class PageDimensions(BaseModel):
    width: int
    height: int
    dpi: int | None = None


class PageTable(BaseModel):
    id: str
    format: str = Field(description="html or markdown")
    content: str


class PageImage(BaseModel):
    id: str
    bbox: list[int] | None = Field(None, description="[x0, y0, x1, y1] in page pixels")
    image_base64: str | None = Field(None, description="data URL, when mistral.includeImages is on")


class PageBlock(BaseModel):
    type: str = Field(description="title, text, table, image, list, caption, header, footer, equation, ...")
    bbox: list[int] | None = Field(None, description="[x0, y0, x1, y1] in page pixels")
    content: str | None = None
    ref: str | None = Field(None, description="table or image id the block points to")


class PageConfidence(BaseModel):
    average: float | None = None
    minimum: float | None = None


class ExtractedPage(BaseModel):
    page_number: int = Field(description="1-based page number in the source file")
    text: str = Field(description="Search text: header/footer removed, tables inlined as markdown, no image refs")
    markdown: str | None = Field(
        None, description="Mistral markdown with [tbl-N] / ![img-N] placeholders, for rendering"
    )
    header: str | None = None
    footer: str | None = None
    dimensions: PageDimensions | None = None
    tables: list[PageTable] = Field(default_factory=list)
    images: list[PageImage] = Field(default_factory=list)
    blocks: list[PageBlock] = Field(default_factory=list)
    hyperlinks: list[str] = Field(default_factory=list)
    confidence: PageConfidence | None = None


@dataclass(frozen=True)
class MistralOptions:
    table_format: Literal["markdown", "html"] = "html"
    extract_header: bool = True
    extract_footer: bool = True
    include_images: bool = True
    image_min_size: int | None = 50
    image_limit: int | None = None
    include_blocks: bool = True
    confidence_granularity: Literal["page", "block", "word"] | None = "page"

    def request_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "table_format": self.table_format,
            "extract_header": self.extract_header,
            "extract_footer": self.extract_footer,
            "include_image_base64": self.include_images,
            "include_blocks": self.include_blocks,
        }
        if self.image_min_size:
            kwargs["image_min_size"] = self.image_min_size
        if self.image_limit is not None:
            kwargs["image_limit"] = self.image_limit
        if self.confidence_granularity:
            kwargs["confidence_scores_granularity"] = self.confidence_granularity
        return kwargs


@dataclass
class ExtractionOutput:
    pages: list[ExtractedPage]
    model: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


def _is_rate_limit_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return True
    message = str(exc).lower()
    return "429" in message or "rate limit" in message or "rate_limited" in message


def join_pages(pages: list[ExtractedPage]) -> str:
    return "\n\n".join(page.text for page in pages).strip()


def extract_text_pymupdf(file_path: str | Path) -> ExtractionOutput:
    """Extract text page by page with PyMuPDF. No page-skip / OCR heuristics."""
    pages: list[ExtractedPage] = []
    try:
        with pymupdf.open(file_path) as doc:
            for number, page in enumerate(doc, start=1):
                text = (page.get_text() or "").strip()
                if text:
                    rect = page.rect
                    pages.append(
                        ExtractedPage(
                            page_number=number,
                            text=text,
                            dimensions=PageDimensions(width=int(rect.width), height=int(rect.height), dpi=72),
                        )
                    )
    except Exception as exc:
        logger.exception("PyMuPDF failed for %s", file_path)
        raise TextExtractionFailedError(f"PyMuPDF extraction failed: {exc}") from exc
    finally:
        gc.collect()

    if not pages:
        raise TextExtractionFailedError("PyMuPDF returned empty text.")
    return ExtractionOutput(pages=pages, usage={"pages_processed": len(pages)})


def _bbox(item: Any) -> list[int] | None:
    coords = [getattr(item, name, None) for name in ("top_left_x", "top_left_y", "bottom_right_x", "bottom_right_y")]
    return [int(value) for value in coords] if all(value is not None for value in coords) else None


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _convert_page(page: Any, position: int) -> ExtractedPage | None:
    markdown = (getattr(page, "markdown", None) or "").strip()
    header = _optional_str(getattr(page, "header", None))
    footer = _optional_str(getattr(page, "footer", None))
    tables = [
        PageTable(id=table.id, format=str(getattr(table, "format_", None) or "markdown"), content=table.content)
        for table in getattr(page, "tables", None) or []
    ]
    text = strip_image_refs(inline_tables(markdown, {table.id: (table.format, table.content) for table in tables}))
    if not text and not header and not footer:
        return None

    index = getattr(page, "index", None)
    dims = getattr(page, "dimensions", None)
    scores = getattr(page, "confidence_scores", None)
    return ExtractedPage(
        page_number=int(index) + 1 if isinstance(index, int) else position + 1,
        text=text,
        markdown=markdown or None,
        header=header,
        footer=footer,
        dimensions=PageDimensions(width=dims.width, height=dims.height, dpi=dims.dpi) if dims else None,
        tables=tables,
        images=[
            PageImage(id=image.id, bbox=_bbox(image), image_base64=_optional_str(getattr(image, "image_base64", None)))
            for image in getattr(page, "images", None) or []
        ],
        blocks=[
            PageBlock(
                type=str(getattr(block, "type", "unknown")),
                bbox=_bbox(block),
                content=_optional_str(getattr(block, "content", None)),
                ref=_optional_str(getattr(block, "table_id", None) or getattr(block, "image_id", None)),
            )
            for block in getattr(page, "blocks", None) or []
        ],
        hyperlinks=list(getattr(page, "hyperlinks", None) or []),
        confidence=(
            PageConfidence(
                average=getattr(scores, "average_page_confidence_score", None),
                minimum=getattr(scores, "minimum_page_confidence_score", None),
            )
            if scores
            else None
        ),
    )


def extract_ocr_mistral(
    mistral_client: Mistral,
    file_path: str | Path,
    mistral_model_version: str = DEFAULT_MISTRAL_MODEL,
    options: MistralOptions | None = None,
) -> ExtractionOutput:
    """Run Mistral OCR on the whole file (image or document); returns markdown + layout per page."""
    file = Path(file_path)
    if not file.is_file():
        raise FileNotFoundOnDiskError(f"{file_path} is not a valid file")

    options = options or MistralOptions()
    extension = file.suffix.lower().lstrip(".")
    is_image = extension in DEFAULT_IMAGE_FORMATS
    ocr_response = None

    try:
        if is_image:
            encoded = base64.b64encode(file.read_bytes()).decode()
            mime_type, _ = guess_type(str(file))
            mime_type = mime_type or "application/octet-stream"
            document = {"type": "image_url", "image_url": f"data:{mime_type};base64,{encoded}"}
        else:
            uploaded = mistral_client.files.upload(
                file={"file_name": file.name, "content": file.read_bytes()},
                purpose="ocr",
            )
            signed = mistral_client.files.get_signed_url(file_id=uploaded.id, expiry=1)
            document = {"type": "document_url", "document_url": signed.url}

        ocr_response = mistral_client.ocr.process(
            model=mistral_model_version,
            document=document,
            **options.request_kwargs(),
        )

        pages = [
            converted
            for position, page in enumerate(getattr(ocr_response, "pages", None) or [])
            if (converted := _convert_page(page, position)) is not None
        ]
        if not pages or not any(page.text for page in pages):
            raise TextExtractionFailedError("Mistral OCR returned empty text.")

        usage_info = getattr(ocr_response, "usage_info", None)
        usage = {
            "pages_processed": getattr(usage_info, "pages_processed", None),
            "doc_size_bytes": getattr(usage_info, "doc_size_bytes", None),
        }
        return ExtractionOutput(
            pages=pages,
            model=getattr(ocr_response, "model", None) or mistral_model_version,
            usage={key: value for key, value in usage.items() if isinstance(value, int)},
        )

    except TextExtractionFailedError:
        raise
    except FileNotFoundOnDiskError:
        raise
    except Exception as exc:
        if _is_rate_limit_error(exc):
            logger.warning("Mistral OCR rate limit hit for %s: %s", file_path, exc)
            raise OCRRateLimitError(
                "Mistral OCR rate limit exceeded. Wait a bit and retry, or try without use_ocr."
            ) from exc
        logger.exception("Mistral OCR failed for %s", file_path)
        raise MistralAPIError(f"Mistral OCR failed: {exc}") from exc
    finally:
        del ocr_response
        gc.collect()
