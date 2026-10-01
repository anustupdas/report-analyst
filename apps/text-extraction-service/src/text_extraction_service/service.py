from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from mistralai.client import Mistral
from pydantic import BaseModel, Field

from text_extraction_service.exceptions import MistralApiKeyMissingError, MistralDisabledError, UnsupportedFormatError
from text_extraction_service.extractor import ExtractedPage, extract_ocr_mistral, extract_text_pymupdf, join_pages
from text_extraction_service.metrics import observe_extraction
from text_extraction_service.utils import (
    ServiceConfig,
    build_file_resolver,
    get_file_extension,
    initialize_configuration,
)

logger = logging.getLogger(__name__)


class ExtractedFile(BaseModel):
    text: str = Field(description="All pages' search text joined with blank lines")
    pages: list[ExtractedPage] = Field(default_factory=list, description="Per-page text and layout, in page order")
    text_format: str = Field(description="plain (PyMuPDF) or markdown (Mistral OCR)")
    method: str = Field(description="pymupdf or mistral")
    model: str | None = Field(None, description="OCR model that processed the file (Mistral only)")
    usage: dict[str, Any] = Field(default_factory=dict, description="pages_processed, doc_size_bytes")
    duration_ms: int = 0
    extension: str
    file_ref: str
    storage: str = Field(description="local or s3")


class TextExtractionService:
    def __init__(self, config: ServiceConfig | None = None) -> None:
        self.config = config or initialize_configuration()
        self.supported_formats = [fmt.lower() for fmt in self.config.supported_formats]
        self.image_formats = [fmt.lower() for fmt in self.config.image_formats]
        self.resolver = build_file_resolver(self.config)
        self._mistral: Mistral | None = None

        if self.config.use_mistral:
            if not self.config.mistral_api_key:
                raise MistralApiKeyMissingError("MISTRAL_API_KEY is required when mistral.enabled / USE_MISTRAL=true")
            self._mistral = Mistral(api_key=self.config.mistral_api_key)
            logger.info(
                "Mistral OCR enabled (model=%s options=%s)",
                self.config.mistral_model,
                self.config.mistral_options.request_kwargs(),
            )
        else:
            logger.info("Mistral OCR disabled — image files will be rejected")

    def extract_text_from_file(self, file_ref: str, use_ocr: bool = False) -> ExtractedFile:
        path = self.resolver.resolve(file_ref)
        extension = get_file_extension(path)
        is_image = extension in self.image_formats
        method = "unknown"
        started = time.perf_counter()
        try:
            if extension not in self.supported_formats:
                raise UnsupportedFormatError(f"Unsupported format '{extension}'. Supported: {self.supported_formats}")
            method = self._select_method(is_image=is_image, use_ocr=use_ocr)
            if method == "mistral":
                assert self._mistral is not None
                options = self.config.mistral_options
                logger.info(
                    "extract.start file_ref=%s method=mistral model=%s use_ocr=%s options=%s",
                    file_ref,
                    self.config.mistral_model,
                    use_ocr,
                    options.request_kwargs(),
                )
                output = extract_ocr_mistral(self._mistral, path, self.config.mistral_model, options)
            else:
                reason = "use_ocr=false" if self.config.use_mistral else "mistral disabled"
                logger.info("extract.start file_ref=%s method=pymupdf (%s)", file_ref, reason)
                output = extract_text_pymupdf(path)
        except Exception:
            observe_extraction(
                extension=extension,
                method=method,
                status="error",
                duration_seconds=time.perf_counter() - started,
            )
            self._cleanup_if_s3(path)
            raise

        self._cleanup_if_s3(path)
        duration_ms = int((time.perf_counter() - started) * 1000)
        pages = output.pages
        logger.info(
            "extract.done file_ref=%s method=%s model=%s pages=%d tables=%d images=%d blocks=%d chars=%d duration_ms=%d",
            file_ref,
            method,
            output.model or "-",
            len(pages),
            sum(len(page.tables) for page in pages),
            sum(len(page.images) for page in pages),
            sum(len(page.blocks) for page in pages),
            sum(len(page.text) for page in pages),
            duration_ms,
        )
        observe_extraction(
            extension=extension,
            method=method,
            status="ok",
            duration_seconds=duration_ms / 1000,
            pages=len(pages),
            chars=sum(len(page.text) for page in pages),
        )

        return ExtractedFile(
            text=join_pages(pages),
            pages=pages,
            text_format="markdown" if method == "mistral" else "plain",
            method=method,
            model=output.model,
            usage=output.usage,
            duration_ms=duration_ms,
            extension=extension,
            file_ref=file_ref,
            storage="s3" if self.config.use_s3 else "local",
        )

    def _select_method(self, *, is_image: bool, use_ocr: bool) -> str:
        """Whole-file method selection — no per-page OCR heuristics."""
        wants_mistral = is_image or use_ocr

        if not wants_mistral:
            return "pymupdf"

        if not self.config.use_mistral or self._mistral is None:
            if is_image:
                raise MistralDisabledError("Image files require Mistral OCR. Set USE_MISTRAL=true and MISTRAL_API_KEY.")
            raise MistralDisabledError("use_ocr=true requires USE_MISTRAL=true and MISTRAL_API_KEY.")

        return "mistral"

    def _cleanup_if_s3(self, path: Path) -> None:
        if not self.config.use_s3:
            return
        try:
            if path.exists():
                os.remove(path)
                logger.info("[Cleanup] Deleted temp S3 download: %s", path)
        except OSError as exc:
            logger.warning("[Cleanup] Failed to delete temp file %s: %s", path, exc)
