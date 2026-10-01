"""Langfuse client: prompt fetch and one request-root trace."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langgraph_server.config import Settings, get_settings

logger = logging.getLogger(__name__)

_client: Any | None = None


def _configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(
        settings.langfuse_tracing
        and settings.langfuse_public_key
        and settings.langfuse_secret_key
        and settings.langfuse_host
    )


def initialize_langfuse(settings: Settings | None = None) -> Any | None:
    """Create the process-wide Langfuse client. Call once from app lifespan."""
    global _client
    settings = settings or get_settings()
    if not _configured(settings):
        _client = None
        if settings.langfuse_tracing:
            logger.warning("Langfuse tracing is on but keys/host are missing; prompts stay on local fallbacks")
        return None

    from langfuse import Langfuse

    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
    os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)
    os.environ.setdefault("LANGFUSE_BASE_URL", settings.langfuse_host)

    _client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        tracing_enabled=True,
        environment=settings.environment,
    )
    logger.info("Langfuse client ready host=%s env=%s", settings.langfuse_host, settings.environment)
    return _client


def get_langfuse() -> Any | None:
    return _client


def create_langfuse_callback() -> Any | None:
    if get_langfuse() is None:
        return None
    from langfuse.langchain import CallbackHandler

    return CallbackHandler()


def flush_langfuse() -> None:
    client = get_langfuse()
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        logger.debug("Langfuse flush failed", exc_info=True)


@contextmanager
def langfuse_generation_scope(
    *,
    name: str,
    model: str | None = None,
    input: Any = None,
    metadata: dict[str, Any] | None = None,
    prompt: Any = None,
) -> Iterator[Any | None]:
    """Nest a generation under the current Langfuse observation (tool / agent)."""
    client = get_langfuse()
    if client is None:
        yield None
        return

    kwargs: dict[str, Any] = {
        "name": name,
        "as_type": "generation",
        "input": input,
        "metadata": dict(metadata or {}),
    }
    if model:
        kwargs["model"] = model
    if prompt is not None:
        kwargs["prompt"] = prompt
    with client.start_as_current_observation(**kwargs) as observation:
        yield observation


@contextmanager
def langfuse_trace_scope(
    *,
    name: str,
    user_id: str | None = None,
    session_id: str | None = None,
    input: Any = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Iterator[Any | None]:
    client = get_langfuse()
    if client is None:
        yield None
        return

    from langfuse import propagate_attributes

    root_metadata = {key: value for key, value in dict(metadata or {}).items() if not str(key).startswith("langfuse_")}
    with client.start_as_current_observation(
        name=name,
        as_type="agent",
        input=input,
        metadata=root_metadata,
    ) as observation:
        with propagate_attributes(
            trace_name=name,
            user_id=user_id,
            session_id=session_id,
            metadata={str(key): str(value) for key, value in root_metadata.items() if value is not None},
            tags=list(tags or []),
        ):
            yield observation
