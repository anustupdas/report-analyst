import logging
from contextlib import asynccontextmanager

import fastapi
import uvicorn
from fastapi import Request
from fastapi.responses import JSONResponse

from text_extraction_service.api_v1.text_extraction_router import get_service
from text_extraction_service.api_v1.text_extraction_router import (
    text_extraction_service_router as router_v1,
)
from text_extraction_service.constants import (
    API_VERSION_PREFIX,
    DEFAULT_HOST,
    DEFAULT_LOG_LEVEL,
    DEFAULT_SERVICE_PORT,
    PROJECT_DESCRIPTION,
    PROJECT_NAME,
)
from text_extraction_service.exceptions import TextExtractionError
from text_extraction_service.metrics import metrics_response
from text_extraction_service.utils import initialize_configuration, setup_project_directory
from text_extraction_service.version import __version__

logger = logging.getLogger(__name__)

__all__ = ("run_app", "app")


def configure_logging(log_level: str | None = None) -> None:
    level_name = str(log_level or initialize_configuration().get("textextractionservice.logLevel", DEFAULT_LOG_LEVEL))
    level = getattr(logging, level_name.upper(), logging.INFO)
    # uvicorn only configures its own loggers; without this, app INFO logs are dropped.
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    logging.getLogger("text_extraction_service").setLevel(level)


@asynccontextmanager
async def lifespan(_app: fastapi.FastAPI):
    configure_logging()
    try:
        get_service()
    except TextExtractionError as exc:
        logger.error("Extraction service not ready: %s", exc.detail)
    yield
    logger.info("Killing app")
    logger.info("Goodbye!")


app = fastapi.FastAPI(
    title=PROJECT_NAME,
    version=__version__,
    description=PROJECT_DESCRIPTION,
    lifespan=lifespan,
)
app.include_router(router_v1, prefix=API_VERSION_PREFIX)


@app.get("/info")
async def info() -> dict:
    conf = initialize_configuration()
    return {
        "name": PROJECT_NAME,
        "description": PROJECT_DESCRIPTION,
        "version": __version__,
        "use_s3": conf.use_s3,
        "use_mistral": conf.use_mistral,
        "mistral_version": conf.mistral_model if conf.use_mistral else None,
        "mistral_options": conf.mistral_options.request_kwargs() if conf.use_mistral else None,
        "local_data_dir": str(conf.local_data_dir) if not conf.use_s3 else None,
        "s3_bucket": conf.get("storage.bucket") if conf.use_s3 else None,
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "OK"}


@app.get("/metrics", include_in_schema=False)
async def metrics():
    """Prometheus scrape endpoint on this service's port."""
    if not initialize_configuration().get("prometheus.enabled", True):
        return JSONResponse(status_code=404, content={"detail": "metrics disabled"})
    return metrics_response()


@app.get("/version")
async def version() -> dict:
    return {"version": __version__}


@app.exception_handler(TextExtractionError)
async def handle_text_extraction_service_error(request: Request, exc: TextExtractionError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def run_app(port: int | None = None, log_level: str | None = None) -> None:
    config = initialize_configuration()
    setup_project_directory(config)

    port = port or config.get("textextractionservice.port", DEFAULT_SERVICE_PORT)
    host = config.get("textextractionservice.hostName", DEFAULT_HOST)
    log_level = log_level or config.get("textextractionservice.logLevel", DEFAULT_LOG_LEVEL)
    fastapi_log_level = config.get("fastapi.logLevel", DEFAULT_LOG_LEVEL)

    configure_logging(log_level)

    logger.info(
        "Starting app on %s:%s (use_s3=%s use_mistral=%s)",
        host,
        port,
        config.use_s3,
        config.use_mistral,
    )
    uvicorn.run(app, host=host, port=port, log_level=fastapi_log_level)


if __name__ == "__main__":
    run_app(log_level="debug")
