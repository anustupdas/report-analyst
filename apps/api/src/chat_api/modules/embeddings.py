"""Active embedding provider + table mapping (HF local vs OpenAI)."""

from __future__ import annotations

from typing import Any, Literal

from chat_api.modules.models import (
    DEFAULT_HF_EMBEDDING_MODEL,
    DEFAULT_OPENAI_EMBEDDING_MODEL,
    HF_EMBEDDING_DIMENSION,
    OPENAI_EMBEDDING_DIMENSION,
    DocumentChunkEmbeddingHf,
    DocumentChunkEmbeddingOpenai,
)

EmbeddingProvider = Literal["huggingface", "openai"]

PROVIDER_HUGGINGFACE: EmbeddingProvider = "huggingface"
PROVIDER_OPENAI: EmbeddingProvider = "openai"

_PROVIDER_DEFAULTS: dict[str, tuple[int, str, Any]] = {
    PROVIDER_HUGGINGFACE: (HF_EMBEDDING_DIMENSION, DEFAULT_HF_EMBEDDING_MODEL, DocumentChunkEmbeddingHf),
    PROVIDER_OPENAI: (OPENAI_EMBEDDING_DIMENSION, DEFAULT_OPENAI_EMBEDDING_MODEL, DocumentChunkEmbeddingOpenai),
}


def normalize_provider(raw: str | None) -> EmbeddingProvider:
    """Map config aliases (`hf`, `oai`, `local`) onto `huggingface` or `openai`."""
    value = (raw or PROVIDER_OPENAI).strip().lower().replace("-", "").replace("_", "")
    if value in {"openai", "oai"}:
        return PROVIDER_OPENAI
    if value in {"huggingface", "hf", "local", "gte"}:
        return PROVIDER_HUGGINGFACE
    raise ValueError(f"Unknown embedding.provider {raw!r}; use huggingface or openai")


def provider_dimension(provider: EmbeddingProvider) -> int:
    """Vector width the API expects for this provider's table."""
    return _PROVIDER_DEFAULTS[provider][0]


def provider_default_model(provider: EmbeddingProvider) -> str:
    """Model name used when YAML omits `embedding.modelName`."""
    return _PROVIDER_DEFAULTS[provider][1]


def provider_embedding_model(provider: EmbeddingProvider) -> Any:
    """SQLAlchemy model for the active vector table."""
    return _PROVIDER_DEFAULTS[provider][2]


def provider_table_name(provider: EmbeddingProvider) -> str:
    """Postgres table that stores vectors for this provider."""
    return str(provider_embedding_model(provider).__tablename__)
