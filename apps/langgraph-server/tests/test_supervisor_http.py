from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.checkpoint.memory import InMemorySaver

from langgraph_server.core.supervisor.graph import build_supervisor_graph
from langgraph_server.main import create_app


class FakeToolChatModel(FakeListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture
def project_id():
    return uuid4()


@pytest.fixture
def thread_id():
    return uuid4()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, user_id, project_id, thread_id):
    async def fake_me(self, bearer_token: str):
        assert bearer_token == "user-token"
        return {"id": str(user_id), "displayName": "Pagol"}

    async def fake_snapshot(self, requested_project_id):
        assert str(requested_project_id) == str(project_id)
        return {
            "id": str(project_id),
            "userId": str(user_id),
            "threadId": str(thread_id),
            "name": "Shell 2024",
            "pendingSourcesCount": 0,
            "sources": [
                {
                    "id": str(uuid4()),
                    "title": "Shell AR 2024",
                    "originalFilename": "shell.pdf",
                    "processStatus": "completed",
                    "summary": "Annual report.",
                }
            ],
        }

    monkeypatch.setattr("langgraph_server.core.api_client.ApiClient.get_me", fake_me)
    monkeypatch.setattr("langgraph_server.core.api_client.ApiClient.get_project_snapshot", fake_snapshot)

    graph = build_supervisor_graph(
        InMemorySaver(),
        llm=FakeToolChatModel(responses=["Net profit was EUR 2.1 billion (shell.pdf, pp. 42–43)."]),
    )
    app = create_app(graph=graph)
    with TestClient(app) as test_client:
        yield test_client


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "OK"}


def test_stream_requires_bearer(client: TestClient, user_id, project_id, thread_id):
    response = client.post(
        "/supervisor/stream",
        json={
            "message": "What was net profit?",
            "user_id": str(user_id),
            "project_id": str(project_id),
            "thread_id": str(thread_id),
        },
    )
    assert response.status_code == 401


def test_stream_and_history(client: TestClient, user_id, project_id, thread_id):
    headers = {"Authorization": "Bearer user-token"}
    body = {
        "message": "What was net profit?",
        "user_id": str(user_id),
        "project_id": str(project_id),
        "thread_id": str(thread_id),
    }
    response = client.post("/supervisor/stream", json=body, headers=headers)
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "ConversationDelta" in response.text or "Net profit" in response.text

    history = client.get(
        "/supervisor/history",
        params={
            "thread_id": str(thread_id),
            "user_id": str(user_id),
            "project_id": str(project_id),
            "page": 1,
            "size": 20,
        },
        headers=headers,
    )
    assert history.status_code == 200
    payload = history.json()["data"]
    messages = payload["messages"]
    assert messages
    assert payload["page"] == 1
    assert payload["size"] == 20
    assert payload["total"] >= 1
    assert payload["pages"] >= 1
    assert messages[0]["role"] == "user"
    assert "net profit" in messages[0]["content"].lower()


def test_datapoints_run_requires_agent_secret(client: TestClient, user_id, project_id, monkeypatch):
    monkeypatch.setenv("AGENT_SECRET", "test-agent-secret")
    from langgraph_server.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_secret", "test-agent-secret")

    body = {
        "project_id": str(project_id),
        "document_id": str(uuid4()),
        "user_id": str(user_id),
        "file_name": "shell.pdf",
        "mode": "ready",
    }
    denied = client.post("/datapoints/run", json=body)
    assert denied.status_code == 401
