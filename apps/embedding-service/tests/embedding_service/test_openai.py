from __future__ import annotations

import httpx
import pytest

from embedding_service.exceptions import EmbeddingServiceError
from embedding_service.openai_embed import OpenAIEmbeddingError, OpenAINotConfiguredError, embed_texts_openai


def test_openai_embed_requires_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(OpenAINotConfiguredError):
        embed_texts_openai(["hello"])


def test_openai_embed_parses_response(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "data": [
                    {"index": 1, "embedding": [0.2, 0.3]},
                    {"index": 0, "embedding": [0.0, 0.1]},
                ]
            }

        @property
        def text(self):
            return ""

    def fake_post(self, url, headers=None, json=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    items, meta = embed_texts_openai(["a", "b"], model_name="text-embedding-3-small")
    assert captured["url"].endswith("/v1/embeddings")
    assert captured["json"]["model"] == "text-embedding-3-small"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert items[0]["embedding"] == [0.0, 0.1]
    assert items[1]["embedding"] == [0.2, 0.3]
    assert meta["dimension"] == 2
    assert meta["input_type"] == "openai"


def test_openai_embed_rejects_empty_texts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with pytest.raises(EmbeddingServiceError, match="non-empty"):
        embed_texts_openai(["  ", ""])


def test_openai_embed_surfaces_api_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    class FakeResponse:
        status_code = 400
        text = "raw"

        def json(self):
            return {"error": {"message": "model not found"}}

    monkeypatch.setattr(httpx.Client, "post", lambda self, url, headers=None, json=None: FakeResponse())
    with pytest.raises(OpenAIEmbeddingError, match="model not found"):
        embed_texts_openai(["hello"])


def test_openai_embed_wraps_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fail(self, url, headers=None, json=None):
        raise httpx.ConnectError("dns")

    monkeypatch.setattr(httpx.Client, "post", fail)
    with pytest.raises(OpenAIEmbeddingError, match="OpenAI unavailable"):
        embed_texts_openai(["hello"])


def test_openai_embed_rejects_short_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"data": [{"index": 0, "embedding": [0.1]}]}

    monkeypatch.setattr(httpx.Client, "post", lambda self, url, headers=None, json=None: FakeResponse())
    with pytest.raises(OpenAIEmbeddingError, match="unexpected"):
        embed_texts_openai(["a", "b"])
