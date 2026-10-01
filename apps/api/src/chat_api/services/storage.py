from __future__ import annotations

import base64
import json
import logging
import mimetypes
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID

from chat_api.config import Settings
from chat_api.modules.documents.enums import ArtifactIdentifier
from chat_api.security import safe_join
from chat_api.tracing import structured_log

logger = logging.getLogger(__name__)


class LocalStorageService:
    """Local filesystem stand-in for S3 upload + artifacts buckets."""

    def __init__(self, settings: Settings) -> None:
        self.root = settings.data_root
        self.root.mkdir(parents=True, exist_ok=True)

    def document_dir(self, user_id: UUID, project_id: UUID, document_id: UUID) -> Path:
        path = safe_join(self.root, str(user_id), str(project_id), str(document_id))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def relative_path(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.root.resolve()))

    def original_filename_for(self, extension: str) -> str:
        ext = extension.lstrip(".")
        return f"original.{ext}" if ext else "original.bin"

    def write_upload(
        self,
        *,
        user_id: UUID,
        project_id: UUID,
        document_id: UUID,
        extension: str,
        data: bytes,
        request_id: str | None = None,
    ) -> tuple[Path, str, int]:
        doc_dir = self.document_dir(user_id, project_id, document_id)
        filename = self.original_filename_for(extension)
        dest = doc_dir / filename
        dest.write_bytes(data)
        rel = self.relative_path(dest)
        structured_log(
            logger,
            "storage.upload_written",
            request_id=request_id,
            document_id=document_id,
            bytes=len(data),
            path=rel,
        )
        return dest, rel, len(data)

    def write_text_artifact(
        self,
        *,
        user_id: UUID,
        project_id: UUID,
        document_id: UUID,
        identifier: ArtifactIdentifier,
        text: str,
        request_id: str | None = None,
    ) -> tuple[Path, str, int]:
        doc_dir = self.document_dir(user_id, project_id, document_id)
        name = {
            ArtifactIdentifier.EXTRACTED_TEXT: "extracted-text.txt",
            ArtifactIdentifier.SUMMARY: "summary.txt",
        }[identifier]
        dest = doc_dir / name
        encoded = text.encode("utf-8")
        dest.write_bytes(encoded)
        rel = self.relative_path(dest)
        structured_log(
            logger,
            "storage.artifact_written",
            request_id=request_id,
            document_id=document_id,
            identifier=identifier.value,
            bytes=len(encoded),
            path=rel,
        )
        return dest, rel, len(encoded)

    def write_layout_artifact(
        self,
        *,
        user_id: UUID,
        project_id: UUID,
        document_id: UUID,
        extraction: dict[str, Any],
        request_id: str | None = None,
    ) -> tuple[Path, str, int]:
        """Store the per-page layout for re-rendering; base64 images become files under images/."""
        doc_dir = self.document_dir(user_id, project_id, document_id)
        images_dir = doc_dir / "images"
        if images_dir.exists():
            shutil.rmtree(images_dir)

        pages = []
        image_count = 0
        for page in extraction.get("pages") or []:
            page = dict(page)
            images = []
            for image in page.get("images") or []:
                image = dict(image)
                data_url = image.pop("image_base64", None)
                if data_url:
                    name = f"p{page.get('page_number')}-{Path(str(image.get('id'))).name}"
                    images_dir.mkdir(parents=True, exist_ok=True)
                    (images_dir / name).write_bytes(base64.b64decode(data_url.split(",", 1)[-1]))
                    image["file"] = f"images/{name}"
                    image_count += 1
                images.append(image)
            page["images"] = images
            pages.append(page)

        layout = {
            key: extraction.get(key) for key in ("method", "model", "text_format", "usage", "duration_ms", "extension")
        }
        layout["pages"] = pages
        dest = doc_dir / "extracted-layout.json"
        encoded = json.dumps(layout, ensure_ascii=False).encode("utf-8")
        dest.write_bytes(encoded)
        rel = self.relative_path(dest)
        structured_log(
            logger,
            "storage.artifact_written",
            request_id=request_id,
            document_id=document_id,
            identifier=ArtifactIdentifier.EXTRACTED_LAYOUT.value,
            bytes=len(encoded),
            images=image_count,
            path=rel,
        )
        return dest, rel, len(encoded)

    def delete_document_dir(self, user_id: UUID, project_id: UUID, document_id: UUID) -> None:
        path = safe_join(self.root, str(user_id), str(project_id), str(document_id))
        if path.exists():
            shutil.rmtree(path)

    @staticmethod
    def guess_mime(filename: str) -> str:
        mime, _ = mimetypes.guess_type(filename)
        return mime or "application/octet-stream"
