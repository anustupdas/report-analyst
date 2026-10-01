from __future__ import annotations

import logging
from typing import Any

import httpx

from chat_api.config import Settings
from chat_api.errors import AppError
from chat_api.tracing import structured_log

logger = logging.getLogger(__name__)


class TextExtractionClient:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.text_extraction_url.rstrip("/")
        self.timeout = settings.text_extraction_timeout_seconds
        self.use_ocr = settings.text_extraction_use_ocr

    def extract_file(
        self,
        file_ref: str,
        *,
        use_ocr: bool | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if request_id:
            headers["X-Request-Id"] = request_id
        if session_id:
            headers["X-Session-Id"] = session_id

        payload = {"file_ref": file_ref, "use_ocr": self.use_ocr if use_ocr is None else use_ocr}
        url = f"{self.base_url}/api/v1/extract/file"

        structured_log(
            logger,
            "text_extraction.request",
            request_id=request_id,
            file_ref=file_ref,
            use_ocr=payload["use_ocr"],
        )

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload, headers=headers)
        except httpx.RequestError as exc:
            raise AppError(
                "Text extraction service unavailable",
                status_code=503,
                code="upstream_unavailable",
            ) from exc

        if response.status_code >= 400:
            detail = _safe_detail(response)
            raise AppError(
                f"Text extraction failed: {detail}",
                status_code=502,
                code="extraction_failed",
            )

        body = response.json()
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict) or "text" not in data:
            raise AppError(
                "Text extraction returned an unexpected payload",
                status_code=502,
                code="extraction_invalid_response",
            )

        structured_log(
            logger,
            "text_extraction.success",
            request_id=request_id,
            file_ref=file_ref,
            method=data.get("method"),
            model=data.get("model"),
            pages=len(data.get("pages") or []),
            chars=len(data.get("text") or ""),
            duration_ms=data.get("duration_ms"),
        )
        return data


def _safe_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            return str(payload.get("detail") or payload.get("message") or response.status_code)
    except Exception:
        pass
    return f"HTTP {response.status_code}"
