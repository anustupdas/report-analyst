from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import fastapi
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from langgraph_server.config import get_settings
from langgraph_server.core.langfuse_client import flush_langfuse, initialize_langfuse
from langgraph_server.core.supervisor.graph import build_supervisor_graph
from langgraph_server.http.datapoints import router as datapoints_router
from langgraph_server.http.supervisor import router as supervisor_router
from langgraph_server.version import __version__

logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


@asynccontextmanager
async def lifespan(app: fastapi.FastAPI) -> AsyncGenerator[None, None]:
    if getattr(app.state, "preloaded_graph", None) is not None:
        app.state.supervisor_graph = app.state.preloaded_graph
        yield
        return

    settings = get_settings()
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    initialize_langfuse(settings)

    llm = getattr(app.state, "llm", None)
    if settings.checkpoint_backend == "memory":
        app.state.supervisor_graph = build_supervisor_graph(InMemorySaver(), llm=llm)
        logger.info("LangGraph server started with in-memory checkpointer")
        yield
        flush_langfuse()
        return

    try:
        async with AsyncPostgresSaver.from_conn_string(settings.database_url) as saver:
            await saver.setup()
            app.state.supervisor_graph = build_supervisor_graph(saver, llm=llm)
            logger.info("LangGraph server started with Postgres checkpointer")
            yield
            flush_langfuse()
            return
    except Exception:
        logger.exception("Postgres checkpointer failed; falling back to in-memory")

    app.state.supervisor_graph = build_supervisor_graph(InMemorySaver(), llm=llm)
    logger.info("LangGraph server started with in-memory checkpointer")
    yield
    flush_langfuse()


def create_app(*, graph: Any | None = None, llm: Any | None = None) -> fastapi.FastAPI:
    settings = get_settings()
    configure_logging(settings.langgraph_log_level)

    app = fastapi.FastAPI(
        title="Annual Report Analyst LangGraph",
        version=__version__,
        description=(
            "Supervisor ReAct agent for annual-report Q&A. "
            "Browser calls `POST /supervisor/stream` with the user Bearer token."
        ),
        lifespan=lifespan,
    )
    app.state.preloaded_graph = graph
    app.state.llm = llm
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(supervisor_router)
    app.include_router(datapoints_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "OK"}

    return app


app = create_app()


def run_app(host: str | None = None, port: int | None = None, log_level: str | None = None) -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "langgraph_server.main:app",
        host=host or settings.langgraph_host,
        port=port or settings.langgraph_port,
        log_level=(log_level or settings.langgraph_log_level).lower(),
        reload=False,
    )
