from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from langgraph.config import get_stream_writer as get_langgraph_stream_writer
from pydantic import BaseModel

_suppress_stream: ContextVar[bool] = ContextVar("suppress_stream", default=False)


def stream_writes_suppressed() -> bool:
    return bool(_suppress_stream.get())


@contextmanager
def suppress_stream_writes(suppress: bool = True) -> Iterator[None]:
    """Mute custom SSE writes (e.g. ui_hidden turns still checkpoint without streaming)."""
    token = _suppress_stream.set(bool(suppress))
    try:
        yield
    finally:
        _suppress_stream.reset(token)


def get_stream_writer():
    stream_writer = get_langgraph_stream_writer()

    def custom_writer(event: BaseModel | dict[str, Any]) -> Any:
        if stream_writes_suppressed():
            return None
        payload = event.model_dump(mode="json") if isinstance(event, BaseModel) else event
        return stream_writer(payload)

    return custom_writer


def emit_stream_event(event: BaseModel | dict[str, Any]) -> None:
    """Write a custom SSE event. No-op when suppressed or outside a LangGraph run."""
    if stream_writes_suppressed():
        return
    try:
        get_stream_writer()(event)
    except Exception:
        return
