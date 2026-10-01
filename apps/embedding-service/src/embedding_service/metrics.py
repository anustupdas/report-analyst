"""Prometheus metrics for embeddings. Scraped at GET /metrics on the service port."""

import time
from collections.abc import Iterator
from contextlib import contextmanager

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

EMBEDDING_REQUESTS = Counter(
    "embedding_requests_total",
    "Embedding API calls by provider, model, input lane, and outcome.",
    ["provider", "model", "input_type", "status"],
)

EMBEDDING_DURATION = Histogram(
    "embedding_duration_seconds",
    "Time spent embedding one request.",
    ["provider", "model"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)

EMBEDDING_TEXTS = Counter(
    "embedding_texts_total",
    "Texts submitted to successful embedding calls.",
    ["provider", "model", "input_type"],
)


def observe_embedding(
    *,
    provider: str,
    model: str,
    input_type: str,
    status: str,
    duration_seconds: float,
    texts: int = 0,
) -> None:
    """Count one embedding call. Text volume is recorded only for successful calls."""
    EMBEDDING_REQUESTS.labels(
        provider=provider,
        model=model or "unknown",
        input_type=input_type or "na",
        status=status,
    ).inc()
    EMBEDDING_DURATION.labels(provider=provider, model=model or "unknown").observe(max(duration_seconds, 0.0))
    if status == "ok":
        EMBEDDING_TEXTS.labels(provider=provider, model=model or "unknown", input_type=input_type or "na").inc(texts)


@contextmanager
def track_embedding(*, provider: str, model: str, input_type: str, texts: int) -> Iterator[None]:
    """Time one embedding request and record ok or error. Re-raises the original exception."""
    started = time.perf_counter()
    try:
        yield
    except Exception:
        observe_embedding(
            provider=provider,
            model=model,
            input_type=input_type,
            status="error",
            duration_seconds=time.perf_counter() - started,
        )
        raise
    observe_embedding(
        provider=provider,
        model=model,
        input_type=input_type,
        status="ok",
        duration_seconds=time.perf_counter() - started,
        texts=texts,
    )


def metrics_response() -> Response:
    """Prometheus text exposition for GET /metrics."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
