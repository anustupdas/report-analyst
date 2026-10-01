from __future__ import annotations

import json
from uuid import uuid4

import pytest
from langchain_core.runnables import RunnableConfig

from langgraph_server.tool.report_search import NO_SOURCES_MESSAGE, UNKNOWN_DOCUMENT_MESSAGE, report_search_tool
from langgraph_server.tool.search_budget import (
    SEARCH_BUDGET_EXCEEDED_REASON,
    reset_report_search_budget,
)


@pytest.fixture(autouse=True)
def _reset_search_budget():
    reset_report_search_budget()
    yield
    reset_report_search_budget()


def _payload(content: str) -> dict:
    data = json.loads(content)
    assert isinstance(data, dict)
    return data


@pytest.mark.asyncio
async def test_report_search_refuses_empty_project():
    config = RunnableConfig(configurable={"usable_document_ids": [], "project_id": str(uuid4())})
    content = await report_search_tool.ainvoke(
        {"query": "net profit", "document_id": str(uuid4())},
        config=config,
    )
    result = _payload(content)
    assert result["ok"] is False
    assert result["reason"] == "no_sources"
    assert result["message"] == NO_SOURCES_MESSAGE


@pytest.mark.asyncio
async def test_report_search_rejects_unknown_document_id():
    known = str(uuid4())
    config = RunnableConfig(configurable={"usable_document_ids": [known], "project_id": str(uuid4())})
    content = await report_search_tool.ainvoke(
        {"query": "net profit", "document_id": str(uuid4())},
        config=config,
    )
    result = _payload(content)
    assert result["ok"] is False
    assert result["reason"] == "unknown_document"
    assert result["message"] == UNKNOWN_DOCUMENT_MESSAGE


@pytest.mark.asyncio
async def test_report_search_returns_structured_matches_and_artifact(monkeypatch: pytest.MonkeyPatch):
    doc_id = str(uuid4())
    project_id = str(uuid4())

    async def fake_search(self, **kwargs):
        return {
            "matches": [
                {
                    "documentId": doc_id,
                    "content": "Net profit was EUR 2.1 billion.",
                    "section": "Financial review",
                    "pageStart": 42,
                    "pageEnd": 43,
                    "score": 0.91,
                    "originalFilename": "shell.pdf",
                }
            ]
        }

    monkeypatch.setattr("langgraph_server.core.api_client.ApiClient.search_chunks", fake_search)
    monkeypatch.setattr(
        "langgraph_server.tool.report_search._tool_configurable",
        lambda runtime: {
            "usable_document_ids": [doc_id],
            "project_id": project_id,
            "document_catalog": {doc_id: {"originalFilename": "shell.pdf", "title": "Shell AR"}},
        },
    )

    content, artifact = await report_search_tool._arun(query="net profit", document_id=doc_id)
    result = _payload(content)
    assert result["ok"] is True
    assert result["query"] == "net profit"
    assert result["document_id"] == doc_id
    assert result["count"] == 1
    assert result["matches"] == [
        {
            "document_id": doc_id,
            "filename": "shell.pdf",
            "section": "Financial review",
            "page_start": 42,
            "page_end": 43,
            "score": 0.91,
            "content": "Net profit was EUR 2.1 billion.",
        }
    ]
    assert artifact is not None
    assert artifact.type == "ReportSearched"
    assert artifact.count == 1
    assert artifact.sources[0].filename == "shell.pdf"


@pytest.mark.asyncio
async def test_report_search_emits_started_and_searched_events(monkeypatch: pytest.MonkeyPatch):
    doc_id = str(uuid4())
    project_id = str(uuid4())
    events: list[object] = []

    async def fake_search(self, **kwargs):
        return {
            "matches": [
                {
                    "documentId": doc_id,
                    "content": "Net profit was EUR 2.1 billion.",
                    "section": "Financial review",
                    "pageStart": 42,
                    "pageEnd": 43,
                    "score": 0.91,
                    "originalFilename": "shell.pdf",
                }
            ]
        }

    monkeypatch.setattr("langgraph_server.core.api_client.ApiClient.search_chunks", fake_search)
    monkeypatch.setattr("langgraph_server.tool.report_search.emit_stream_event", events.append)
    monkeypatch.setattr(
        "langgraph_server.tool.report_search._tool_configurable",
        lambda runtime: {
            "usable_document_ids": [doc_id],
            "project_id": project_id,
            "document_catalog": {doc_id: {"originalFilename": "shell.pdf"}},
        },
    )

    content, artifact = await report_search_tool._arun(query="net profit", document_id=doc_id, limit=8)
    assert [event.type for event in events] == ["ReportSearchStarted", "ReportSearched"]
    started, searched = events
    assert started.query == "net profit"
    assert started.document_id == doc_id
    assert started.limit == 8
    assert searched.id == started.id
    assert searched.count == 1
    assert searched.sources[0].filename == "shell.pdf"
    assert searched.sources[0].page_start == 42
    assert searched.sources[0].page_end == 43
    assert searched.sources[0].section == "Financial review"
    assert artifact is not None
    assert artifact.id == searched.id
    assert _payload(content)["ok"] is True


@pytest.mark.asyncio
async def test_report_search_enforces_per_turn_budget(monkeypatch: pytest.MonkeyPatch):
    doc_id = str(uuid4())
    project_id = str(uuid4())

    async def fake_search(self, **kwargs):
        return {
            "matches": [
                {
                    "documentId": doc_id,
                    "content": "Net profit was EUR 2.1 billion.",
                    "pageStart": 1,
                    "pageEnd": 1,
                    "score": 0.9,
                    "originalFilename": "shell.pdf",
                }
            ]
        }

    monkeypatch.setattr("langgraph_server.core.api_client.ApiClient.search_chunks", fake_search)
    monkeypatch.setattr(
        "langgraph_server.tool.report_search._tool_configurable",
        lambda runtime: {
            "usable_document_ids": [doc_id],
            "project_id": project_id,
            "document_catalog": {doc_id: {"originalFilename": "shell.pdf"}},
        },
    )
    monkeypatch.setattr(
        "langgraph_server.tool.search_budget.get_settings",
        lambda: type("S", (), {"supervisor_max_report_searches_per_turn": 2})(),
    )

    reset_report_search_budget()
    ok1, _ = await report_search_tool._arun(query="q1", document_id=doc_id)
    ok2, _ = await report_search_tool._arun(query="q2", document_id=doc_id)
    blocked, artifact = await report_search_tool._arun(query="q3", document_id=doc_id)

    assert _payload(ok1)["ok"] is True
    assert _payload(ok2)["ok"] is True
    result = _payload(blocked)
    assert result["ok"] is False
    assert result["reason"] == SEARCH_BUDGET_EXCEEDED_REASON
    assert "2/2" in result["message"]
    assert artifact is None
