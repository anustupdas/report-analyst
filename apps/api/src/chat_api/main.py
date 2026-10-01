from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

import fastapi

from chat_api.config import get_settings
from chat_api.errors import register_exception_handlers
from chat_api.http.v1 import api_v1_router
from chat_api.langfuse_client import flush_langfuse, initialize_langfuse
from chat_api.tracing import RequestContextMiddleware
from chat_api.version import __version__

logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def verify_embedding_dimension() -> None:
    """Fail fast if config, ORM provider model and DB column disagree on vector size."""
    from chat_api.db import document_chunks_vector_dimension
    from chat_api.modules.embeddings import (
        normalize_provider,
        provider_dimension,
        provider_table_name,
    )

    settings = get_settings()
    provider = normalize_provider(settings.embedding_provider)
    expected = provider_dimension(provider)
    table = provider_table_name(provider)
    if settings.embedding_dimension != expected:
        raise RuntimeError(
            f"embedding.dimension={settings.embedding_dimension} but provider "
            f"{provider!r} requires vector({expected}). Fix configs/*/config.yml."
        )
    try:
        db_dimension = document_chunks_vector_dimension(provider)
    except Exception as exc:
        logger.warning("Could not check %s vector dimension (DB unavailable?): %s", table, exc)
        return
    if db_dimension is None:
        logger.warning("%s.embedding not found; run `make db-migrate`", table)
    elif db_dimension != expected:
        raise RuntimeError(
            f"{table}.embedding is vector({db_dimension}) but the API expects "
            f"vector({expected}). Run `make db-migrate`."
        )


@asynccontextmanager
async def lifespan(_app: fastapi.FastAPI):
    settings = get_settings()
    settings.data_root.mkdir(parents=True, exist_ok=True)
    verify_embedding_dimension()
    logger.info(
        "Annual Report Analyst API starting version=%s data_root=%s extraction=%s "
        "embedding=%s provider=%s model=%s dim=%s",
        __version__,
        settings.data_root,
        settings.text_extraction_url,
        settings.embedding_service_url,
        settings.embedding_provider,
        settings.embedding_model_name,
        settings.embedding_dimension,
    )
    initialize_langfuse(settings)
    yield
    flush_langfuse()
    logger.info("Annual Report Analyst API shutting down")


def create_app() -> fastapi.FastAPI:
    settings = get_settings()
    configure_logging(settings.api_log_level)

    app = fastapi.FastAPI(
        title="Annual Report Analyst API",
        version=__version__,
        description=(
            "Backend for **Annual Report Analyst**, an assistant that ingests "
            "annual-report PDFs and answers questions with verbatim, cited "
            "datapoints (e.g. FTE count, sustainability goals).\n\n"
            "Owns users, report projects, document ingest and the pgvector "
            "index; calls the text-extraction and embedding services.\n\n"
            "## Auth\n"
            "1. `POST /api/v1/users` → copy `data.apiToken` (shown once).\n"
            "2. Click **Authorize** and paste the token (Swagger adds `Bearer`).\n"
            "3. Or send `Authorization: Bearer <apiToken>` on each request.\n"
            "Agent routes use `X-Agent-Secret` instead of the user token.\n\n"
            "## Document ingest flow\n"
            "`prepare` → `PUT .../content` → `process` → poll document until "
            "`ready` / `completed` / `failed`.\n\n"
            "Files live under `data/{userId}/{projectId}/{documentId}/`."
        ),
        lifespan=lifespan,
        swagger_ui_parameters={"persistAuthorization": True},
    )
    app.add_middleware(RequestContextMiddleware)
    # CORS for optional separate UI host (e.g. python -m http.server).
    # Same-origin UI is also served from this process when apps/web exists.
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id", "X-Session-Id"],
    )
    register_exception_handlers(app)
    app.include_router(api_v1_router)

    web_dir = settings.web_static_dir
    if web_dir.is_dir() and (web_dir / "index.html").is_file():
        from fastapi.responses import FileResponse

        def _static(path: str, media_type: str) -> FileResponse:
            return FileResponse(
                web_dir / path,
                media_type=media_type,
                headers={"Cache-Control": "no-store"},
            )

        @app.get("/", include_in_schema=False)
        def web_index() -> FileResponse:
            return _static("index.html", "text/html; charset=utf-8")

        @app.get("/project/{project_id}", include_in_schema=False)
        def web_project(project_id: str) -> FileResponse:
            """SPA entry for /project/{id}."""
            _ = project_id
            return _static("index.html", "text/html; charset=utf-8")

        @app.get("/styles.css", include_in_schema=False)
        def web_styles() -> FileResponse:
            return _static("styles.css", "text/css; charset=utf-8")

        @app.get("/app.js", include_in_schema=False)
        def web_app_js() -> FileResponse:
            return _static("app.js", "application/javascript; charset=utf-8")

        @app.get("/config.js", include_in_schema=False)
        def web_config_js() -> fastapi.Response:
            payload = {
                "langgraphBaseUrl": (settings.langgraph_url or "http://localhost:8080").rstrip("/"),
            }
            return fastapi.Response(
                content=f"window.APP_CONFIG = {json.dumps(payload)};\n",
                media_type="application/javascript; charset=utf-8",
                headers={"Cache-Control": "no-store"},
            )

        logger.info("Serving UI from %s at / and /project/{id}", web_dir)

    return app


app = create_app()


def run_app(host: str | None = None, port: int | None = None, log_level: str | None = None) -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "chat_api.main:app",
        host=host or settings.api_host,
        port=port or settings.api_port,
        log_level=(log_level or settings.api_log_level).lower(),
        reload=False,
    )
