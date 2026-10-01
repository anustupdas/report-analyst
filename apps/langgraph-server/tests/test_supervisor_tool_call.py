from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import PrivateAttr

from langgraph_server.core.supervisor.graph import build_supervisor_graph


class SequentialToolModel(BaseChatModel):
    document_id: str
    _n: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "sequential-tool"

    def bind_tools(self, tools: Any, **kwargs: Any) -> SequentialToolModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self._n += 1
        if self._n == 1:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "report-search-tool",
                        "args": {"query": "net profit", "document_id": self.document_id},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            )
        else:
            message = AIMessage(content="Net profit was EUR 2.1 billion (shell.pdf, pp. 42–43).")
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop=stop, run_manager=run_manager)


@pytest.mark.asyncio
async def test_supervisor_passes_project_filters_into_search(monkeypatch: pytest.MonkeyPatch):
    doc_id = str(uuid4())
    project_id = str(uuid4())
    seen: dict[str, Any] = {}

    async def fake_search(self, **kwargs):
        seen.update(kwargs)
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

    graph = build_supervisor_graph(InMemorySaver(), llm=SequentialToolModel(document_id=doc_id))
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="What was net profit?")]},
        config={
            "configurable": {
                "thread_id": str(uuid4()),
                "project_id": project_id,
                "usable_document_ids": [doc_id],
                "document_catalog": {doc_id: {"originalFilename": "shell.pdf"}},
                "empty_project": False,
            }
        },
    )

    assert seen.get("query") == "net profit"
    assert str(seen.get("document_id")) == doc_id
    assert str(seen.get("project_id")) == project_id
    texts = [getattr(message, "content", "") for message in result["messages"]]
    assert any("EUR 2.1 billion" in str(text) for text in texts)
    assert not any(isinstance(message, ToolMessage) for message in result["messages"])
    assert not any(isinstance(message, ToolMessage) for message in result.get("model_context_messages") or [])

    assistant = next(
        message
        for message in result["messages"]
        if getattr(message, "type", None) == "ai" and "EUR 2.1 billion" in str(getattr(message, "content", ""))
    )
    events = (getattr(assistant, "response_metadata", None) or {}).get("events") or []
    assert events
    assert events[0]["type"] == "ReportSearched"
    assert events[0]["sources"][0]["filename"] == "shell.pdf"
    assert events[0]["sources"][0]["page_start"] == 42
