from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from chat_api.modules.documents.enums import DocumentStatus
from chat_api.modules.embeddings import (
    PROVIDER_OPENAI,
    EmbeddingProvider,
    normalize_provider,
    provider_embedding_model,
)
from chat_api.modules.models import DEFAULT_EMBEDDING_MODEL, Document, DocumentChunk
from chat_api.tracing import structured_log

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChunkRecord:
    document_id: UUID
    project_id: UUID
    user_id: UUID
    chunk_index: int
    content: str
    embedding: list[float]
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    token_count: int | None = None
    model_name: str = DEFAULT_EMBEDDING_MODEL


@dataclass(frozen=True)
class SearchHit:
    id: UUID
    document_id: UUID
    project_id: UUID
    chunk_index: int
    content: str
    score: float
    section: str | None
    page_start: int | None
    page_end: int | None
    token_count: int | None
    model_name: str
    title: str | None = None
    original_filename: str | None = None


class VectorStore:
    """Chunk text in document_chunks; vectors in provider-specific embedding tables."""

    def __init__(self, *, provider: str | EmbeddingProvider = PROVIDER_OPENAI) -> None:
        self.provider: EmbeddingProvider = normalize_provider(provider)
        self._Embedding = provider_embedding_model(self.provider)

    def upsert_chunks(
        self,
        db: Session,
        chunks: list[ChunkRecord],
        *,
        request_id: str | None = None,
    ) -> int:
        if not chunks:
            return 0

        text_rows = [
            {
                "document_id": chunk.document_id,
                "project_id": chunk.project_id,
                "user_id": chunk.user_id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "section": chunk.section,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "token_count": chunk.token_count,
                "model_name": chunk.model_name,
            }
            for chunk in chunks
        ]
        text_stmt = insert(DocumentChunk).values(text_rows)
        text_stmt = text_stmt.on_conflict_do_update(
            constraint="document_chunks_document_chunk_unique",
            set_={
                "content": text_stmt.excluded.content,
                "section": text_stmt.excluded.section,
                "page_start": text_stmt.excluded.page_start,
                "page_end": text_stmt.excluded.page_end,
                "token_count": text_stmt.excluded.token_count,
                "model_name": text_stmt.excluded.model_name,
            },
        ).returning(DocumentChunk.id, DocumentChunk.document_id, DocumentChunk.chunk_index)
        upserted = db.execute(text_stmt).all()
        id_by_key = {(row.document_id, row.chunk_index): row.id for row in upserted}

        embedding_rows = []
        for chunk in chunks:
            chunk_id = id_by_key.get((chunk.document_id, chunk.chunk_index))
            if chunk_id is None:
                continue
            embedding_rows.append(
                {
                    "chunk_id": chunk_id,
                    "embedding": chunk.embedding,
                    "model_name": chunk.model_name,
                }
            )
        if embedding_rows:
            emb_stmt = insert(self._Embedding).values(embedding_rows)
            emb_stmt = emb_stmt.on_conflict_do_update(
                index_elements=["chunk_id"],
                set_={
                    "embedding": emb_stmt.excluded.embedding,
                    "model_name": emb_stmt.excluded.model_name,
                },
            )
            db.execute(emb_stmt)

        db.commit()
        structured_log(
            logger,
            "vector.upsert",
            request_id=request_id,
            chunks=len(chunks),
            provider=self.provider,
        )
        return len(chunks)

    def replace_document_chunks(
        self,
        db: Session,
        *,
        document_id: UUID,
        chunks: list[ChunkRecord],
        request_id: str | None = None,
    ) -> int:
        self.delete_document_chunks(db, document_id=document_id, request_id=request_id)
        return self.upsert_chunks(db, chunks, request_id=request_id)

    def delete_document_chunks(
        self,
        db: Session,
        *,
        document_id: UUID,
        request_id: str | None = None,
    ) -> int:
        # Embedding rows cascade from document_chunks.
        result = db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        db.commit()
        deleted = int(result.rowcount or 0)
        structured_log(
            logger,
            "vector.delete",
            request_id=request_id,
            document_id=document_id,
            deleted=deleted,
        )
        return deleted

    def list_document_chunks(self, db: Session, *, document_id: UUID) -> list[DocumentChunk]:
        return (
            db.execute(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
                .order_by(DocumentChunk.chunk_index.asc())
            )
            .scalars()
            .all()
        )

    def search(
        self,
        db: Session,
        *,
        user_id: UUID,
        project_id: UUID,
        embedding: list[float],
        model_name: str,
        limit: int = 8,
        document_id: UUID | None = None,
        request_id: str | None = None,
    ) -> list[SearchHit]:
        Embedding = self._Embedding
        distance = Embedding.embedding.cosine_distance(embedding)
        # Join text ↔ active provider vectors. Usable-document filter mirrors project state.
        stmt = (
            select(
                DocumentChunk,
                Embedding.model_name,
                (1 - distance).label("score"),
                Document.title,
                Document.original_filename,
            )
            .join(Embedding, Embedding.chunk_id == DocumentChunk.id)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                DocumentChunk.user_id == user_id,
                DocumentChunk.project_id == project_id,
                Embedding.model_name == model_name,
                Document.status.in_([status.value for status in DocumentStatus.usable()]),
            )
            .order_by(distance)
            .limit(limit)
        )
        if document_id is not None:
            stmt = stmt.where(DocumentChunk.document_id == document_id)

        rows = db.execute(stmt).all()
        hits = [
            SearchHit(
                id=chunk.id,
                document_id=chunk.document_id,
                project_id=chunk.project_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                score=float(score),
                section=chunk.section,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                token_count=chunk.token_count,
                model_name=emb_model_name,
                title=title,
                original_filename=original_filename,
            )
            for chunk, emb_model_name, score, title, original_filename in rows
        ]
        structured_log(
            logger,
            "vector.search",
            request_id=request_id,
            project_id=project_id,
            hits=len(hits),
            limit=limit,
            provider=self.provider,
        )
        return hits
