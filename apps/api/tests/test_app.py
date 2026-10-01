from uuid import uuid4

from fastapi.testclient import TestClient

from chat_api.main import app


def test_health():
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "OK"}


def test_spa_home_and_project_routes():
    with TestClient(app) as client:
        home = client.get("/")
        project = client.get(f"/project/{uuid4()}")
        styles = client.get("/styles.css")
        script = client.get("/app.js")
        config_js = client.get("/config.js")

    assert home.status_code == 200
    assert "text/html" in home.headers["content-type"]
    assert b"Annual Report Analyst" in home.content
    assert b'id="workspace"' in home.content

    assert project.status_code == 200
    assert b'id="workspace"' in project.content

    assert styles.status_code == 200
    assert "text/css" in styles.headers["content-type"]
    assert styles.headers.get("cache-control") == "no-store"

    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert script.headers.get("cache-control") == "no-store"

    assert config_js.status_code == 200
    assert "javascript" in config_js.headers["content-type"]
    assert b"langgraphBaseUrl" in config_js.content
    assert config_js.headers.get("cache-control") == "no-store"


def test_me_requires_bearer():
    with TestClient(app) as client:
        response = client.get("/api/v1/me")
    assert response.status_code in {401, 403}
