from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, FrozenSet
from urllib.parse import quote

import yaml
from pydantic import Field, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

logger = logging.getLogger(__name__)

# apps/api/src/chat_api/config.py → parents[2] = apps/api, parents[4] = monorepo root
API_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONFIG_PATH = API_ROOT / "configs" / "staging" / "config.yml"
CONFIG_PATH_ENV = "API_CONFIG_PATH"

# Only these may come from .env files; everything else belongs in configs/*/config.yml.
SECRET_FIELDS: FrozenSet[str] = frozenset(
    {
        "database_url",
        "postgres_user",
        "postgres_password",
        "agent_secret",
        "internal_workflow_secret",
        "openai_api_key",
        "langfuse_secret_key",
        "langfuse_public_key",
        "langfuse_host",
        "langfuse_base_url",
    }
)

YAML_KEYS: dict[str, str] = {
    "api.environment": "environment",
    "api.hostName": "api_host",
    "api.port": "api_port",
    "api.logLevel": "api_log_level",
    "api.webStaticDir": "web_static_dir",
    "api.corsOrigins": "cors_origins",
    "database.host": "database_host",
    "database.port": "database_port",
    "database.name": "database_name",
    "storage.dataRoot": "data_root",
    "storage.maxUploadBytes": "max_upload_bytes",
    "storage.allowedExtensions": "allowed_extensions",
    "textExtraction.url": "text_extraction_url",
    "textExtraction.useOcr": "text_extraction_use_ocr",
    "textExtraction.timeoutSeconds": "text_extraction_timeout_seconds",
    "embedding.url": "embedding_service_url",
    "embedding.timeoutSeconds": "embedding_timeout_seconds",
    "embedding.provider": "embedding_provider",
    "embedding.modelName": "embedding_model_name",
    "embedding.dimension": "embedding_dimension",
    "chunking.targetChars": "chunk_target_chars",
    "chunking.maxChars": "chunk_max_chars",
    "chunking.overlapSentences": "chunk_overlap_sentences",
    "vectorSearch.defaultLimit": "vector_search_default_limit",
    "vectorSearch.maxLimit": "vector_search_max_limit",
    "ingest.staleProcessingMinutes": "stale_processing_minutes",
    "langgraph.url": "langgraph_url",
    "langgraph.internalUrl": "langgraph_internal_url",
    "llm.summaryEnabled": "llm_summary_enabled",
    "llm.descriptionContextChunks": "llm_description_context_chunks",
    "llm.timeoutSeconds": "llm_timeout_seconds",
    "langfuse.tracing": "langfuse_tracing",
    "langfuse.promptCacheTtlSeconds": "langfuse_prompt_cache_ttl_seconds",
}


def config_path() -> Path:
    raw = os.getenv(CONFIG_PATH_ENV)
    if not raw:
        return DEFAULT_CONFIG_PATH
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (API_ROOT / path).resolve()


