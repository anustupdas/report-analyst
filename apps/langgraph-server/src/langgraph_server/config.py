from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, FrozenSet
from urllib.parse import quote

import yaml
from dotenv import dotenv_values
from pydantic import field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

logger = logging.getLogger(__name__)

SERVICE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONFIG_PATH = SERVICE_ROOT / "configs" / "staging" / "config.yml"
CONFIG_PATH_ENV = "LANGGRAPH_CONFIG_PATH"

SECRET_FIELDS: FrozenSet[str] = frozenset(
    {
        "database_url",
        "postgres_user",
        "postgres_password",
        "agent_secret",
        "openai_api_key",
        "langfuse_secret_key",
        "langfuse_public_key",
        "langfuse_host",
        "langfuse_base_url",
    }
)

YAML_KEYS: dict[str, str] = {
    "langgraph.environment": "environment",
    "langgraph.hostName": "langgraph_host",
    "langgraph.port": "langgraph_port",
    "langgraph.logLevel": "langgraph_log_level",
    "langgraph.checkpoint": "checkpoint_backend",
    "langgraph.sseKeepaliveSeconds": "sse_keepalive_seconds",
    "langgraph.corsOrigins": "cors_origins",
    "api.url": "api_url",
    "api.timeoutSeconds": "api_timeout_seconds",
    "database.host": "database_host",
    "database.port": "database_port",
    "database.name": "database_name",
    "vectorSearch.defaultLimit": "vector_search_default_limit",
    "langfuse.tracing": "langfuse_tracing",
    "langfuse.promptCacheTtlSeconds": "langfuse_prompt_cache_ttl_seconds",
    "supervisor.summarizationEnabled": "supervisor_summarization_enabled",
    "supervisor.summarizationTriggerMessages": "supervisor_summarization_trigger_messages",
    "supervisor.summarizationKeepMessages": "supervisor_summarization_keep_messages",
    "supervisor.summarizationTriggerTokens": "supervisor_summarization_trigger_tokens",
    "supervisor.summarizationTrimTokens": "supervisor_summarization_trim_tokens",
    "supervisor.maxReportSearchesPerTurn": "supervisor_max_report_searches_per_turn",
}


def config_path() -> Path:
    raw = os.getenv(CONFIG_PATH_ENV)
    if not raw:
        return DEFAULT_CONFIG_PATH
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (SERVICE_ROOT / path).resolve()


def psycopg_conninfo(url: str) -> str:
    """LangGraph's AsyncPostgresSaver wants a psycopg DSN, not a SQLAlchemy URL."""
    text = (url or "").strip()
    if not text:
        return text
    for marker in ("+psycopg2://", "+psycopg://", "+asyncpg://"):
        if marker in text:
            _scheme, rest = text.split("://", 1)
            return f"postgresql://{rest}"
    if text.startswith("postgres://"):
        return "postgresql://" + text[len("postgres://") :]
    return text


class YamlConfigSource(PydanticBaseSettingsSource):
    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        path = config_path()
        if not path.is_file():
            raise FileNotFoundError(f"LangGraph config file not found: {path}")
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
    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        ignored: set[str] = set()
        for path in (
            REPO_ROOT / ".env",
            REPO_ROOT / "apps" / "api" / ".env",
            SERVICE_ROOT / ".env",
        ):
            if not path.is_file():
                continue
            for raw_name, value in dotenv_values(path).items():
                name = (raw_name or "").lower()
                if name not in SECRET_FIELDS:
                    if name:
                        ignored.add(name)
                    continue
                if value is None or not str(value).strip():
                    continue
                merged[name] = value
        if ignored:
            logger.warning(
                ".env contains non-secret settings %s; they are ignored. Set them in %s.",
                [name.upper() for name in sorted(ignored)],
                config_path(),
            )
        return merged


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(
            str(REPO_ROOT / ".env"),
            str(REPO_ROOT / "apps" / "api" / ".env"),
            str(SERVICE_ROOT / ".env"),
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = ""
    postgres_user: str = "chat"
    postgres_password: str = "chat"
    agent_secret: str = ""
    openai_api_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_public_key: str = ""
    langfuse_host: str = ""
    langfuse_base_url: str = ""

    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "chat_project"

    langgraph_host: str = "0.0.0.0"
    langgraph_port: int = 8080
    langgraph_log_level: str = "info"
    environment: str = "development"
    checkpoint_backend: str = "postgres"
    sse_keepalive_seconds: float = 15.0
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    api_url: str = "http://localhost:8000"
    api_timeout_seconds: float = 30.0
    vector_search_default_limit: int = 8
    langfuse_tracing: bool = False
    langfuse_prompt_cache_ttl_seconds: int = 60
    supervisor_summarization_enabled: bool = True
    supervisor_summarization_trigger_messages: int = 50
    supervisor_summarization_keep_messages: int = 20
    supervisor_summarization_trigger_tokens: int = 80_000
    supervisor_summarization_trim_tokens: int = 24_000
    supervisor_max_report_searches_per_turn: int = 5

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            SecretsOnlyDotEnvSource(settings_cls),
            YamlConfigSource(settings_cls),
        )

    @field_validator("checkpoint_backend", mode="before")
    @classmethod
    def _normalize_checkpoint(cls, value: object) -> str:
        text = str(value or "postgres").strip().lower()
        if text not in {"postgres", "memory"}:
            raise ValueError("langgraph.checkpoint must be postgres or memory")
        return text

    @field_validator("langfuse_tracing", "supervisor_summarization_enabled", mode="before")
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
        self.database_url = psycopg_conninfo(self.database_url)
        if not self.langfuse_host.strip() and self.langfuse_base_url.strip():
            self.langfuse_host = self.langfuse_base_url.strip()
        self.langfuse_host = self.langfuse_host.rstrip("/")
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def api_base_url(self) -> str:
        return self.api_url.rstrip("/")

    @property
    def langfuse_prompt_labels(self) -> list[str]:
        env = (self.environment or "development").strip().lower()
        if env in {"production", "prod"}:
            return ["production"]
        return ["dev", "production"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
