from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from langgraph_server.core.supervisor.graph import (
    _build_model_context_messages,
    _summarize_model_context_messages,
    _without_tool_payloads,
    run_supervisor_turn,
)
from langgraph_server.core.supervisor.state import SupervisorGraphState


def test_without_tool_payloads_drops_tool_json_and_tool_calls():
    human = HumanMessage(content="What was net profit?")
    search_call = AIMessage(content="", tool_calls=[{"name": "report-search-tool", "args": {}, "id": "c1"}])
    tool = ToolMessage(content='{"ok": true, "matches": []}', tool_call_id="c1")
    spoken = AIMessage(content="Net profit was EUR 2.1 billion.")
    kept = _without_tool_payloads([human, search_call, tool, spoken])
    assert kept == [human, spoken]


def test_build_model_context_messages_appends_new_transcript_messages():
    summary = HumanMessage(content="[COMPRESSED_CONTEXT]\n- Earlier ABN AMRO discussion")
    recent = AIMessage(content="Previous visible answer")
    new_message = HumanMessage(content="What about risks?")
    state = SupervisorGraphState(
        messages=[
            HumanMessage(content="Old question"),
            AIMessage(content="Old answer"),
            new_message,
        ],
        model_context_messages=[summary, recent],
        model_context_transcript_count=2,
    )
    assert _build_model_context_messages(state) == [summary, recent, new_message]


@pytest.mark.asyncio
async def test_summarize_model_context_messages_falls_back_when_abefore_model_raises():
    messages = [HumanMessage(content="hello")]
    middleware = MagicMock()
    middleware.abefore_model = AsyncMock(side_effect=RuntimeError("summarizer down"))
    assert await _summarize_model_context_messages(messages, middleware) == messages


@pytest.mark.asyncio
async def test_summarize_model_context_messages_falls_back_when_everything_removed():
    messages = [HumanMessage(content="hello")]
    middleware = MagicMock()
    middleware.abefore_model = AsyncMock(return_value={"messages": [RemoveMessage(id="__remove_all__")]})
    assert await _summarize_model_context_messages(messages, middleware) == messages


@pytest.mark.asyncio
async def test_run_supervisor_turn_uses_summarized_context_and_omits_tools(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, list] = {}
    summary = HumanMessage(content="[COMPRESSED_CONTEXT]\n- Earlier context")
    recent = HumanMessage(content="What about risks?")
    middleware = MagicMock()
    middleware.abefore_model = AsyncMock(
        return_value={"messages": [RemoveMessage(id="__remove_all__"), summary, recent]}
    )

    class FakeAgent:
        _summarization_middleware = middleware

        async def astream_events(self, *args, **kwargs):
            captured["messages"] = kwargs["input"]["messages"]
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content="Apple lists recession risk.")},
            }
            yield {
                "event": "on_chain_end",
                "data": {"output": {"messages": [ToolMessage(content="ignored", tool_call_id="x")]}},
            }

    monkeypatch.setattr(
        "langgraph_server.core.supervisor.graph.get_config",
        lambda: RunnableConfig(configurable={"thread_id": str(uuid4())}),
    )
    monkeypatch.setattr("langgraph_server.core.supervisor.graph.get_stream_writer", lambda: (lambda _event: None))

    original = [HumanMessage(content=f"turn {i}") for i in range(8)]
    original.append(recent)
    result = await run_supervisor_turn(SupervisorGraphState(messages=original), FakeAgent())

    assert captured["messages"] == [summary, recent]
    assert result["messages"][0].content == "Apple lists recession risk."
    assert not any(isinstance(message, ToolMessage) for message in result["messages"])
    assert result["model_context_messages"] == [summary, recent, result["messages"][0]]
    assert result["model_context_transcript_count"] == len(original) + 1
    assert not any(isinstance(message, ToolMessage) for message in result["model_context_messages"])
