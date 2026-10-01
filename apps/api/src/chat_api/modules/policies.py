from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from chat_api.errors import ForbiddenError, NotFoundError
from chat_api.modules.documents.enums import DocumentStatus
from chat_api.modules.models import Document, Project, User

logger = logging.getLogger(__name__)


def get_user_by_id(db: Session, user_id: UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    return user


def get_owned_project(db: Session, *, project_id: UUID, user_id: UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    if project.user_id != user_id:
        raise ForbiddenError("You do not have access to this project")
    return project


def get_project_document(db: Session, *, project_id: UUID, document_id: UUID, user_id: UUID | None = None) -> Document:
    document = db.execute(
        select(Document).where(Document.id == document_id, Document.project_id == project_id)
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError("Document not found")
    if user_id is not None and document.user_id != user_id:
        raise ForbiddenError("You do not have access to this document")
    return document


def pending_sources_count(db: Session, project_id: UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.project_id == project_id,
                Document.status.in_([s.value for s in DocumentStatus.in_flight()]),
            )
        )
        or 0
    )


def claim_document_for_processing(db: Session, document_id: UUID) -> Document | None:
    """Atomic pending|failed → processing. Returns None if not claimable (race)."""
    result = db.execute(
        update(Document)
        .where(
            Document.id == document_id,
            Document.status.in_([DocumentStatus.PENDING.value, DocumentStatus.FAILED.value]),
        )
        .values(status=DocumentStatus.PROCESSING.value, error_message=None)
        .returning(Document.id)
    )
    row = result.first()
    db.commit()
    if row is None:
        return None
    return db.get(Document, document_id)


def restore_document_status(db: Session, document_id: UUID, status: str, error: str | None = None) -> None:
    db.execute(update(Document).where(Document.id == document_id).values(status=status, error_message=error))
    db.commit()
