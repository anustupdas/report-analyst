"""OpenAI embeddings via the official /v1/embeddings HTTP API."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from embedding_service.exceptions import EmbeddingServiceError

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
DEFAULT_OPENAI_DIMENSION = 1536
OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"


class OpenAIEmbeddingError(EmbeddingServiceError):
    """OpenAI rejected the call or returned a payload this service cannot use."""

    status_code = 502
    detail = "OpenAI embedding request failed."


class OpenAINotConfiguredError(EmbeddingServiceError):
    """`OPENAI_API_KEY` is missing, so the OpenAI route cannot run."""

    status_code = 503
    detail = "OPENAI_API_KEY is not configured for the embedding service."


def openai_api_key() -> str:
    """Key from the process environment. Empty when the route should answer 503."""
    return (os.getenv("OPENAI_API_KEY") or "").strip()


def embed_texts_openai(
    texts: list[str],
    *,
    model_name: str = DEFAULT_OPENAI_MODEL,
    timeout: float = 120.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return ([{text, embedding}, ...], meta) matching the HF EmbeddingsResponse shape."""
    cleaned = [str(text).strip() for text in texts if text and str(text).strip()]
    if not cleaned:
        raise EmbeddingServiceError(detail="texts must contain at least one non-empty string")

    api_key = openai_api_key()
    if not api_key:
        raise OpenAINotConfiguredError()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"input": cleaned, "model": model_name}
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.post(OPENAI_EMBEDDINGS_URL, headers=headers, json=payload)
    except httpx.RequestError as exc:
        logger.exception("OpenAI embeddings request failed")
        raise OpenAIEmbeddingError(detail=f"OpenAI unavailable: {exc}") from exc

    if response.status_code >= 400:
        detail = response.text[:500]
        try:
            body = response.json()
            if isinstance(body, dict):
                err = body.get("error") or body
                detail = str(err.get("message") or err)[:500]
        except Exception:
            # Keep the raw body when the error payload is not JSON.
            pass
        raise OpenAIEmbeddingError(detail=detail)

    body = response.json()
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or len(data) != len(cleaned):
        raise OpenAIEmbeddingError(detail="OpenAI returned an unexpected embeddings payload")

    # OpenAI may return rows out of order; sort by index.
    ordered = sorted(data, key=lambda row: int(row.get("index", 0)) if isinstance(row, dict) else 0)
    items: list[dict[str, Any]] = []
    dimension = 0
    for text, row in zip(cleaned, ordered):
        vector = row.get("embedding") if isinstance(row, dict) else None
        if not isinstance(vector, list) or not vector:
            raise OpenAIEmbeddingError(detail="OpenAI embedding row missing vector")
        dimension = len(vector)
        items.append({"text": text, "embedding": [float(v) for v in vector]})

    meta = {
        "model_name": model_name,
        "model_version": 1,
        "hf_model_id": f"openai:{model_name}",
        "dimension": dimension or DEFAULT_OPENAI_DIMENSION,
        "input_type": "openai",
    }
    return items, meta
