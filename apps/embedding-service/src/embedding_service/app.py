import logging
from contextlib import asynccontextmanager

import fastapi
import uvicorn
from fastapi import Request
from fastapi.responses import JSONResponse

from embedding_service.api_v1.router import embedding_service_router as router_v1
from embedding_service.constants import (
    API_VERSION_PREFIX,
    DEFAULT_HOST,
    DEFAULT_LOG_LEVEL,
    DEFAULT_SERVICE_PORT,
    PROJECT_DESCRIPTION,
    PROJECT_NAME,
)
from embedding_service.exceptions import EmbeddingServiceError
from embedding_service.metrics import metrics_response
from embedding_service.service import prefetch_enabled_models
from embedding_service.utils import initialize_configuration
from embedding_service.version import __version__

logger = logging.getLogger(__name__)

__all__ = ("run_app", "app")


@asynccontextmanager
async def lifespan(_app: fastapi.FastAPI):
    import asyncio
    import logging as logging_module

    if not logging_module.getLogger().handlers:
        logging_module.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )
    config = initialize_configuration()
    if config.prefetch_on_start:
        logger.info("Startup prefetch: download Hub files into cache")
        await asyncio.to_thread(prefetch_enabled_models, config)
        logger.info("Startup prefetch finished")
    else:
        logger.info("Startup prefetch disabled (PREFETCH_ON_START=false)")

    from embedding_service.api_v1.router import get_pool, reset_runtime

    pool = get_pool()
    if config.warmup_on_start:
        logger.info("Startup warmup: load query + passage models into memory")
        await pool.warmup_async()
        logger.info("Startup warmup finished")
    else:
        logger.info("Startup warmup disabled (WARMUP_ON_START=false)")
    yield
    reset_runtime()
    logger.info("Killing app")


app = fastapi.FastAPI(
    title=PROJECT_NAME,
    version=__version__,
    description=PROJECT_DESCRIPTION,
    lifespan=lifespan,
)
app.include_router(router_v1, prefix=API_VERSION_PREFIX)


@app.get("/info")
async def info() -> dict:
    from embedding_service.api_v1.router import get_pool

    conf = initialize_configuration()
    model = conf.model_settings(conf.default_model_name)
    pool = get_pool()
    loaded = pool.both_lanes_ready(conf.default_model_name)
    return {
        "name": PROJECT_NAME,
        "description": PROJECT_DESCRIPTION.strip().split("\n")[0].strip("* "),
        "version": __version__,
        "models": conf.enabled_models,
        "hfModelId": model["hf_model_id"],
        "dimension": model["dimension"],
        "prefetchOnStart": conf.prefetch_on_start,
        "warmupOnStart": conf.warmup_on_start,
        "loadedInMemory": loaded,
        "lanes": {
            "query": pool.lane_ready("query", conf.default_model_name),
            "passage": pool.lane_ready("passage", conf.default_model_name),
        },
        "encodePool": {
            "queryWorkers": 1,
            "passageWorkers": 1,
            "policy": "dedicated-lanes",
        },
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


@app.exception_handler(EmbeddingServiceError)
async def handle_embedding_service_error(_request: Request, exc: EmbeddingServiceError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def run_app(port: int | None = None, log_level: str | None = None) -> None:
    config = initialize_configuration()

    port = port or config.get("embeddingservice.port", DEFAULT_SERVICE_PORT)
    host = config.get("embeddingservice.hostName", DEFAULT_HOST)
    log_level = log_level or config.get("embeddingservice.logLevel", DEFAULT_LOG_LEVEL)
    fastapi_log_level = config.get("fastapi.logLevel", DEFAULT_LOG_LEVEL)

    logging.basicConfig(
        level=getattr(logging, str(log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    logger.info("Starting app on %s:%s models=%s", host, port, config.enabled_models)
    uvicorn.run(app, host=host, port=port, log_level=fastapi_log_level)


if __name__ == "__main__":
    run_app(log_level="debug")
