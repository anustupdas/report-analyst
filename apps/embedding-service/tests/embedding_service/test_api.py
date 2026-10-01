import threading
import time

import pytest
from fastapi.testclient import TestClient

from embedding_service.api_v1 import router as router_module
from embedding_service.app import app
from embedding_service.pool import DedicatedEncodePool
from embedding_service.service import EmbeddingService, apply_input_prefix
from embedding_service.utils import ServiceConfig, initialize_configuration
from embedding_service.version import __version__


class FakeEncoder:
    dimension = 768

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.lock = threading.Lock()

    def encode(self, texts: list[str]) -> list[list[float]]:
        with self.lock:
            self.seen.extend(texts)
        vectors = []
        for text in texts:
            vec = [0.0] * self.dimension
            vec[0] = 1.0 if text.startswith("query:") else 0.5
            vec[1] = min(len(text), 100) / 100.0
            vectors.append(vec)
        return vectors


class BlockingEncoder:
    """Passage encodes wait on an Event so tests can prove query stays independent."""

    dimension = 768

    def __init__(self, *, lane: str) -> None:
        self.lane = lane
        self.seen: list[str] = []
        self.lock = threading.Lock()
        self.block_passage = threading.Event()
        self.passage_started = threading.Event()

    def encode(self, texts: list[str]) -> list[list[float]]:
        if self.lane == "passage":
            self.passage_started.set()
            assert self.block_passage.wait(timeout=5)
        with self.lock:
            self.seen.extend(texts)
        vectors = []
        for text in texts:
            vec = [0.0] * self.dimension
            vec[0] = 0.5
            vec[1] = min(len(text), 100) / 100.0
            vectors.append(vec)
        return vectors


@pytest.fixture
def service_config() -> ServiceConfig:
    return ServiceConfig(
        {
            "models.enabled": ["gte-multilingual-base"],
            "gte-multilingual-base.hfModelId": "Alibaba-NLP/gte-multilingual-base",
            "gte-multilingual-base.dimension": 768,
            "gte-multilingual-base.version": 1,
            "gte-multilingual-base.maxSeqLength": 8192,
            "gte-multilingual-base.trustRemoteCode": True,
            "gte-multilingual-base.extraHubRepos": ["Alibaba-NLP/new-impl"],
            "gte-multilingual-base.queryPrefix": "",
            "gte-multilingual-base.passagePrefix": "",
            "normalize_embedding": True,
            "max_batch_size": 32,
        }
    )


@pytest.fixture
def encoder() -> FakeEncoder:
    return FakeEncoder()


@pytest.fixture
def client(service_config: ServiceConfig, encoder: FakeEncoder, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    query_service = EmbeddingService(config=service_config, encoder=encoder)
    passage_service = EmbeddingService(config=service_config, encoder=encoder)
    pool = DedicatedEncodePool(
        config=service_config,
        query_service=query_service,
        passage_service=passage_service,
    )
    pool.start()
    # /metrics must not follow prometheus.enabled in the shipped YAML.
    monkeypatch.setitem(initialize_configuration()._values, "prometheus.enabled", True)
    app.dependency_overrides[router_module.get_pool] = lambda: pool
    app.dependency_overrides[router_module.get_service] = lambda: query_service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    pool.stop()
    router_module.reset_runtime()
    initialize_configuration.cache_clear()


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "OK"}


def test_version(client: TestClient) -> None:
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json()["version"] == __version__


def test_info_reports_dedicated_pool(client: TestClient) -> None:
    response = client.get("/info")
    assert response.status_code == 200
    body = response.json()
    assert body["encodePool"] == {
        "queryWorkers": 1,
        "passageWorkers": 1,
        "policy": "dedicated-lanes",
    }
    assert body["warmupOnStart"] is False
    assert "lanes" in body


def test_pool_warmup_loads_both_lanes(service_config: ServiceConfig) -> None:
    query_encoder = FakeEncoder()
    passage_encoder = FakeEncoder()
    pool = DedicatedEncodePool(
        config=service_config,
        query_service=EmbeddingService(config=service_config, encoder=query_encoder),
        passage_service=EmbeddingService(config=service_config, encoder=passage_encoder),
    )
    pool.start()
    try:
        assert query_encoder.seen == []
        assert passage_encoder.seen == []
        pool.warmup()
        assert pool.both_lanes_ready()
        assert query_encoder.seen == ["warmup"]
        assert passage_encoder.seen == ["warmup"]
    finally:
        pool.stop()


def test_metrics_on_service_port(client: TestClient) -> None:
    embedded = client.post(
        "/api/v1/embed_text",
        json={"text": "Net profit rose in 2024", "input_type": "passage"},
    )
    assert embedded.status_code == 200

    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    assert (
        'embedding_requests_total{input_type="passage",model="gte-multilingual-base",provider="huggingface",status="ok"}'
        in body
    )
    assert "embedding_duration_seconds" in body
    assert "embedding_texts_total" in body


