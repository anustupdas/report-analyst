from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from chat_api.config import Settings
from chat_api.errors import AppError, NotFoundError
from chat_api.http.v1.schemas import ChunkUpsertItem, SearchChunksRequest
from chat_api.modules.models import DEFAULT_EMBEDDING_MODEL, Document, DocumentChunk, Project
from chat_api.modules.policies import get_owned_project, get_project_document
from chat_api.services.embedding import EmbeddingClient
from chat_api.services.vectors import ChunkRecord, SearchHit, VectorStore


def _chunk_resource(chunk: DocumentChunk, *, include_embedding: bool = False) -> dict:
    payload = {
        "id": str(chunk.id),
        "documentId": str(chunk.document_id),
        "projectId": str(chunk.project_id),
        "chunkIndex": chunk.chunk_index,
        "content": chunk.content,
        "section": chunk.section,
        "pageStart": chunk.page_start,
        "pageEnd": chunk.page_end,
        "tokenCount": chunk.token_count,
        "modelName": chunk.model_name,
        "createdAt": chunk.created_at.isoformat() if chunk.created_at else None,
    }
    if include_embedding:
        # Vectors are provider-table only; list APIs never inline them.
        payload["embedding"] = None
    return payload


def _hit_resource(hit: SearchHit) -> dict:
    return {
        "id": str(hit.id),
        "documentId": str(hit.document_id),
        "projectId": str(hit.project_id),
        "chunkIndex": hit.chunk_index,
        "content": hit.content,
        "score": hit.score,
        "section": hit.section,
        "pageStart": hit.page_start,
        "pageEnd": hit.page_end,
        "tokenCount": hit.token_count,
        "modelName": hit.model_name,
        "title": hit.title,
        "originalFilename": hit.original_filename,
    }


def upsert_project_chunks(
    db: Session,
    *,
    user_id: UUID,
    project_id: UUID,
    document_id: UUID,
    items: list[ChunkUpsertItem],
    settings: Settings,
    replace: bool = False,
    request_id: str | None = None,
    session_id: str | None = None,
) -> dict:
    get_owned_project(db, project_id=project_id, user_id=user_id)
    document = get_project_document(db, project_id=project_id, document_id=document_id, user_id=user_id)
    return _upsert_chunks_for_document(
        db,
        document=document,
        items=items,
        settings=settings,
        replace=replace,
        request_id=request_id,
        session_id=session_id,
    )


def _upsert_chunks_for_document(
    db: Session,
    *,
    document: Document,
    items: list[ChunkUpsertItem],
    settings: Settings,
    replace: bool,
    request_id: str | None,
    session_id: str | None,
) -> dict:
    embedder = EmbeddingClient(settings)
    to_embed: list[int] = []
    embeddings: list[list[float] | None] = []
    for index, item in enumerate(items):
        if item.embedding is None:
            to_embed.append(index)
            embeddings.append(None)
            continue
        if len(item.embedding) != settings.embedding_dimension:
            raise AppError(
                f"Embedding at index {index} must have dimension {settings.embedding_dimension}",
                status_code=422,
                code="embedding_dimension_mismatch",
            )
        embeddings.append(item.embedding)

    if to_embed:
        vectors = embedder.embed_texts(
            [items[i].content for i in to_embed],
            input_type="passage",
            request_id=request_id,
            session_id=session_id,
        )
        for source_index, vector in zip(to_embed, vectors):
            embeddings[source_index] = vector

    records: list[ChunkRecord] = []
    for index, item in enumerate(items):
        vector = embeddings[index]
        if vector is None:
            raise AppError("Missing embedding after encode", status_code=500, code="embedding_missing")
        records.append(
            ChunkRecord(
                document_id=document.id,
                project_id=document.project_id,
                user_id=document.user_id,
                chunk_index=item.chunk_index if item.chunk_index is not None else index,
                content=item.content,
                embedding=vector,
                page_start=item.page_start,
                page_end=item.page_end,
                token_count=max(1, len(item.content.split())),
                model_name=settings.embedding_model_name or DEFAULT_EMBEDDING_MODEL,
            )
        )

    store = VectorStore(provider=settings.embedding_provider)
    if replace:
        count = store.replace_document_chunks(db, document_id=document.id, chunks=records, request_id=request_id)
    else:
        count = store.upsert_chunks(db, records, request_id=request_id)
    return {"upserted": count, "documentId": str(document.id)}


