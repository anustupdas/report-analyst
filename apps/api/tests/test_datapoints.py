from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from chat_api.modules.vectors import merge_key_datapoints, update_document_datapoints
from chat_api.workflows.ingest import (
    _normalize_datapoints_mode,
    refresh_document_datapoints,
)


def test_update_document_datapoints_writes_json(monkeypatch: pytest.MonkeyPatch):
    doc_id = uuid4()
    project_id = uuid4()
    user_id = uuid4()
    document = SimpleNamespace(
        id=doc_id,
        project_id=project_id,
        user_id=user_id,
        status="completed",
        updated_at=None,
        key_datapoints=None,
    )
    monkeypatch.setattr(
        "chat_api.modules.vectors.get_owned_project",
        lambda db, **kwargs: SimpleNamespace(id=project_id, user_id=user_id),
    )
    monkeypatch.setattr(
        "chat_api.modules.vectors.get_project_document",
        lambda db, **kwargs: document,
    )
    result = update_document_datapoints(
        MagicMock(),
        user_id=user_id,
        project_id=project_id,
        document_id=doc_id,
        key_datapoints={"fte": {"value": 42}, "sustainability_goals": []},
    )
    assert document.key_datapoints == {"fte": {"value": 42}, "sustainability_goals": []}
    assert result["keyDatapoints"]["fte"]["value"] == 42


def test_update_document_datapoints_merges_without_wiping_fte(monkeypatch: pytest.MonkeyPatch):
    doc_id = uuid4()
    project_id = uuid4()
    user_id = uuid4()
    document = SimpleNamespace(
        id=doc_id,
        project_id=project_id,
        user_id=user_id,
        status="completed",
        updated_at=None,
        key_datapoints={"fte": {"value": 100}, "sustainability_goals": []},
    )
    monkeypatch.setattr(
        "chat_api.modules.vectors.get_owned_project",
        lambda db, **kwargs: SimpleNamespace(id=project_id, user_id=user_id),
    )
    monkeypatch.setattr(
        "chat_api.modules.vectors.get_project_document",
        lambda db, **kwargs: document,
    )
    result = update_document_datapoints(
        MagicMock(),
        user_id=user_id,
        project_id=project_id,
        document_id=doc_id,
        key_datapoints={"fte": None, "sustainability_goals": [{"label": "Net zero", "deadline": "2050"}]},
    )
    assert result["keyDatapoints"]["fte"]["value"] == 100
    assert result["keyDatapoints"]["sustainability_goals"][0]["label"] == "Net zero"


def test_merge_key_datapoints_clears_status_note_when_filled():
    merged = merge_key_datapoints(
        {"fte": None, "sustainability_goals": [], "status_note": "empty early pages"},
        {"fte": {"value": 100}, "sustainability_goals": []},
    )
    assert merged["fte"]["value"] == 100
    assert "status_note" not in merged


def test_langgraph_client_run_posts_datapoints_run(monkeypatch: pytest.MonkeyPatch):
    from chat_api.services.langgraph_datapoints import LangGraphDatapointsClient

    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"ok": True, "keyDatapoints": {"fte": {"value": 1}, "sustainability_goals": []}}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("chat_api.services.langgraph_datapoints.httpx.post", fake_post)
    client = LangGraphDatapointsClient(SimpleNamespace(langgraph_url="http://lg:8080", agent_secret="secret"))
    doc_id = uuid4()
    out = client.run(
        project_id=uuid4(),
        document_id=doc_id,
        user_id=uuid4(),
        file_name="x.pdf",
        mode="ready",
    )
    assert out and out["ok"] is True
    assert captured["url"].endswith("/datapoints/run")
    assert captured["json"]["mode"] == "ready"
    assert captured["json"]["document_id"] == str(doc_id)
    assert captured["headers"]["X-Agent-Secret"] == "secret"


def test_normalize_datapoints_mode_keeps_ready():
    assert _normalize_datapoints_mode("ready") == "ready"
    assert _normalize_datapoints_mode("refresh") == "refresh"
    assert _normalize_datapoints_mode("completed") == "completed"
    assert _normalize_datapoints_mode("weird") == "completed"


def test_refresh_document_datapoints_forwards_ready_mode(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    def fake_trigger(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "keyDatapoints": {"fte": None, "sustainability_goals": []}}

    monkeypatch.setattr(
        "chat_api.workflows.ingest._trigger_completed_datapoints_pass",
        fake_trigger,
    )
    doc_id = uuid4()
    out = refresh_document_datapoints(
        project_id=uuid4(),
        document_id=doc_id,
        user_id=uuid4(),
        thread_id=uuid4(),
        file_name="report.pdf",
        mode="ready",
    )
    assert out and out["ok"] is True
    assert captured["mode"] == "ready"
    assert captured["document_id"] == doc_id
    assert captured["wait_for_slot"] is True


def test_datapoints_client_uses_internal_url_when_set():
    from chat_api.services.langgraph_datapoints import LangGraphDatapointsClient

    client = LangGraphDatapointsClient(
        SimpleNamespace(
            langgraph_url="http://localhost:8080",
            langgraph_internal_url="http://langgraph:8080",
            agent_secret="s",
        )
    )
    assert client._base == "http://langgraph:8080"


def test_datapoints_client_falls_back_to_browser_url():
    from chat_api.services.langgraph_datapoints import LangGraphDatapointsClient

    client = LangGraphDatapointsClient(
        SimpleNamespace(
            langgraph_url="http://localhost:8080",
            langgraph_internal_url="",
            agent_secret="s",
        )
    )
    assert client._base == "http://localhost:8080"