class YamlConfigSource(PydanticBaseSettingsSource):
    """Non-secret settings from configs/{env}/config.yml (flat dotted keys)."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        path = config_path()
        if not path.is_file():
            raise FileNotFoundError(f"API config file not found: {path}")
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid YAML config at {path}")

        values: dict[str, Any] = {}
        for key, value in raw.items():
            field_name = YAML_KEYS.get(key)
            if field_name is None:
                logger.warning("Unknown key %r in %s (ignored)", key, path)
                continue
            if isinstance(value, list):
                value = ",".join(str(item) for item in value)
            values[field_name] = value
        return values


class SecretsOnlyDotEnvSource(PydanticBaseSettingsSource):
    """Wraps the .env source so only secret fields are read from .env files."""

    def __init__(self, settings_cls: type[BaseSettings], inner: PydanticBaseSettingsSource) -> None:
        super().__init__(settings_cls)
        self._inner = inner

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        values = self._inner()
        ignored = sorted(name for name in values if name not in SECRET_FIELDS)
        if ignored:
            logger.warning(
                ".env contains non-secret settings %s; they are ignored. Set them in %s.",
                [name.upper() for name in ignored],
                config_path(),
            )
        return {name: value for name, value in values.items() if name in SECRET_FIELDS and value not in (None, "")}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Later files win: apps/api/.env overrides the repo-root .env.
        env_file=(str(REPO_ROOT / ".env"), str(API_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Secrets (.env). database_url is optional; when empty it is built from database.* + POSTGRES_*.
    database_url: str = ""
    postgres_user: str = "chat"
    postgres_password: str = "chat"
    agent_secret: str = ""
    internal_workflow_secret: str = ""
    openai_api_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_public_key: str = ""
    langfuse_host: str = ""
    langfuse_base_url: str = ""

    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "chat_project"

    data_root: Path = Field(default=REPO_ROOT / "data")

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_log_level: str = "info"
    environment: str = "development"

    text_extraction_url: str = "http://localhost:5000"
    # False keeps ingest on PyMuPDF. OCR is opt-in via textExtraction.useOcr.
    text_extraction_use_ocr: bool = False
    text_extraction_timeout_seconds: float = 300.0

    max_upload_bytes: int = 100 * 1024 * 1024
    allowed_extensions: str = "pdf,txt,png,jpg,jpeg,docx,pptx,md"

    embedding_service_url: str = "http://localhost:5100"
    embedding_timeout_seconds: float = 120.0
    # Default store is OpenAI (1536-d, document_chunk_embeddings_openai).
    # huggingface → local gte (768-d, document_chunk_embeddings_hf).
    embedding_provider: str = "openai"
    embedding_model_name: str = "text-embedding-3-small"
    embedding_dimension: int = 1536
    chunk_target_chars: int = 2000
    chunk_max_chars: int = 2500
    chunk_overlap_sentences: int = 2
    vector_search_default_limit: int = 8
    vector_search_max_limit: int = 50
    langgraph_url: str = ""
    # Server-side calls (datapoints). Empty uses langgraph_url. Docker sets this to
    # http://langgraph:8080 while langgraph_url stays the browser address.
    langgraph_internal_url: str = ""
    llm_summary_enabled: bool = False
    llm_description_context_chunks: int = 10
    llm_timeout_seconds: float = 60.0
    langfuse_tracing: bool = False
    langfuse_prompt_cache_ttl_seconds: int = 60

    stale_processing_minutes: int = 14

    # Comma-separated browser origins allowed to call the API cross-origin.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000"

    # Zero-build UI (apps/web). Empty disables static hosting.
    web_static_dir: Path = Field(default=REPO_ROOT / "apps" / "web")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Priority: explicit kwargs > process env > .env (secrets only) > YAML > defaults.
        return (
            init_settings,
            env_settings,
            SecretsOnlyDotEnvSource(settings_cls, dotenv_settings),
            YamlConfigSource(settings_cls),
        )

    @field_validator("data_root", "web_static_dir", mode="before")
    @classmethod
    def _resolve_path(cls, value: object) -> Path:
        path = Path(str(value or ".")).expanduser()
        if not path.is_absolute():
            path = API_ROOT / path
        return path.resolve()

    @field_validator("langfuse_tracing", mode="before")
    @classmethod
    def _coerce_bool(cls, value: object) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @model_validator(mode="after")
    def _build_database_url(self) -> Settings:
        if not self.database_url:
            self.database_url = (
                f"postgresql://{quote(self.postgres_user, safe='')}:{quote(self.postgres_password, safe='')}"
                f"@{self.database_host}:{self.database_port}/{self.database_name}"
            )
        if not self.langfuse_host.strip() and self.langfuse_base_url.strip():
            self.langfuse_host = self.langfuse_base_url.strip()
        self.langfuse_host = self.langfuse_host.rstrip("/")
        # Normalize provider spelling and keep dimension aligned with the active store.
        from chat_api.modules.embeddings import normalize_provider, provider_dimension

        self.embedding_provider = normalize_provider(self.embedding_provider)
        expected = provider_dimension(self.embedding_provider)  # type: ignore[arg-type]
        if self.embedding_dimension != expected:
            raise ValueError(
                f"embedding.dimension={self.embedding_dimension} does not match "
                f"embedding.provider={self.embedding_provider} (expected {expected})"
            )
        return self

    @property
    def langfuse_prompt_labels(self) -> list[str]:
        env = (self.environment or "development").strip().lower()
        if env in {"production", "prod"}:
            return ["production"]
        return ["dev", "production"]

    @property
    def allowed_extension_set(self) -> FrozenSet[str]:
        return frozenset(ext.strip().lower().lstrip(".") for ext in self.allowed_extensions.split(",") if ext.strip())

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_root.mkdir(parents=True, exist_ok=True)
    return settings
