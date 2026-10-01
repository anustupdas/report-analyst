"""Outbound calls from chat-api to LangGraph `POST /datapoints/run` (non-streaming).

Uses the shared supervisor graph with mode=datapoints — not a separate agent process.
"""

from __future__ import annotations

import logging
from typing import Any, Literal
from uuid import UUID

import httpx

from chat_api.config import Settings

logger = logging.getLogger(__name__)

DatapointsMode = Literal["ready", "completed", "refresh"]


class LangGraphDatapointsClient:
    def __init__(self, settings: Settings) -> None:
        internal = str(getattr(settings, "langgraph_internal_url", "") or "").strip()
        public = str(getattr(settings, "langgraph_url", "") or "").strip()
        self._base = (internal or public).rstrip("/")
        self._secret = settings.agent_secret or ""
        self._timeout = 300.0

    @property
    def enabled(self) -> bool:
        return bool(self._base and self._secret)

    def run(
        self,
        *,
        project_id: UUID,
        document_id: UUID,
        user_id: UUID,
        file_name: str | None = None,
        mode: DatapointsMode = "completed",
    ) -> dict[str, Any] | None:
        """POST /datapoints/run — returns structured keyDatapoints (caller persists)."""
        if not self.enabled:
            return None
        return self._post_json(
            "/datapoints/run",
            {
                "project_id": str(project_id),
                "document_id": str(document_id),
                "user_id": str(user_id),
                "file_name": file_name,
                "mode": mode,
            },
            timeout=self._timeout,
        )

    def _post_json(self, path: str, body: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any] | None:
        url = f"{self._base}{path}"
        try:
            response = httpx.post(
                url,
                headers={
                    "X-Agent-Secret": self._secret,
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=timeout or self._timeout,
            )
            if response.status_code >= 400:
                logger.warning(
                    "langgraph datapoints call failed path=%s status=%s body=%s",
                    path,
                    response.status_code,
                    response.text[:300],
                )
                return None
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except Exception:
            logger.exception("langgraph datapoints call failed path=%s", path)
            return None
