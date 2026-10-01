"""Prometheus metrics for text extraction. Scraped at GET /metrics on the service port."""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

TEXT_EXTRACTION_REQUESTS = Counter(
    "text_extraction_requests_total",
    "Text extraction calls by file type, method, and outcome.",
    ["extension", "method", "status"],
)

TEXT_EXTRACTION_DURATION = Histogram(
    "text_extraction_duration_seconds",
    "Time spent extracting one file.",
    ["method"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)

TEXT_EXTRACTION_PAGES = Counter(
    "text_extraction_pages_total",
    "Pages returned by successful extractions.",
    ["method"],
)

TEXT_EXTRACTION_CHARS = Counter(
    "text_extraction_chars_total",
    "Characters of search text returned by successful extractions.",
    ["method"],
)


def observe_extraction(
    *,
    extension: str,
    method: str,
    status: str,
    duration_seconds: float,
    pages: int = 0,
    chars: int = 0,
) -> None:
    """Count one extraction. Pages and characters are recorded only when it succeeds."""
    TEXT_EXTRACTION_REQUESTS.labels(extension=extension or "unknown", method=method, status=status).inc()
    TEXT_EXTRACTION_DURATION.labels(method=method).observe(max(duration_seconds, 0.0))
    if status == "ok":
        TEXT_EXTRACTION_PAGES.labels(method=method).inc(pages)
        TEXT_EXTRACTION_CHARS.labels(method=method).inc(chars)


def metrics_response() -> Response:
    """Prometheus text exposition for GET /metrics."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
