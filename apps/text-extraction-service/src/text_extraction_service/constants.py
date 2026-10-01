from pathlib import Path

from text_extraction_service.version import __version__

__all__ = (
    "__version__",
    "SERVICE_ROOT",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_SERVICE_PORT",
    "DEFAULT_PROMETHEUS_ENABLED",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_HOST",
    "PROJECT_NAME",
    "PROJECT_DESCRIPTION",
    "API_VERSION_PREFIX",
    "DEFAULT_SUPPORTED_FORMATS",
    "DEFAULT_IMAGE_FORMATS",
    "DEFAULT_MISTRAL_MODEL",
    "DEFAULT_LOCAL_DATA_DIR",
    "DEFAULT_AWS_REGION",
)

SERVICE_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CONFIG_PATH = SERVICE_ROOT / "configs" / "staging" / "config.yml"
DEFAULT_SERVICE_PORT = 5000
DEFAULT_PROMETHEUS_ENABLED = True
DEFAULT_LOG_LEVEL = "info"
DEFAULT_HOST = "0.0.0.0"
PROJECT_NAME = "text-extraction-service"
PROJECT_DESCRIPTION = """
**Text Extraction Service**

Simple microservice that extracts text from documents using PyMuPDF and optional Mistral OCR.

- Local disk or S3 via `storage.useS3` / `USE_S3`
- Mistral OCR via `mistral.enabled` / `USE_MISTRAL` (images require it)
- Whole-file extraction only — no per-page OCR heuristics
"""
API_VERSION_PREFIX = "/api/v1"

DEFAULT_SUPPORTED_FORMATS = ["txt", "md", "doc", "docx", "pdf", "ppt", "pptx", "png", "jpg", "jpeg"]
DEFAULT_IMAGE_FORMATS = ["png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "tif"]
DEFAULT_MISTRAL_MODEL = "mistral-ocr-4-1"
# Shared monorepo store: <repo>/data/{user}/{project}/{doc}/…
# SERVICE_ROOT is apps/text-extraction-service → parents[1] is repo root.
REPO_ROOT = SERVICE_ROOT.parents[1]
DEFAULT_LOCAL_DATA_DIR = REPO_ROOT / "data"
DEFAULT_AWS_REGION = "eu-west-1"
