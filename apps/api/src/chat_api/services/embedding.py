from __future__ import annotations

import logging
from typing import Any, Literal

import httpx

from chat_api.config import Settings
from chat_api.errors import AppError
from chat_api.modules.embeddings import PROVIDER_OPENAI, normalize_provider
from chat_api.tracing import structured_log

logger = logging.getLogger(__name__)

InputType = Literal["passage", "query"]


class EmbeddingClient:
    """HTTP client for apps/embedding-service (HF local or OpenAI route)."""

    def __init__(self, settings: Settings) -> None:
        self.base_url = (settings.embedding_service_url or "").rstrip("/")
        self.timeout = settings.embedding_timeout_seconds
        self.model_name = settings.embedding_model_name
        self.dimension = settings.embedding_dimension
        self.provider = normalize_provider(settings.embedding_provider)

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def embed_texts(
        self,
        texts: list[str],
        *,
        input_type: InputType = "passage",
        request_id: str | None = None,
        session_id: str | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        if not self.configured:
            raise AppError(
                "Embedding service is not configured",
                status_code=503,
                code="embedding_not_configured",
            )

        batch_size = 32
        if len(texts) <= batch_size:
            return self._embed_batch(
                texts,
                input_type=input_type,
                request_id=request_id,
                session_id=session_id,
            )

        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            vectors.extend(
                self._embed_batch(
                    texts[start : start + batch_size],
                    input_type=input_type,
                    request_id=request_id,
                    session_id=session_id,
                )
            )
        return vectors

    def _embed_path(self) -> str:
        if self.provider == PROVIDER_OPENAI:
            return f"{self.base_url}/api/v1/embeddings/openai"
        return f"{self.base_url}/api/v1/embed_texts"

    def _embed_payload(self, texts: list[str], *, input_type: InputType) -> dict[str, Any]:
        if self.provider == PROVIDER_OPENAI:
            return {"texts": texts, "model_name": self.model_name}
        return {
            "texts": texts,
            "model_name": self.model_name,
            "input_type": input_type,
        }

    def _embed_batch(
        self,
        texts: list[str],
        *,
        input_type: InputType,
        request_id: str | None,
        session_id: str | None,
    ) -> list[list[float]]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if request_id:
            headers["X-Request-ID"] = request_id
        if session_id:
            headers["X-Session-ID"] = session_id

        url = self._embed_path()
        payload = self._embed_payload(texts, input_type=input_type)
        structured_log(
            logger,
            "embedding.request",
            request_id=request_id,
            n=len(texts),
            input_type=input_type,
            model=self.model_name,
            provider=self.provider,
        )

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload, headers=headers)
        except httpx.RequestError as exc:
            raise AppError(
                "Embedding service unavailable",
                status_code=503,
                code="upstream_unavailable",
            ) from exc

        if response.status_code >= 400:
            raise AppError(
                f"Embedding failed: {_safe_detail(response)}",
                status_code=502,
                code="embedding_failed",
            )

        body = response.json()
        items = body.get("embeddings") if isinstance(body, dict) else None
        if not isinstance(items, list) or len(items) != len(texts):
            raise AppError(
                "Embedding service returned an unexpected payload",
                status_code=502,
                code="embedding_invalid_response",
            )

        vectors: list[list[float]] = []
        for item in items:
            # HF: {text, embedding}; OpenAI endpoint uses the same shape.
            vector = item.get("embedding") if isinstance(item, dict) else item
            if not isinstance(vector, list) or len(vector) != self.dimension:
                raise AppError(
                    f"Embedding dimension mismatch (expected {self.dimension})",
                    status_code=502,
                    code="embedding_dimension_mismatch",
                )
            vectors.append([float(v) for v in vector])

        structured_log(
            logger,
            "embedding.success",
            request_id=request_id,
            n=len(vectors),
            dimension=self.dimension,
            provider=self.provider,
        )
        return vectors

    def embed_text(
        self,
        text: str,
        *,
        input_type: InputType = "query",
        request_id: str | None = None,
        session_id: str | None = None,
    ) -> list[float]:
        vectors = self.embed_texts(
            [text],
            input_type=input_type,
            request_id=request_id,
            session_id=session_id,
        )
        return vectors[0]


def _safe_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            return str(payload.get("detail") or payload.get("message") or response.status_code)
    except Exception:
        pass
    return f"HTTP {response.status_code}"
