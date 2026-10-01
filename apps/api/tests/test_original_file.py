from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from chat_api.errors import NotFoundError
from chat_api.modules import actions
from chat_api.modules.documents.enums import ArtifactIdentifier


class StubSettings:
    def __init__(self, root: Path):
        self.data_root = root


def test_resolve_original_file_reads_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    user_id = uuid4()
    project_id = uuid4()
    document_id = uuid4()
    rel = f"{user_id}/{project_id}/{document_id}/original.pdf"
    path = tmp_path / rel
    path.parent.mkdir(parents=True)
    path.write_bytes(b"%PDF-1.4 fake")

    document = SimpleNamespace(
        id=document_id,
        project_id=project_id,
        user_id=user_id,
        original_filename="report.pdf",
        mime_type="application/pdf",
    )
    artifact = SimpleNamespace(
        identifier=ArtifactIdentifier.ORIGINAL_PDF.value,
        storage_path=rel,
        mime_type="application/pdf",
    )

    class FakeResult:
        def scalar_one_or_none(self):
            return artifact

    class FakeDb:
        def execute(self, _stmt):
            return FakeResult()

    monkeypatch.setattr(actions, "get_project_document", lambda db, **kwargs: document)

    resolved_doc, resolved_artifact, resolved_path = actions.resolve_original_file(
        FakeDb(),
        user=SimpleNamespace(id=user_id),
        project_id=project_id,
        document_id=document_id,
        settings=StubSettings(tmp_path),
    )
    assert resolved_doc is document
    assert resolved_artifact is artifact
    assert resolved_path == path.resolve()


def test_resolve_original_file_missing_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    document = SimpleNamespace(id=uuid4(), project_id=uuid4(), user_id=uuid4())

    class FakeResult:
        def scalar_one_or_none(self):
            return None

    class FakeDb:
        def execute(self, _stmt):
            return FakeResult()

    monkeypatch.setattr(actions, "get_project_document", lambda db, **kwargs: document)

    with pytest.raises(NotFoundError):
        actions.resolve_original_file(
            FakeDb(),
            user=SimpleNamespace(id=document.user_id),
            project_id=document.project_id,
            document_id=document.id,
            settings=StubSettings(tmp_path),
        )
