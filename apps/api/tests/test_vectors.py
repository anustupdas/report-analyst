from uuid import uuid4

import pytest
from pydantic import ValidationError

from chat_api.http.v1.schemas import SearchChunksRequest, UpsertChunksRequest
from chat_api.modules.documents.enums import DocumentStatus
from chat_api.services.vectors import VectorStore


def test_search_requires_query_or_embedding():
    with pytest.raises(ValidationError):
        SearchChunksRequest.model_validate({})
    with pytest.raises(ValidationError):
        SearchChunksRequest.model_validate({"query": "hello", "embedding": [0.1, 0.2]})
    body = SearchChunksRequest.model_validate({"query": "What is net profit?"})
    assert body.query == "What is net profit?"
    assert body.embedding is None


class _Rows:
    def all(self):
        return []


class _CapturingSession:
    def __init__(self):
        self.statement = None

    def execute(self, statement):
        self.statement = statement
        return _Rows()


def _from_names(froms) -> set[str]:
    names: set[str] = set()
    stack = list(froms)
    while stack:
        node = stack.pop()
        name = getattr(node, "name", None)
        if isinstance(name, str):
            names.add(name)
        stack.extend(
            child for child in (getattr(node, "left", None), getattr(node, "right", None)) if child is not None
        )
    return names


def _in_values(clause, column_name: str) -> set[str] | None:
    """Return the bound values of ``column IN (...)``, or None if that predicate is absent."""
    stack = [clause]
    while stack:
        node = stack.pop()
        if node is None:
            continue
        left = getattr(node, "left", None)
        right = getattr(node, "right", None)
        if getattr(left, "key", None) == column_name and getattr(right, "expanding", False):
            return set(right.effective_value)
        clauses = getattr(node, "clauses", None)
        if clauses is not None:
            stack.extend(clauses)
        stack.extend(child for child in (left, right) if child is not None)
    return None


def test_search_only_returns_chunks_from_usable_documents():
    session = _CapturingSession()
    VectorStore(provider="huggingface").search(
        session,
        user_id=uuid4(),
        project_id=uuid4(),
        embedding=[0.1, 0.2],
        model_name="gte-multilingual-base",
        document_id=uuid4(),
    )

    assert session.statement is not None
    froms = _from_names(session.statement.get_final_froms())
    assert "documents" in froms
    assert "document_chunks" in froms
    assert "document_chunk_embeddings_hf" in froms
    assert _in_values(session.statement.whereclause, "status") == {status.value for status in DocumentStatus.usable()}


def test_openai_search_joins_openai_embedding_table():
    session = _CapturingSession()
    VectorStore(provider="openai").search(
        session,
        user_id=uuid4(),
        project_id=uuid4(),
        embedding=[0.1] * 8,
        model_name="text-embedding-3-small",
    )
    froms = _from_names(session.statement.get_final_froms())
    assert "document_chunk_embeddings_openai" in froms
    assert "document_chunk_embeddings_hf" not in froms


def test_upsert_chunks_accepts_camel_document_id():
    from uuid import uuid4

    document_id = uuid4()
    body = UpsertChunksRequest.model_validate(
        {
            "documentId": str(document_id),
            "chunks": [{"content": "hello", "chunkIndex": 0}],
            "replace": True,
        }
    )
    assert body.document_id == document_id
    assert body.chunks[0].chunk_index == 0
    assert body.replace is True
