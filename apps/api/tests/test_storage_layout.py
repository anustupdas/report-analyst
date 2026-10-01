from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path

from chat_api.config import Settings
from chat_api.services.storage import LocalStorageService
from chat_api.workflows.ingest import _pages_from_extraction


def test_layout_artifact_writes_images_as_files(tmp_path: Path) -> None:
    storage = LocalStorageService(Settings(_env_file=None, data_root=str(tmp_path)))
    ids = dict(user_id=uuid.uuid4(), project_id=uuid.uuid4(), document_id=uuid.uuid4())
    pixel = base64.b64encode(b"\xff\xd8jpeg-bytes").decode()
    extraction = {
        "method": "mistral",
        "model": "mistral-ocr-4-1",
        "text_format": "markdown",
        "usage": {"pages_processed": 1},
        "text": "ignored",
        "pages": [
            {
                "page_number": 3,
                "text": "Body",
                "markdown": "Body\n\n![img-0.jpeg](img-0.jpeg)",
                "images": [
                    {"id": "img-0.jpeg", "bbox": [1, 2, 3, 4], "image_base64": f"data:image/jpeg;base64,{pixel}"}
                ],
            }
        ],
    }

    path, rel, size = storage.write_layout_artifact(extraction=extraction, **ids)

    layout = json.loads(path.read_text())
    assert rel.endswith("extracted-layout.json") and size == path.stat().st_size
    assert layout["model"] == "mistral-ocr-4-1"
    assert "text" not in layout
    image = layout["pages"][0]["images"][0]
    assert "image_base64" not in image
    assert image["file"] == "images/p3-img-0.jpeg"
    assert (path.parent / image["file"]).read_bytes() == b"\xff\xd8jpeg-bytes"


def test_pages_from_extraction_uses_header_as_hint_and_drops_footer() -> None:
    pages = _pages_from_extraction(
        {"pages": [{"page_number": 2, "header": "Strategy", "text": "Body text.", "footer": "ACME | 2"}]},
        "fallback",
    )
    assert (pages[0].page_number, pages[0].text, pages[0].header) == (2, "Body text.", "Strategy")
    assert _pages_from_extraction({}, "fallback")[0].text == "fallback"
