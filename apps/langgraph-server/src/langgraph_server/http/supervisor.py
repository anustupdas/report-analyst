from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph

from langgraph_server.config import Settings, get_settings
from langgraph_server.core.langfuse_client import create_langfuse_callback, flush_langfuse, langfuse_trace_scope
from langgraph_server.core.project_context import HistoryMessage, SupervisorRequest
from langgraph_server.http.auth import load_authorized_snapshot, require_user, snapshot_configurable
from langgraph_server.http.sse import model_to_sse_str, sse_keepalive

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/supervisor")


def _graph(request: Request) -> CompiledStateGraph:
    graph = getattr(request.app.state, "supervisor_graph", None)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Supervisor graph is not ready")
    return graph


def _history_messages(raw_messages: list[Any]) -> list[HistoryMessage]:
    history: list[HistoryMessage] = []
    for message in raw_messages:
        message_type = getattr(message, "type", None)
        content = getattr(message, "content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        metadata = getattr(message, "response_metadata", None) or {}
        additional = getattr(message, "additional_kwargs", None) or {}
        ui_hidden = bool(
            (isinstance(metadata, dict) and metadata.get("ui_hidden"))
            or (isinstance(additional, dict) and additional.get("ui_hidden"))
        )
        if ui_hidden:
            continue
        events = metadata.get("events") if isinstance(metadata, dict) else None
        event_list = [item for item in events if isinstance(item, dict)] if isinstance(events, list) else []
        if message_type == "human":
            history.append(HistoryMessage(role="user", content=content))
        elif message_type == "ai":
            history.append(HistoryMessage(role="assistant", content=content, events=event_list))
    return history


def _paginate_newest_first(messages: list[HistoryMessage], *, page: int, size: int) -> dict[str, Any]:
    """Page history newest first, and keep chronological order within each page."""
    total = len(messages)
    pages = max(1, (total + size - 1) // size) if total else 0
    if total == 0:
        return {"messages": [], "total": 0, "page": page, "size": size, "pages": 0}

    page = max(1, min(page, pages))
    rev = list(reversed(messages))
    start = (page - 1) * size
    end = start + size
    page_items = list(reversed(rev[start:end]))
    return {
        "messages": [item.model_dump() for item in page_items],
        "total": total,
        "page": page,
        "size": size,
        "pages": pages,
    }


async def message_generator(
    graph: CompiledStateGraph,
    kwargs: dict[str, Any],
    keepalive_s: float,
    *,
    user_id: str,
    thread_id: str,
    project_id: str,
    message: str,
) -> AsyncGenerator[str, None]:
    cancelled = False
    try:
        with langfuse_trace_scope(
            name="supervisor",
            user_id=user_id,
            session_id=thread_id,
            input=message,
            metadata={"user_id": user_id, "project_id": project_id, "thread_id": thread_id},
            tags=["supervisor"],
        ):
            stream_iter = graph.astream(**kwargs, stream_mode=["custom"], subgraphs=True, durability="exit")
            next_event: asyncio.Task[Any] = asyncio.create_task(anext(stream_iter))  # type: ignore[arg-type]
            try:
                while True:
                    done, _ = await asyncio.wait({next_event}, timeout=keepalive_s)
                    if not done:
                        yield sse_keepalive()
                        continue
                    try:
                        event = next_event.result()
                    except StopAsyncIteration:
                        break
                    event_tuple = cast(tuple[Any, Any, dict[str, Any]], event)
                    yield model_to_sse_str(event_tuple[2])
                    next_event = asyncio.create_task(anext(stream_iter))  # type: ignore[arg-type]
            finally:
                if not next_event.done():
                    next_event.cancel()
                    with suppress(asyncio.CancelledError):
                        await next_event
                aclose = getattr(stream_iter, "aclose", None)
                if aclose is not None:
                    with suppress(Exception):
                        await aclose()
    except (asyncio.CancelledError, GeneratorExit):
        cancelled = True
        raise
    except Exception:
        logger.exception("Supervisor stream failed")
        yield "event: error\ndata: " + json.dumps({"type": "error", "content": "Internal server error"}) + "\n\n"
    finally:
        flush_langfuse()
        if not cancelled:
            yield "event: END\ndata: [DONE]\n\n"


@router.post("/stream", response_class=StreamingResponse)
async def supervisor_stream(
    body: SupervisorRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    auth: Annotated[tuple[str, dict[str, Any]], Depends(require_user)],
) -> StreamingResponse:
    _token, user = auth
    snapshot = await load_authorized_snapshot(
        settings=settings,
        token_user=user,
        user_id=body.user_id,
        project_id=body.project_id,
        thread_id=body.thread_id,
    )
    extra = snapshot_configurable(snapshot, user=user)
    callbacks = []
    handler = create_langfuse_callback()
    if handler is not None:
        callbacks.append(handler)
    config = {
        "configurable": {
            "thread_id": str(body.thread_id),
            "user_id": str(body.user_id),
            "project_id": str(body.project_id),
            "ui_hidden": bool(body.hidden) or body.mode == "datapoints",
            "mode": body.mode,
            "prompt_variant": body.prompt,
            "document_id": str(body.document_id) if body.document_id else None,
            "document_name": (body.document_name or "").strip() or None,
            **extra,
        },
        "callbacks": callbacks,
        "metadata": {
            "user_id": str(body.user_id),
            "project_id": str(body.project_id),
            "thread_id": str(body.thread_id),
            "ui_hidden": bool(body.hidden) or body.mode == "datapoints",
            "mode": body.mode,
            "prompt_variant": body.prompt,
            "document_id": str(body.document_id) if body.document_id else None,
            "document_name": (body.document_name or "").strip() or None,
        },
        "run_name": "supervisor",
    }
    human_kwargs: dict[str, Any] = {}
    if body.hidden:
        human_kwargs["additional_kwargs"] = {"ui_hidden": True}
        human_kwargs["response_metadata"] = {"ui_hidden": True}
    kwargs = {
        "input": {"messages": [HumanMessage(content=body.message.strip(), **human_kwargs)]},
        "config": config,
    }
    return StreamingResponse(
        message_generator(
            _graph(request),
            kwargs,
            settings.sse_keepalive_seconds,
            user_id=str(body.user_id),
            thread_id=str(body.thread_id),
            project_id=str(body.project_id),
            message=body.message.strip(),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/history")
async def supervisor_history(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    auth: Annotated[tuple[str, dict[str, Any]], Depends(require_user)],
    thread_id: UUID,
    user_id: UUID,
    project_id: UUID,
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """Return chat history paged newest→oldest (page=1 is the latest block).

    Within a page, messages stay chronological (oldest→newest) for natural reading.
    """
    if page < 1:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="page must be >= 1")
    if size < 1 or size > 100:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="size must be between 1 and 100")

    _token, user = auth
    await load_authorized_snapshot(
        settings=settings,
        token_user=user,
        user_id=user_id,
        project_id=project_id,
        thread_id=thread_id,
    )
    graph = _graph(request)
    snapshot = await graph.aget_state({"configurable": {"thread_id": str(thread_id)}})
    values = getattr(snapshot, "values", None) or {}
    messages = _history_messages(list(values.get("messages") or []))
    return {"data": _paginate_newest_first(messages, page=page, size=size)}