def delete_project_chunks(
    db: Session,
    *,
    user_id: UUID,
    project_id: UUID,
    document_id: UUID,
    request_id: str | None = None,
) -> dict:
    get_owned_project(db, project_id=project_id, user_id=user_id)
    document = get_project_document(db, project_id=project_id, document_id=document_id, user_id=user_id)
    deleted = VectorStore().delete_document_chunks(db, document_id=document.id, request_id=request_id)
    return {"deleted": deleted, "documentId": str(document.id)}


def list_project_chunks(
    db: Session,
    *,
    user_id: UUID,
    project_id: UUID,
    document_id: UUID,
) -> list[dict]:
    get_owned_project(db, project_id=project_id, user_id=user_id)
    document = get_project_document(db, project_id=project_id, document_id=document_id, user_id=user_id)
    chunks = VectorStore().list_document_chunks(db, document_id=document.id)
    return [_chunk_resource(chunk) for chunk in chunks]


def search_project_chunks(
    db: Session,
    *,
    user_id: UUID,
    project_id: UUID,
    body: SearchChunksRequest,
    settings: Settings,
    request_id: str | None = None,
    session_id: str | None = None,
) -> dict:
    get_owned_project(db, project_id=project_id, user_id=user_id)
    if body.document_id is not None:
        get_project_document(db, project_id=project_id, document_id=body.document_id, user_id=user_id)

    limit = body.limit or settings.vector_search_default_limit
    limit = min(max(1, limit), settings.vector_search_max_limit)

    if body.embedding is not None:
        if len(body.embedding) != settings.embedding_dimension:
            raise AppError(
                f"Query embedding must have dimension {settings.embedding_dimension}",
                status_code=422,
                code="embedding_dimension_mismatch",
            )
        vector = body.embedding
    else:
        query = (body.query or "").strip()
        vector = EmbeddingClient(settings).embed_text(
            query,
            input_type="query",
            request_id=request_id,
            session_id=session_id,
        )

    hits = VectorStore(provider=settings.embedding_provider).search(
        db,
        user_id=user_id,
        project_id=project_id,
        embedding=vector,
        model_name=settings.embedding_model_name,
        limit=limit,
        document_id=body.document_id,
        request_id=request_id,
    )
    return {"matches": [_hit_resource(hit) for hit in hits], "limit": limit}


def search_project_chunks_as_owner(
    db: Session,
    *,
    project_id: UUID,
    body: SearchChunksRequest,
    settings: Settings,
    request_id: str | None = None,
    session_id: str | None = None,
) -> dict:
    project = db.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return search_project_chunks(
        db,
        user_id=project.user_id,
        project_id=project_id,
        body=body,
        settings=settings,
        request_id=request_id,
        session_id=session_id,
    )


def merge_key_datapoints(existing: dict | None, incoming: dict) -> dict:
    """Keep prior FTE when a later pass only finds goals (and vice versa)."""
    base = dict(existing or {})
    merged = dict(base)
    incoming = dict(incoming or {})

    fte = incoming.get("fte")
    if isinstance(fte, dict) and fte.get("value") is not None:
        merged["fte"] = fte
    elif "fte" not in merged:
        merged["fte"] = fte if isinstance(fte, dict) else None

    goals = incoming.get("sustainability_goals")
    if goals is None:
        goals = incoming.get("sustainabilityGoals")
    if isinstance(goals, list) and goals:
        merged["sustainability_goals"] = goals
    elif "sustainability_goals" not in merged:
        merged["sustainability_goals"] = goals if isinstance(goals, list) else []

    note = incoming.get("status_note") or incoming.get("statusNote")
    if isinstance(note, str) and note.strip():
        merged["status_note"] = note.strip()

    fte_ok = isinstance(merged.get("fte"), dict) and merged["fte"].get("value") is not None
    goals_ok = isinstance(merged.get("sustainability_goals"), list) and bool(merged["sustainability_goals"])
    if fte_ok or goals_ok:
        merged.pop("status_note", None)

    return merged


def update_document_datapoints(
    db: Session,
    *,
    user_id: UUID,
    project_id: UUID,
    document_id: UUID,
    key_datapoints: dict,
) -> dict:
    get_owned_project(db, project_id=project_id, user_id=user_id)
    document = get_project_document(db, project_id=project_id, document_id=document_id, user_id=user_id)
    document.key_datapoints = merge_key_datapoints(
        getattr(document, "key_datapoints", None),
        key_datapoints,
    )
    db.commit()
    db.refresh(document)
    return {
        "id": str(document.id),
        "projectId": str(document.project_id),
        "processStatus": document.status,
        "keyDatapoints": document.key_datapoints,
        "updatedAt": document.updated_at.isoformat() if document.updated_at else None,
    }
