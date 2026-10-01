import httpx
import pytest
from pydantic import ValidationError

from chat_api.config import Settings
from chat_api.errors import AppError
from chat_api.modules.models import OPENAI_EMBEDDING_DIMENSION, DocumentChunkEmbeddingOpenai
from chat_api.services.embedding import EmbeddingClient


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _settings() -> Settings:
    return Settings(
        embedding_service_url="http://embed.test",
        embedding_provider="huggingface",
        embedding_dimension=768,
        embedding_model_name="gte-multilingual-base",
    )


def _patch_post(monkeypatch, vector, captured=None):
    def fake_post(self, url, json=None, headers=None):
        if captured is not None:
            captured["url"] = url
            captured["json"] = json
        return _FakeResponse(
            {
                "embeddings": [{"text": "hello", "embedding": vector}],
                "meta": {"dimension": len(vector)},
            }
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)


def test_default_dimension_matches_orm_column():
    """The omitted-config dimension matches the OpenAI vector column."""
    column_dim = DocumentChunkEmbeddingOpenai.__table__.c.embedding.type.dim
    assert Settings.model_fields["embedding_dimension"].default == column_dim == OPENAI_EMBEDDING_DIMENSION


def test_embedding_client_parses_batch(monkeypatch):
    vector = [0.0] * 768
    vector[0] = 0.5
    captured = {}
    _patch_post(monkeypatch, vector, captured)

    result = EmbeddingClient(_settings()).embed_texts(["hello"], input_type="passage")
    assert captured["url"] == "http://embed.test/api/v1/embed_texts"
    assert captured["json"]["input_type"] == "passage"
    assert captured["json"]["model_name"] == "gte-multilingual-base"
    assert result[0][0] == 0.5
    assert len(result[0]) == 768


def test_embedding_client_openai_route(monkeypatch):
    vector = [0.1] * 1536
    captured = {}
    _patch_post(monkeypatch, vector, captured)
    settings = Settings(
        embedding_service_url="http://embed.test",
        embedding_provider="openai",
        embedding_model_name="text-embedding-3-small",
        embedding_dimension=1536,
    )
    result = EmbeddingClient(settings).embed_texts(["hello"], input_type="query")
    assert captured["url"] == "http://embed.test/api/v1/embeddings/openai"
    assert "input_type" not in captured["json"]
    assert captured["json"]["model_name"] == "text-embedding-3-small"
    assert len(result[0]) == 1536


def test_settings_reject_dimension_that_does_not_match_provider():
    with pytest.raises(ValidationError, match="does not match"):
        Settings(
            embedding_provider="openai",
            embedding_dimension=768,
            embedding_model_name="text-embedding-3-small",
        )


def test_embedding_client_rejects_wrong_dimension(monkeypatch):
    _patch_post(monkeypatch, [0.0] * 1024)
    with pytest.raises(AppError) as excinfo:
        EmbeddingClient(_settings()).embed_texts(["hello"], input_type="passage")
    assert excinfo.value.code == "embedding_dimension_mismatch"
