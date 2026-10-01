from __future__ import annotations

from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from langgraph_server.core.project_context import ReportSearched, ReportSearchSource
from langgraph_server.core.supervisor.graph import _attach_events_to_conversation, _collect_tool_events
from langgraph_server.http.supervisor import _history_messages


def test_collect_tool_events_from_artifacts():
    searched = ReportSearched(
        id=uuid4(),
        query="risks",
        document_id=str(uuid4()),
        limit=8,
        count=1,
        sources=[ReportSearchSource(filename="apple.pdf", page_start=8, page_end=8, section="Item 1A")],
    )
    messages = [
        HumanMessage(content="what are the risks?"),
        AIMessage(content="", tool_calls=[{"name": "report-search-tool", "args": {}, "id": "c1", "type": "tool_call"}]),
        ToolMessage(content='{"ok": true}', tool_call_id="c1", artifact=searched),
        AIMessage(content="Here are the risks."),
    ]
    events = _collect_tool_events(messages)
    assert len(events) == 1
    assert events[0]["type"] == "ReportSearched"
    assert events[0]["sources"][0]["filename"] == "apple.pdf"
    assert "timestamp" in events[0]


def test_attach_events_to_final_assistant_message():
    messages = [AIMessage(content="Answer text", response_metadata={"timestamp": 123})]
    events = [{"type": "ReportSearched", "query": "risks", "sources": []}]
    updated = _attach_events_to_conversation(messages, events)
    assert len(updated) == 1
    meta = updated[0].response_metadata
    assert meta["events"] == events
    assert meta["stream_type"] == ["content", "response_metadata"]
    assert meta["timestamp"] == 123


def test_history_messages_include_response_metadata_events():
    events = [
        {
            "type": "ReportSearched",
            "id": str(uuid4()),
            "query": "risks",
            "sources": [{"filename": "apple.pdf", "page_start": 8, "page_end": 8}],
        }
    ]
    raw = [
        HumanMessage(content="what are the risks from apple?"),
        AIMessage(content="Apple's main risks...", response_metadata={"events": events}),
    ]
    history = _history_messages(raw)
    assert history[0].role == "user"
    assert history[0].events == []
    assert history[1].role == "assistant"
    assert history[1].events == events
    assert history[1].model_dump()["events"][0]["sources"][0]["filename"] == "apple.pdf"


def test_history_messages_skip_ui_hidden():
    raw = [
        HumanMessage(
            content="visible user turn",
        ),
        AIMessage(content="visible answer"),
        HumanMessage(
            content="Tell me the FTE details",
            additional_kwargs={"ui_hidden": True},
            response_metadata={"ui_hidden": True},
        ),
        AIMessage(
            content="Internal datapoints pass",
            response_metadata={"ui_hidden": True},
        ),
    ]
    history = _history_messages(raw)
    assert len(history) == 2
    assert history[0].content == "visible user turn"
    assert history[1].content == "visible answer"
