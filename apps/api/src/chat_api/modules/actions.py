from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from chat_api.config import Settings
from chat_api.errors import AppError, ConflictError, NotFoundError
from chat_api.modules.documents.enums import ArtifactIdentifier, DocumentStatus
from chat_api.modules.models import Artifact, Document, Project, User
from chat_api.modules.policies import (
    claim_document_for_processing,
    get_owned_project,
    get_project_document,
    pending_sources_count,
    restore_document_status,
)
from chat_api.security import extension_of, generate_api_token, hash_api_token, safe_join
from chat_api.services.storage import LocalStorageService


def create_user(db: Session, *, email: str, display_name: str) -> tuple[User, str]:
    existing = db.execute(select(User).where(User.email == email.lower().strip())).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("A user with this email already exists")

    token = generate_api_token()
    user = User(
        email=email.lower().strip(),
        display_name=display_name.strip(),
        api_token_hash=hash_api_token(token),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, token


def regenerate_user_token(db: Session, *, email: str) -> tuple[User, str]:
    """Issue a new apiToken for an existing user (local prototype recovery).

    The previous token stays valid until the next regenerate so open browser
    sessions for the same user keep working. Plaintext of the new token is
    returned once.
    """
    user = db.execute(select(User).where(User.email == email.lower().strip())).scalar_one_or_none()
    if user is None:
        raise NotFoundError("No user with this email")

    token = generate_api_token()
    user.previous_api_token_hash = user.api_token_hash
    user.api_token_hash = hash_api_token(token)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, token


def create_project(db: Session, *, user: User, name: str) -> Project:
    project = Project(user_id=user.id, name=name.strip(), thread_id=uuid4())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def prepare_document(
    db: Session,
    *,
    user: User,
    project_id: UUID,
    filename: str,
    title: str | None,
    settings: Settings,
) -> Document:
    project = get_owned_project(db, project_id=project_id, user_id=user.id)
    ext = extension_of(filename)
    if not ext or ext not in settings.allowed_extension_set:
        raise AppError(
            f"Unsupported file extension '.{ext}'. Allowed: {sorted(settings.allowed_extension_set)}",
            status_code=415,
            code="unsupported_media_type",
        )

    doc_title = (title or filename).strip()
    document = Document(
        project_id=project.id,
        user_id=user.id,
        title=doc_title,
        original_filename=filename.strip(),
        mime_type=LocalStorageService.guess_mime(filename),
        status=DocumentStatus.PENDING.value,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    # Create the document folder before the client uploads bytes.
    storage = LocalStorageService(settings)
    storage.document_dir(user.id, project.id, document.id)
    return document


def save_document_content(
    db: Session,
    *,
    user: User,
    project_id: UUID,
    document_id: UUID,
    filename: str,
    data: bytes,
    settings: Settings,
    request_id: str | None = None,
) -> Document:
    document = get_project_document(db, project_id=project_id, document_id=document_id, user_id=user.id)
    if document.status not in (DocumentStatus.PENDING.value, DocumentStatus.FAILED.value):
        raise ConflictError(f"Cannot upload content while document status is '{document.status}'")

    if len(data) == 0:
        raise AppError("Empty file", status_code=422, code="empty_file")
    if len(data) > settings.max_upload_bytes:
        raise AppError(
            f"File exceeds max upload size of {settings.max_upload_bytes} bytes",
            status_code=413,
            code="payload_too_large",
        )

    ext = extension_of(filename or document.original_filename)
    if ext not in settings.allowed_extension_set:
        raise AppError(
            f"Unsupported file extension '.{ext}'",
            status_code=415,
            code="unsupported_media_type",
        )

    storage = LocalStorageService(settings)
    _path, rel, size = storage.write_upload(
        user_id=user.id,
        project_id=project_id,
        document_id=document_id,
        extension=ext,
        data=data,
        request_id=request_id,
    )

    # Upsert original artifact row (pdf-specific identifier for annual reports).
    identifier = ArtifactIdentifier.ORIGINAL_PDF.value if ext == "pdf" else ArtifactIdentifier.ORIGINAL.value
    # Replace any previous original-* row for this document.
    for existing in (
        db.execute(
            select(Artifact).where(
                Artifact.document_id == document.id,
                Artifact.identifier.in_([ArtifactIdentifier.ORIGINAL_PDF.value, ArtifactIdentifier.ORIGINAL.value]),
            )
        )
        .scalars()
        .all()
    ):
        db.delete(existing)
    db.flush()
    db.add(
        Artifact(
            document_id=document.id,
            identifier=identifier,
            storage_path=rel,
            mime_type=storage.guess_mime(filename or document.original_filename),
            size_bytes=size,
        )
    )

    document.mime_type = storage.guess_mime(filename or document.original_filename)
    document.original_filename = filename or document.original_filename
    document.updated_at = datetime.now(timezone.utc)
    if document.status == DocumentStatus.FAILED.value:
        document.status = DocumentStatus.PENDING.value
        document.error_message = None
    db.commit()
    db.refresh(document)
    return document


def resolve_original_file(
    db: Session,
    *,
    user: User,
    project_id: UUID,
    document_id: UUID,
    settings: Settings,
) -> tuple[Document, Artifact, Path]:
    """Return the on-disk original upload for inline preview / download."""
    document = get_project_document(db, project_id=project_id, document_id=document_id, user_id=user.id)
    artifact = db.execute(
        select(Artifact).where(
            Artifact.document_id == document.id,
            Artifact.identifier.in_([ArtifactIdentifier.ORIGINAL_PDF.value, ArtifactIdentifier.ORIGINAL.value]),
        )
    ).scalar_one_or_none()
    if artifact is None:
        raise NotFoundError("Original file not found")

    storage = LocalStorageService(settings)
    try:
        path = safe_join(storage.root, *Path(artifact.storage_path).parts)
    except ValueError as exc:
        raise NotFoundError("Original file not found") from exc
    if not path.is_file():
        raise NotFoundError("Original file not found")
    return document, artifact, path


def trigger_processing(
    db: Session,
    *,
    user: User,
    project_id: UUID,
    document_id: UUID,
) -> Document:
    document = get_project_document(db, project_id=project_id, document_id=document_id, user_id=user.id)
    previous_status = document.status

    # Require the original file on disk before claiming the document.
    original = db.execute(
        select(Artifact).where(
            Artifact.document_id == document.id,
            Artifact.identifier.in_([ArtifactIdentifier.ORIGINAL_PDF.value, ArtifactIdentifier.ORIGINAL.value]),
        )
    ).scalar_one_or_none()
    if original is None:
        raise ConflictError("Upload the document content before processing")

    claimed = claim_document_for_processing(db, document.id)
    if claimed is None:
        raise ConflictError(f"Document is not claimable from status '{previous_status}'")
    return claimed


def fail_stale_processing(db: Session, *, minutes: int) -> int:
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    result = (
        db.execute(
            select(Document).where(
                Document.status.in_(
                    [
                        DocumentStatus.PROCESSING.value,
                        DocumentStatus.EXTRACTED.value,
                        DocumentStatus.INGESTING.value,
                    ]
                ),
                Document.updated_at < cutoff,
            )
        )
        .scalars()
        .all()
    )
    count = 0
    for doc in result:
        doc.status = DocumentStatus.FAILED.value
        doc.error_message = f"Processing timed out after {minutes} minutes"
        count += 1
    if count:
        db.commit()
    return count


def delete_document(
    db: Session,
    *,
    user: User,
    project_id: UUID,
    document_id: UUID,
    settings: Settings,
) -> None:
    document = get_project_document(db, project_id=project_id, document_id=document_id, user_id=user.id)
    storage = LocalStorageService(settings)
    storage.delete_document_dir(user.id, project_id, document_id)
    db.delete(document)
    db.commit()


def build_project_state(db: Session, *, user_id: UUID, project_ids: list[UUID]) -> list[dict]:
    """Project state for the given ids. Unowned ids are dropped."""
    if not project_ids:
        return []
    if len(project_ids) > 20:
        raise AppError("A maximum of 20 project ids is allowed", status_code=422)

    projects = (
        db.execute(select(Project).where(Project.user_id == user_id, Project.id.in_(project_ids))).scalars().all()
    )
    by_id = {p.id: p for p in projects}

    states = []
    for pid in project_ids:
        project = by_id.get(pid)
        if project is None:
            continue
        docs = (
            db.execute(
                select(Document)
                .options(selectinload(Document.artifacts))
                .where(
                    Document.project_id == project.id,
                    Document.status.in_([s.value for s in DocumentStatus.usable()]),
                )
                .order_by(Document.created_at.desc())
            )
            .scalars()
            .all()
        )
        states.append(
            {
                "id": str(project.id),
                "userId": str(project.user_id),
                "name": project.name,
                "threadId": str(project.thread_id),
                "pendingSourcesCount": pending_sources_count(db, project.id),
                "sources": [document_to_resource(d) for d in docs],
            }
        )
    return states


def document_to_resource(document: Document) -> dict:
    artifacts = [
        {
            "id": str(artifact.id),
            "identifier": artifact.identifier,
            "storagePath": artifact.storage_path,
            "mimeType": artifact.mime_type,
        }
        for artifact in (document.artifacts or [])
    ]
    extracted_text_path = next(
        (item["storagePath"] for item in artifacts if item["identifier"] == "extracted-text"),
        None,
    )
    return {
        "id": str(document.id),
        "projectId": str(document.project_id),
        "title": document.title,
        "originalFilename": document.original_filename,
        "mimeType": document.mime_type,
        "processStatus": document.status,
        "companyName": document.company_name,
        "reportYear": document.report_year,
        "docType": document.doc_type,
        "summary": document.summary,
        "keyDatapoints": getattr(document, "key_datapoints", None),
        "extractedTextPath": extracted_text_path,
        "originalFileUrl": f"/api/v1/projects/{document.project_id}/documents/{document.id}/original",
        "artifacts": artifacts,
        "error": (
            {"code": "PROCESSING_FAILED", "message": document.error_message}
            if document.status == DocumentStatus.FAILED.value and document.error_message
            else None
        ),
        "createdAt": document.created_at.isoformat() if document.created_at else None,
        "updatedAt": document.updated_at.isoformat() if document.updated_at else None,
        "createdBy": {"userId": str(document.user_id)},
    }


# re-export for workflow rollback helpers
__all__ = [
    "create_user",
    "regenerate_user_token",
    "create_project",
    "prepare_document",
    "save_document_content",
    "trigger_processing",
    "fail_stale_processing",
    "delete_document",
    "build_project_state",
    "document_to_resource",
    "resolve_original_file",
    "restore_document_status",
]