def test_metrics_records_unknown_model(client: TestClient) -> None:
    response = client.post("/api/v1/embed_text", json={"text": "hello", "model_name": "not-a-model"})
    assert response.status_code == 404
    body = client.get("/metrics").text
    assert (
        'embedding_requests_total{input_type="passage",model="not-a-model",provider="huggingface",status="error"}'
        in body
    )


def test_embed_text_passage(client: TestClient) -> None:
    response = client.post(
        "/api/v1/embed_text",
        json={"text": "Net profit rose in 2024", "input_type": "passage"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["model_name"] == "gte-multilingual-base"
    assert body["meta"]["dimension"] == 768
    assert body["meta"]["hf_model_id"] == "Alibaba-NLP/gte-multilingual-base"
    assert body["meta"]["input_type"] == "passage"
    assert len(body["embeddings"]) == 1
    assert len(body["embeddings"][0]["embedding"]) == 768


def test_gte_sends_text_without_prefix(client: TestClient, encoder: FakeEncoder) -> None:
    client.post("/api/v1/embed_text", json={"text": "What was net profit?", "input_type": "query"})
    client.post("/api/v1/embed_text", json={"text": "Net profit rose", "input_type": "passage"})
    assert encoder.seen == ["What was net profit?", "Net profit rose"]


def test_e5_prefixes_still_applied_when_configured() -> None:
    config = ServiceConfig(
        {
            "models.enabled": ["multilingual-e5-large"],
            "multilingual-e5-large.hfModelId": "intfloat/multilingual-e5-large",
            "multilingual-e5-large.dimension": 768,
            "multilingual-e5-large.queryPrefix": "query: ",
            "multilingual-e5-large.passagePrefix": "passage: ",
        }
    )
    encoder = FakeEncoder()
    service = EmbeddingService(config=config, encoder=encoder)
    service.embed_texts(["What was net profit?"], input_type="query")
    service.embed_texts(["Net profit rose"], input_type="passage")
    assert encoder.seen == ["query: What was net profit?", "passage: Net profit rose"]


def test_embed_texts_batch(client: TestClient) -> None:
    response = client.post(
        "/api/v1/embed_texts",
        json={"texts": ["chunk one", "chunk two"], "input_type": "passage"},
    )
    assert response.status_code == 200
    assert len(response.json()["embeddings"]) == 2


def test_empty_text_rejected(client: TestClient) -> None:
    response = client.post("/api/v1/embed_text", json={"text": "   "})
    assert response.status_code == 422


def test_unknown_model(client: TestClient) -> None:
    response = client.post(
        "/api/v1/embed_text",
        json={"text": "hello", "model_name": "not-a-model"},
    )
    assert response.status_code == 404


def test_apply_input_prefix_does_not_double() -> None:
    assert apply_input_prefix("hello", "query: ") == "query: hello"
    assert apply_input_prefix("query: hello", "query: ") == "query: hello"
    assert apply_input_prefix("  hello ", "") == "hello"


def test_model_settings_reads_gte_options(service_config: ServiceConfig) -> None:
    settings = service_config.model_settings("gte-multilingual-base")
    assert settings["trust_remote_code"] is True
    assert settings["max_seq_length"] == 8192
    assert settings["query_prefix"] == ""
    assert settings["passage_prefix"] == ""


def test_prefetch_downloads_model_and_remote_code(
    service_config: ServiceConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []

    def fake_snapshot_download(repo_id: str, **_kwargs) -> str:
        called.append(repo_id)
        return f"/tmp/hf-cache/{repo_id}"

    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot_download)
    from embedding_service.service import prefetch_enabled_models

    prefetch_enabled_models(service_config)
    assert called == ["Alibaba-NLP/gte-multilingual-base", "Alibaba-NLP/new-impl"]


def test_query_not_blocked_by_in_flight_passage(service_config: ServiceConfig) -> None:
    query_encoder = BlockingEncoder(lane="query")
    passage_encoder = BlockingEncoder(lane="passage")

    pool = DedicatedEncodePool(
        config=service_config,
        query_service=EmbeddingService(config=service_config, encoder=query_encoder),
        passage_service=EmbeddingService(config=service_config, encoder=passage_encoder),
    )
    pool.start()
    try:
        passage_future = pool.submit(["long passage batch"], input_type="passage")
        assert passage_encoder.passage_started.wait(timeout=2)

        started = time.monotonic()
        query_future = pool.submit(["urgent search query"], input_type="query")
        items, meta = query_future.result(timeout=2)
        elapsed = time.monotonic() - started

        assert meta["input_type"] == "query"
        assert items[0]["text"] == "urgent search query"
        assert elapsed < 0.5, f"query waited on passage lane ({elapsed:.2f}s)"
        assert not passage_future.done()

        passage_encoder.block_passage.set()
        passage_items, passage_meta = passage_future.result(timeout=2)
        assert passage_meta["input_type"] == "passage"
        assert passage_items[0]["text"] == "long passage batch"
    finally:
        passage_encoder.block_passage.set()
        pool.stop()
