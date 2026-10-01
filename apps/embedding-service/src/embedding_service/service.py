from __future__ import annotations

import logging
import threading
from typing import Protocol

from embedding_service.exceptions import (
    EmbeddingComputationError,
    InvalidRequestInputError,
    ModelNotConfiguredError,
    ModelNotReadyError,
)
from embedding_service.utils import ServiceConfig

logger = logging.getLogger(__name__)

__all__ = (
    "EmbeddingEncoder",
    "HuggingFaceEncoder",
    "EmbeddingService",
    "apply_input_prefix",
    "prefetch_enabled_models",
    "prefetch_hub_model",
)


class EmbeddingEncoder(Protocol):
    dimension: int

    def encode(self, texts: list[str]) -> list[list[float]]:
        pass


def apply_input_prefix(text: str, prefix: str) -> str:
    """Prepend the model's configured prefix (e.g. E5 `query: `). Do not double-prefix."""
    stripped = text.strip()
    if not prefix or stripped.lower().startswith(prefix.strip().lower()):
        return stripped
    return prefix + stripped


def prefetch_hub_model(model_id: str) -> str:
    """Download Hub files into the local HF cache. Does not load weights into RAM."""
    import os

    from huggingface_hub import snapshot_download

    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    logger.info("Prefetching Hugging Face files for %s into cache (not loading into memory)", model_id)
    try:
        cache_dir = snapshot_download(repo_id=model_id, local_files_only=True)
        logger.info("Using existing cache for %s at %s", model_id, cache_dir)
        return cache_dir
    except Exception:
        logger.info("Cache miss for %s; downloading from the Hub", model_id)
    cache_dir = snapshot_download(repo_id=model_id)
    logger.info("Cached %s at %s", model_id, cache_dir)
    return cache_dir


def prefetch_enabled_models(config: ServiceConfig) -> None:
    for name in config.enabled_models:
        settings = config.model_settings(name)
        for repo_id in [settings["hf_model_id"], *settings["extra_hub_repos"]]:
            try:
                prefetch_hub_model(repo_id)
            except Exception:
                logger.exception(
                    "Prefetch failed for %s (%s); first request will retry the download",
                    name,
                    repo_id,
                )


class HuggingFaceEncoder:
    """Loads a SentenceTransformer from the local HF cache into RAM."""

    def __init__(
        self,
        model_id: str,
        *,
        normalize: bool,
        trust_remote_code: bool = False,
        max_seq_length: int | None = None,
    ) -> None:
        from sentence_transformers import SentenceTransformer

        logger.info(
            "Loading Hugging Face model %s into memory from cache (trust_remote_code=%s)",
            model_id,
            trust_remote_code,
        )
        self._model = SentenceTransformer(model_id, trust_remote_code=trust_remote_code)
        if max_seq_length:
            self._model.max_seq_length = max_seq_length
        self.normalize = normalize
        self.dimension = int(self._model.get_sentence_embedding_dimension())
        logger.info(
            "Loaded %s dimension=%s max_seq_length=%s into memory",
            model_id,
            self.dimension,
            self._model.max_seq_length,
        )

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(
            texts,
            normalize_embeddings=self.normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [row.astype(float).tolist() for row in vectors]


class EmbeddingService:
    def __init__(self, config: ServiceConfig, encoder: EmbeddingEncoder | None = None) -> None:
        self.config = config
        self._encoders: dict[str, EmbeddingEncoder] = {}
        self._lock = threading.Lock()
        if encoder is not None:
            self._encoders[config.default_model_name] = encoder

    def is_model_ready(self, model_name: str | None = None) -> bool:
        name = model_name or self.config.default_model_name
        return name in self._encoders

    def _get_encoder(self, model_name: str) -> EmbeddingEncoder:
        if model_name not in self.config.enabled_models:
            raise ModelNotConfiguredError(f"Model '{model_name}' is not enabled in config")

        cached = self._encoders.get(model_name)
        if cached is not None:
            return cached

        with self._lock:
            cached = self._encoders.get(model_name)
            if cached is not None:
                return cached
            try:
                settings = self.config.model_settings(model_name)
                encoder = HuggingFaceEncoder(
                    settings["hf_model_id"],
                    normalize=self.config.normalize_embedding,
                    trust_remote_code=settings["trust_remote_code"],
                    max_seq_length=settings["max_seq_length"],
                )
            except Exception as exc:
                logger.exception("Failed to load embedding model %s", model_name)
                raise ModelNotReadyError(f"Failed to load model '{model_name}': {exc}") from exc
            expected = int(settings["dimension"])
            if encoder.dimension != expected:
                raise ModelNotReadyError(f"Model '{model_name}' dimension {encoder.dimension} != configured {expected}")
            self._encoders[model_name] = encoder
            return encoder

    def embed_texts(
        self,
        texts: list[str],
        *,
        model_name: str | None = None,
        input_type: str = "passage",
    ) -> tuple[list[dict], dict]:
        name = model_name or self.config.default_model_name
        if name not in self.config.enabled_models:
            raise ModelNotConfiguredError(f"Model '{name}' is not enabled in config")
        if input_type not in {"passage", "query"}:
            raise InvalidRequestInputError("input_type must be 'passage' or 'query'")

        cleaned = [t.strip() for t in texts if t and t.strip()]
        if not cleaned:
            raise InvalidRequestInputError("Input text is empty or only whitespace.")
        if len(cleaned) > self.config.max_batch_size:
            raise InvalidRequestInputError(f"Too many texts. Maximum allowed is {self.config.max_batch_size}.")

        settings = self.config.model_settings(name)
        prefix = settings["query_prefix"] if input_type == "query" else settings["passage_prefix"]
        prefixed = [apply_input_prefix(text, prefix) for text in cleaned]
        encoder = self._get_encoder(name)
        try:
            vectors = encoder.encode(prefixed)
        except (InvalidRequestInputError, ModelNotConfiguredError, ModelNotReadyError):
            raise
        except Exception as exc:
            logger.exception("Embedding computation failed for model=%s", name)
            raise EmbeddingComputationError(str(exc)) from exc

        if len(vectors) != len(cleaned):
            raise EmbeddingComputationError("Encoder returned a different number of vectors than inputs")

        items = [{"text": original, "embedding": vector} for original, vector in zip(cleaned, vectors)]
        meta = {
            "model_name": name,
            "model_version": settings["version"],
            "hf_model_id": settings["hf_model_id"],
            "dimension": encoder.dimension,
            "input_type": input_type,
        }
        return items, meta
