"""YAML + env config loading."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from embedding_service.constants import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_HF_MODEL_ID,
    DEFAULT_HOST,
    DEFAULT_LOG_LEVEL,
    DEFAULT_MAX_BATCH_SIZE,
    DEFAULT_MODEL_NAME,
    DEFAULT_SERVICE_PORT,
    SERVICE_ROOT,
)
from embedding_service.exceptions import ConfigurationFileNotFoundError

logger = logging.getLogger(__name__)

__all__ = ("ServiceConfig", "initialize_configuration")

# Only these are read from .env; every other setting belongs in configs/*/config.yml.
SECRET_ENV_KEYS = frozenset({"HF_TOKEN", "OPENAI_API_KEY"})


def _env_or(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class ServiceConfig:
    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    @property
    def enabled_models(self) -> list[str]:
        raw = self.get("models.enabled") or [DEFAULT_MODEL_NAME]
        return [str(name) for name in raw]

    @property
    def default_model_name(self) -> str:
        enabled = self.enabled_models
        return enabled[0] if enabled else DEFAULT_MODEL_NAME

    @property
    def max_batch_size(self) -> int:
        return int(self.get("max_batch_size", DEFAULT_MAX_BATCH_SIZE))

    @property
    def normalize_embedding(self) -> bool:
        return bool(self.get("normalize_embedding", True))

    @property
    def prefetch_on_start(self) -> bool:
        return bool(self.get("models.prefetchOnStart", True))

    @property
    def warmup_on_start(self) -> bool:
        return bool(self.get("models.warmupOnStart", True))

    def model_settings(self, model_name: str) -> dict[str, Any]:
        if model_name not in self.enabled_models:
            raise KeyError(model_name)
        max_seq_length = self.get(f"{model_name}.maxSeqLength")
        return {
            "name": model_name,
            "hf_model_id": self.get(f"{model_name}.hfModelId", DEFAULT_HF_MODEL_ID),
            "dimension": int(self.get(f"{model_name}.dimension", DEFAULT_EMBEDDING_DIM)),
            "version": int(self.get(f"{model_name}.version", 1)),
            "max_seq_length": int(max_seq_length) if max_seq_length else None,
            "trust_remote_code": bool(self.get(f"{model_name}.trustRemoteCode", False)),
            "extra_hub_repos": [str(repo) for repo in self.get(f"{model_name}.extraHubRepos") or []],
            "query_prefix": str(self.get(f"{model_name}.queryPrefix") or ""),
            "passage_prefix": str(self.get(f"{model_name}.passagePrefix") or ""),
        }


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigurationFileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigurationFileNotFoundError(f"Invalid YAML config at {path}")
    return data


def _load_secret_env(path: Path) -> None:
    for key, value in dotenv_values(path).items():
        if key not in SECRET_ENV_KEYS:
            logger.warning("Ignoring non-secret key %s in %s; set it in configs/*/config.yml", key, path)
            continue
        if value is not None and key not in os.environ:
            os.environ[key] = value


def _service_path(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (SERVICE_ROOT / path).resolve()


@lru_cache
def initialize_configuration() -> ServiceConfig:
    """Load YAML config; secrets come from .env, process env may still override."""
    _load_secret_env(SERVICE_ROOT / ".env")

    config_path = _service_path(os.getenv("REMOTE_CONFIG_URL") or DEFAULT_CONFIG_PATH)
    raw = _load_yaml(config_path)

    merged: dict[str, Any] = {
        **raw,
        "embeddingservice.port": int(_env_or("PORT", str(raw.get("embeddingservice.port", DEFAULT_SERVICE_PORT)))),
        "embeddingservice.hostName": _env_or("HOST", raw.get("embeddingservice.hostName", DEFAULT_HOST)),
        "embeddingservice.logLevel": _env_or("LOG_LEVEL", raw.get("embeddingservice.logLevel", DEFAULT_LOG_LEVEL)),
        "fastapi.logLevel": raw.get("fastapi.logLevel", DEFAULT_LOG_LEVEL),
        "models.prefetchOnStart": _env_bool("PREFETCH_ON_START", bool(raw.get("models.prefetchOnStart", True))),
        "models.warmupOnStart": _env_bool("WARMUP_ON_START", bool(raw.get("models.warmupOnStart", True))),
    }
    config = ServiceConfig(merged)
    logger.info(
        "Config loaded from %s models=%s",
        config_path,
        config.enabled_models,
    )
    return config
