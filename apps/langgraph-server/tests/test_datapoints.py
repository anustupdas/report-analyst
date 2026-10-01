from __future__ import annotations

from langchain_core.messages import ToolMessage

from langgraph_server.core.key_datapoints import job_message, pages_from_messages, search_context_from_messages
from langgraph_server.core.supervisor.constants import DETAIL_EXTRACTOR_PROMPT, PROMPT_NAME
from langgraph_server.prompt.loader import load_prompt
from langgraph_server.tool.datapoints_extract import (
    FteDatapoint,
    KeyDatapoints,
    is_empty_datapoints,
)


def test_supervisor_prompt_branches_load():
    main = load_prompt("supervisor_agent", "supervisor", PROMPT_NAME)
    detail = load_prompt("supervisor_agent", "supervisor", DETAIL_EXTRACTOR_PROMPT)
    extractor = load_prompt("supervisor_agent", "extractor", "main")
    assert main.source == "fallback"
    assert detail.source == "fallback"
    assert extractor.source == "fallback"
    assert extractor.model_config.max_output_tokens == 8000
    assert "report-search-tool" in detail.compile(max_report_searches=3)[0]["content"]
    assert "key datapoints" in detail.compile(max_report_searches=3)[0]["content"].lower()


def test_job_message_ready_vs_completed():
    ready = job_message(document_id="d1", file_name="a.pdf", mode="ready")
    done = job_message(document_id="d1", file_name="a.pdf", mode="completed")
    assert "ready" in ready.lower()
    assert "document_id: d1" in ready
    assert "completed/refresh" in done.lower()


def test_is_empty_datapoints():
    assert is_empty_datapoints(KeyDatapoints()) is True
    assert is_empty_datapoints(KeyDatapoints(fte=FteDatapoint(value=1))) is False


def test_search_context_from_tool_messages():
    msg = ToolMessage(
        content='{"ok": true, "matches": [{"page_start": 4, "page_end": 4, "section": "People", "content": "364,000 people"}]}',
        tool_call_id="1",
    )
    context = search_context_from_messages([msg])
    assert "364,000" in context
    assert pages_from_messages([msg]) == [4]


def test_supervisor_request_datapoints_forces_prompt_and_hidden():
    from uuid import uuid4

    from langgraph_server.core.project_context import SupervisorRequest

    body = SupervisorRequest(
        message="extract",
        thread_id=uuid4(),
        user_id=uuid4(),
        project_id=uuid4(),
        mode="datapoints",
        prompt="main",
    )
    assert body.prompt == "detail-extractor"
    assert body.hidden is True
