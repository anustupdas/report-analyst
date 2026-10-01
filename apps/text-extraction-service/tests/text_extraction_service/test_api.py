from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from text_extraction_service.api_v1 import text_extraction_router as router_module
from text_extraction_service.app import app
from text_extraction_service.service import TextExtractionService
from text_extraction_service.utils import ServiceConfig, initialize_configuration


@pytest.fixture
def local_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ServiceConfig:
    monkeypatch.setenv("USE_S3", "false")
    monkeypatch.setenv("USE_MISTRAL", "false")
    monkeypatch.setenv("PROMETHEUS_ENABLED", "true")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REMOTE_CONFIG_URL", str(Path("configs/staging/config.yml").resolve()))
    initialize_configuration.cache_clear()
    router_module._service_instance = None

    config = ServiceConfig(
        {
            "storage.useS3": False,
            "mistral.enabled": False,
            "mistral.apiKey": None,
            "mistral.version": "mistral-ocr-4-1",
            "data.location": str(tmp_path),
            "files.supportedFormats": ["pdf", "png", "jpg", "jpeg", "txt", "md"],
            "files.imageFormats": ["png", "jpg", "jpeg"],
            "storage.bucket": None,
        }
    )
    config.validate_runtime()
    return config


@pytest.fixture
def client(local_config: ServiceConfig) -> TestClient:
    service = TextExtractionService(config=local_config)
    app.dependency_overrides[router_module.get_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    router_module._service_instance = None
    initialize_configuration.cache_clear()


def _make_pdf(path: Path, *texts: str) -> None:
    doc = pymupdf.open()
    for text in texts:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "OK"}


def test_version(client: TestClient) -> None:
    response = client.get("/version")
    assert response.status_code == 200
    assert "version" in response.json()


def test_extract_local_pdf(client: TestClient, local_config: ServiceConfig) -> None:
    pdf_path = local_config.local_data_dir / "hello.pdf"
    _make_pdf(pdf_path, "Hello extraction")

    response = client.post(
        "/api/v1/extract/file",
        json={"file_ref": "hello.pdf", "use_ocr": False},
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert "Hello extraction" in body["text"]
    assert body["method"] == "pymupdf"
    assert body["text_format"] == "plain"
    assert body["storage"] == "local"


def test_extract_returns_pages(client: TestClient, local_config: ServiceConfig) -> None:
    _make_pdf(local_config.local_data_dir / "multi.pdf", "First page", "", "Third page")

    response = client.post("/api/v1/extract/file", json={"file_ref": "multi.pdf"})
    assert response.status_code == 200
    body = response.json()["data"]
    assert [(page["page_number"], page["text"]) for page in body["pages"]] == [(1, "First page"), (3, "Third page")]
    assert body["pages"][0]["dimensions"] == {"width": 595, "height": 842, "dpi": 72}
    assert body["text"] == "First page\n\nThird page"
    assert body["model"] is None
    assert body["usage"] == {"pages_processed": 2}


def test_image_rejected_when_mistral_off(client: TestClient, local_config: ServiceConfig) -> None:
    image_path = local_config.local_data_dir / "photo.png"
    image_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    response = client.post("/api/v1/extract/file", json={"file_ref": "photo.png"})
    assert response.status_code == 422
    assert "Mistral" in response.json()["detail"]


def test_metrics_on_service_port(client: TestClient, local_config: ServiceConfig) -> None:
    _make_pdf(local_config.local_data_dir / "metrics.pdf", "Metrics page")
    extracted = client.post("/api/v1/extract/file", json={"file_ref": "metrics.pdf"})
    assert extracted.status_code == 200

    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    assert 'text_extraction_requests_total{extension="pdf",method="pymupdf",status="ok"}' in body
    assert "text_extraction_duration_seconds" in body
    assert "text_extraction_pages_total" in body


def test_metrics_records_unsupported_format(client: TestClient, local_config: ServiceConfig) -> None:
    (local_config.local_data_dir / "notes.docx").write_bytes(b"not a pdf")
    response = client.post("/api/v1/extract/file", json={"file_ref": "notes.docx"})
    assert response.status_code == 415
    body = client.get("/metrics").text
    assert 'text_extraction_requests_total{extension="docx",method="unknown",status="error"}' in body


def test_metrics_can_be_disabled(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMETHEUS_ENABLED", "false")
    initialize_configuration.cache_clear()
    response = client.get("/metrics")
    assert response.status_code == 404
    initialize_configuration.cache_clear()


def test_missing_file(client: TestClient) -> None:
    response = client.post(
        "/api/v1/extract/file",
        json={"file_ref": "does-not-exist.pdf"},
    )
    assert response.status_code == 404
