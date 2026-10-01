from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from chat_api.deps import CurrentUser, DbDep, SettingsDep, request_ids, require_agent_secret
from chat_api.http.v1.schemas import (
    SearchChunksRequest,
    UpsertChunksRequest,
)
from chat_api.modules import vectors as vector_actions

router = APIRouter()


@router.post(
    "/projects/{project_id}/chunks",
    status_code=status.HTTP_201_CREATED,
    tags=["Vectors"],
    summary="Add chunk embeddings",
)
def add_chunks(
    project_id: UUID,
    body: UpsertChunksRequest,
    db: DbDep,
    user: CurrentUser,
    settings: SettingsDep,
    request: Request,
) -> dict:
    """Embed missing vectors and insert/upsert rows in `document_chunks`.

    **Auth:** required. Chunks must belong to a document in this project.

    If a chunk has no `embedding`, chat-api calls the embedding service with
    `input_type=passage`. Set `replace: true` to delete existing rows first.
    """
    request_id, session_id = request_ids(request)
    data = vector_actions.upsert_project_chunks(
        db,
        user_id=user.id,
        project_id=project_id,
        document_id=body.document_id,
        items=body.chunks,
        settings=settings,
        replace=body.replace,
        request_id=request_id,
        session_id=session_id,
    )
    return {"data": data}


@router.put(
    "/projects/{project_id}/chunks",
    tags=["Vectors"],
    summary="Update chunk embeddings",
)
def update_chunks(
    project_id: UUID,
    body: UpsertChunksRequest,
    db: DbDep,
    user: CurrentUser,
    settings: SettingsDep,
    request: Request,
) -> dict:
    """Same as add: upsert by `(documentId, chunkIndex)`."""
    request_id, session_id = request_ids(request)
    data = vector_actions.upsert_project_chunks(
        db,
        user_id=user.id,
        project_id=project_id,
        document_id=body.document_id,
        items=body.chunks,
        settings=settings,
        replace=body.replace,
        request_id=request_id,
        session_id=session_id,
    )
    return {"data": data}


@router.delete(
    "/projects/{project_id}/documents/{document_id}/chunks",
    tags=["Vectors"],
    summary="Delete document chunk embeddings",
)
def delete_chunks(
    project_id: UUID,
    document_id: UUID,
    db: DbDep,
    user: CurrentUser,
    request: Request,
) -> dict:
    """Delete all pgvector rows for one document.

    **Auth:** required. Document delete also cascades via FK.
    """
    request_id, _ = request_ids(request)
    data = vector_actions.delete_project_chunks(
        db,
        user_id=user.id,
        project_id=project_id,
        document_id=document_id,
        request_id=request_id,
    )
    return {"data": data}


@router.get(
    "/projects/{project_id}/documents/{document_id}/chunks",
    tags=["Vectors"],
    summary="List document chunks",
)
def list_chunks(
    project_id: UUID,
    document_id: UUID,
    db: DbDep,
    user: CurrentUser,
) -> dict:
    """Return stored chunks without the raw embedding arrays."""
    data = vector_actions.list_project_chunks(
        db,
        user_id=user.id,
        project_id=project_id,
        document_id=document_id,
    )
    return {"data": data}


@router.post(
    "/projects/{project_id}/chunks/search",
    tags=["Vectors"],
    summary="Search similar chunks",
)
def search_chunks(
    project_id: UUID,
    body: SearchChunksRequest,
    db: DbDep,
    user: CurrentUser,
    settings: SettingsDep,
    request: Request,
) -> dict:
    """Embed `query` (`input_type=query`) then cosine-search pgvector for the configured model.

    **Auth:** required. Always filtered by the current user and project.

    Send either `query` (API embeds it) or `embedding` (already the active provider's dimension).
    """
    request_id, session_id = request_ids(request)
    data = vector_actions.search_project_chunks(
        db,
        user_id=user.id,
        project_id=project_id,
        body=body,
        settings=settings,
        request_id=request_id,
        session_id=session_id,
    )
    return {"data": data}


@router.post(
    "/agent/projects/{project_id}/chunks/search",
    tags=["Agent"],
    summary="Search chunks for LangGraph / agents",
    dependencies=[Depends(require_agent_secret)],
)
def agent_search_chunks(
    project_id: UUID,
    body: SearchChunksRequest,
    db: DbDep,
    settings: SettingsDep,
    request: Request,
) -> dict:
    """Same search as the user route, authenticated with `X-Agent-Secret`."""
    request_id, session_id = request_ids(request)
    data = vector_actions.search_project_chunks_as_owner(
        db,
        project_id=project_id,
        body=body,
        settings=settings,
        request_id=request_id,
        session_id=session_id,
    )
    return {"data": data}
