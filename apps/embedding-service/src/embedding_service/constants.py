from pathlib import Path

from embedding_service.version import __version__

__all__ = (
    "__version__",
    "SERVICE_ROOT",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_SERVICE_PORT",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_HOST",
    "PROJECT_NAME",
    "PROJECT_DESCRIPTION",
    "API_VERSION_PREFIX",
    "DEFAULT_MODEL_NAME",
    "DEFAULT_HF_MODEL_ID",
    "DEFAULT_EMBEDDING_DIM",
    "DEFAULT_MAX_BATCH_SIZE",
)

SERVICE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = SERVICE_ROOT / "configs" / "staging" / "config.yml"
DEFAULT_SERVICE_PORT = 5100
DEFAULT_LOG_LEVEL = "info"
DEFAULT_HOST = "0.0.0.0"
PROJECT_NAME = "embedding-service"
PROJECT_DESCRIPTION = """
**Embedding Service**

Turns text into dense vectors using a Hugging Face model named in YAML config.
Weights are downloaded from the Hub into the local HF cache — they are not
stored in this repository.

- Default model: `Alibaba-NLP/gte-multilingual-base` (768-d, 8192 tokens)
- `input_type`: `passage` for document chunks, `query` for search questions.
  Per-model prefixes come from config (gte uses none; E5 uses `query:` / `passage:`).
"""
API_VERSION_PREFIX = "/api/v1"
DEFAULT_MODEL_NAME = "gte-multilingual-base"
DEFAULT_HF_MODEL_ID = "Alibaba-NLP/gte-multilingual-base"
DEFAULT_EMBEDDING_DIM = 768
DEFAULT_MAX_BATCH_SIZE = 32
