"""Config loading, storage resolution, and shared helpers."""

from __future__ import annotations

import logging
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import yaml
from dotenv import dotenv_values

from text_extraction_service.constants import (
    DEFAULT_AWS_REGION,
    DEFAULT_CONFIG_PATH,
    DEFAULT_HOST,
    DEFAULT_IMAGE_FORMATS,
    DEFAULT_LOCAL_DATA_DIR,
    DEFAULT_LOG_LEVEL,
    DEFAULT_MISTRAL_MODEL,
    DEFAULT_PROMETHEUS_ENABLED,
    DEFAULT_SERVICE_PORT,
    DEFAULT_SUPPORTED_FORMATS,
    PROJECT_NAME,
    SERVICE_ROOT,
)
from text_extraction_service.exceptions import (
    ConfigurationFileNotFoundError,
    FileDownloadError,
    S3ConnectionError,
    StorageConfigError,
)
from text_extraction_service.extractor import MistralOptions

logger = logging.getLogger(__name__)

__all__ = (
    "ServiceConfig",
    "initialize_configuration",
    "setup_project_directory",
    "get_file_extension",
    "build_file_resolver",
)


# Only these are read from .env; every other setting belongs in configs/*/config.yml.
SECRET_ENV_KEYS = frozenset({"MISTRAL_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"})


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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_or(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


class ServiceConfig:
    """Flat config object loaded from YAML + .env overrides/secrets."""

    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    @property
    def use_s3(self) -> bool:
        return bool(self.get("storage.useS3", False))

    @property
    def use_mistral(self) -> bool:
        return bool(self.get("mistral.enabled", False))

    @property
    def local_data_dir(self) -> Path:
        return _service_path(self.get("data.location", DEFAULT_LOCAL_DATA_DIR))

    @property
    def supported_formats(self) -> list[str]:
        return list(self.get("files.supportedFormats", DEFAULT_SUPPORTED_FORMATS))

    @property
    def image_formats(self) -> list[str]:
        return list(self.get("files.imageFormats", DEFAULT_IMAGE_FORMATS))

    @property
    def mistral_api_key(self) -> str | None:
        return self.get("mistral.apiKey")

    @property
    def mistral_model(self) -> str:
        return self.get("mistral.version", DEFAULT_MISTRAL_MODEL)

    @property
    def mistral_options(self) -> MistralOptions:
        defaults = MistralOptions()
        image_limit = self.get("mistral.imageLimit")
        image_min_size = self.get("mistral.imageMinSize", defaults.image_min_size)
        return MistralOptions(
            table_format=self.get("mistral.tableFormat", defaults.table_format),
            extract_header=bool(self.get("mistral.extractHeader", defaults.extract_header)),
            extract_footer=bool(self.get("mistral.extractFooter", defaults.extract_footer)),
            include_images=bool(self.get("mistral.includeImages", defaults.include_images)),
            image_min_size=int(image_min_size) if image_min_size else None,
            image_limit=int(image_limit) if image_limit is not None else None,
            include_blocks=bool(self.get("mistral.includeBlocks", defaults.include_blocks)),
            confidence_granularity=self.get("mistral.confidenceGranularity", defaults.confidence_granularity),
        )

    def validate_runtime(self) -> None:
        if self.use_s3:
            missing = []
            if not self.get("storage.bucket"):
                missing.append("S3_BUCKET / storage.bucket")
            if not self.get("storage.awsAccessKeyId"):
                missing.append("AWS_ACCESS_KEY_ID")
            if not self.get("storage.awsSecretAccessKey"):
                missing.append("AWS_SECRET_ACCESS_KEY")
            if missing:
                raise StorageConfigError(
                    f"USE_S3=true requires: {', '.join(missing)}. "
                    "When USE_S3=false, AWS credentials are not required."
                )
        else:
            self.local_data_dir.mkdir(parents=True, exist_ok=True)

        if self.use_mistral and not self.mistral_api_key:
            raise StorageConfigError("USE_MISTRAL=true requires MISTRAL_API_KEY in .env")


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigurationFileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigurationFileNotFoundError(f"Invalid YAML config at {path}")
    return data


@lru_cache
def initialize_configuration() -> ServiceConfig:
    """Load YAML config; secrets come from .env, process env may still override."""
    _load_secret_env(SERVICE_ROOT / ".env")

    config_path = _service_path(os.getenv("REMOTE_CONFIG_URL") or DEFAULT_CONFIG_PATH)
    raw = _load_yaml(config_path)

    use_s3_default = bool(raw.get("storage.useS3", False))
    use_mistral_default = bool(raw.get("mistral.enabled", False))

    merged: dict[str, Any] = {
        **raw,
        "textextractionservice.port": int(
            _env_or("PORT", str(raw.get("textextractionservice.port", DEFAULT_SERVICE_PORT)))
        ),
        "textextractionservice.hostName": _env_or("HOST", raw.get("textextractionservice.hostName", DEFAULT_HOST)),
        "textextractionservice.logLevel": _env_or(
            "LOG_LEVEL", raw.get("textextractionservice.logLevel", DEFAULT_LOG_LEVEL)
        ),
        "fastapi.logLevel": raw.get("fastapi.logLevel", DEFAULT_LOG_LEVEL),
        "prometheus.enabled": _env_bool(
            "PROMETHEUS_ENABLED", bool(raw.get("prometheus.enabled", DEFAULT_PROMETHEUS_ENABLED))
        ),
        "storage.useS3": _env_bool("USE_S3", use_s3_default),
        "storage.serviceName": raw.get("storage.serviceName", PROJECT_NAME),
        "storage.bucket": _env_or("S3_BUCKET", raw.get("storage.bucket")),
        "storage.awsRegion": _env_or("AWS_REGION", raw.get("storage.awsRegion", DEFAULT_AWS_REGION)),
        "storage.awsAccessKeyId": _env_or("AWS_ACCESS_KEY_ID", raw.get("storage.awsAccessKeyId")),
        "storage.awsSecretAccessKey": _env_or("AWS_SECRET_ACCESS_KEY", raw.get("storage.awsSecretAccessKey")),
        "storage.awsEndpointUrl": _env_or("AWS_ENDPOINT_URL", raw.get("storage.awsEndpointUrl")),
        "data.location": _env_or("LOCAL_DATA_DIR", raw.get("data.location", str(DEFAULT_LOCAL_DATA_DIR))),
        "mistral.enabled": _env_bool("USE_MISTRAL", use_mistral_default),
        "mistral.version": _env_or("MISTRAL_OCR_MODEL", raw.get("mistral.version", DEFAULT_MISTRAL_MODEL)),
        "mistral.apiKey": _env_or("MISTRAL_API_KEY", raw.get("mistral.apiKey")),
        "files.supportedFormats": raw.get("files.supportedFormats", DEFAULT_SUPPORTED_FORMATS),
        "files.imageFormats": raw.get("files.imageFormats", DEFAULT_IMAGE_FORMATS),
    }

    config = ServiceConfig(merged)
    config.validate_runtime()
    logger.info(
        "Config loaded from %s (use_s3=%s use_mistral=%s)",
        config_path,
        config.use_s3,
        config.use_mistral,
    )
    return config


def setup_project_directory(config: ServiceConfig) -> None:
    if config.use_s3:
        return
    destination = config.local_data_dir
    destination.mkdir(parents=True, exist_ok=True)
    logger.debug("Project data directory: %s", destination)


def get_file_extension(file_path: str | Path) -> str:
    if file_path is None:
        raise ValueError("Invalid file path provided.")
    path = Path(file_path)
    suffix = path.suffix
    return suffix.lower().lstrip(".") if suffix else "unknown"


class FileResolver(Protocol):
    def resolve(self, file_ref: str) -> Path:
        pass


class LocalFileResolver:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.resolve()

    def resolve(self, file_ref: str) -> Path:
        if not file_ref or not file_ref.strip():
            raise FileDownloadError("file_ref is required")

        candidate = Path(file_ref)
        if not candidate.is_absolute():
            candidate = self.data_dir / file_ref
        candidate = candidate.resolve()

        try:
            candidate.relative_to(self.data_dir)
        except ValueError as exc:
            raise FileDownloadError(f"Path must be under {self.data_dir}: {file_ref}") from exc

        if not candidate.is_file():
            raise FileDownloadError(f"Local file not found: {file_ref}")

        logger.info("Resolved local file: %s", candidate)
        return candidate


class S3FileResolver:
    def __init__(self, config: ServiceConfig) -> None:
        bucket = config.get("storage.bucket")
        if not bucket:
            raise StorageConfigError("storage.bucket / S3_BUCKET is required when USE_S3=true")

        try:
            import boto3
        except ImportError as exc:
            raise StorageConfigError("boto3 is required when USE_S3=true") from exc

        client_kwargs: dict[str, Any] = {
            "region_name": config.get("storage.awsRegion", DEFAULT_AWS_REGION),
        }
        access_key = config.get("storage.awsAccessKeyId")
        secret_key = config.get("storage.awsSecretAccessKey")
        if access_key and secret_key:
            client_kwargs["aws_access_key_id"] = access_key
            client_kwargs["aws_secret_access_key"] = secret_key
        endpoint = config.get("storage.awsEndpointUrl")
        if endpoint:
            client_kwargs["endpoint_url"] = endpoint

        try:
            self._client = boto3.client("s3", **client_kwargs)
        except Exception as exc:
            raise S3ConnectionError(f"Failed to create S3 client: {exc}") from exc

        self._bucket = bucket
        self._temp_dir = Path(tempfile.mkdtemp(prefix="text-extract-"))

    def resolve(self, file_ref: str) -> Path:
        if not file_ref or not file_ref.strip():
            raise FileDownloadError("file_ref (S3 key) is required")

        key = file_ref.lstrip("/")
        dest = self._temp_dir / Path(key).name
        try:
            self._client.download_file(self._bucket, key, str(dest))
        except Exception as exc:
            logger.exception("S3 download failed for key=%s", key)
            raise FileDownloadError(f"S3 object not found or unreachable: {key}") from exc

        logger.info("Downloaded s3://%s/%s -> %s", self._bucket, key, dest)
        return dest


def build_file_resolver(config: ServiceConfig) -> FileResolver:
    if config.use_s3:
        logger.info("Storage mode: S3 (bucket=%s)", config.get("storage.bucket"))
        return S3FileResolver(config)
    logger.info("Storage mode: local (dir=%s)", config.local_data_dir)
    return LocalFileResolver(config.local_data_dir)
