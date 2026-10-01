from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from langgraph_server.config import Settings


class ApiClientError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ApiClient:
    """HTTP client for chat-api user and agent routes (no MCP)."""

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._client = client

    def _timeout(self) -> float:
        return self._settings.api_timeout_seconds

    def _base(self) -> str:
        return self._settings.api_base_url

    async def _request(self, method: str, path: str, *, headers: dict[str, str], json: Any | None = None) -> Any:
        url = f"{self._base()}{path}"
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout())
        try:
            response = await client.request(method, url, headers=headers, json=json)
        except httpx.HTTPError as exc:
            raise ApiClientError(f"API request failed: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code == 401:
            raise ApiClientError("Invalid API token", status_code=401)
        if response.status_code == 404:
            raise ApiClientError("Not found", status_code=404)
        if response.status_code >= 400:
            raise ApiClientError(
                f"API returned {response.status_code}",
                status_code=response.status_code,
            )
        payload = response.json()
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    async def get_me(self, bearer_token: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/api/v1/me",
            headers={"Authorization": f"Bearer {bearer_token}"},
        )

    async def get_project_snapshot(self, project_id: UUID) -> dict[str, Any] | None:
        if not self._settings.agent_secret:
            raise ApiClientError("Agent auth is not configured", status_code=503)
        return await self._request(
            "GET",
            f"/api/v1/agent/projects/{project_id}",
            headers={"X-Agent-Secret": self._settings.agent_secret},
        )

    async def search_chunks(
        self,
        *,
        project_id: UUID,
        query: str,
        document_id: UUID | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        if not self._settings.agent_secret:
            raise ApiClientError("Agent auth is not configured", status_code=503)
        body: dict[str, Any] = {"query": query}
        if document_id is not None:
            body["documentId"] = str(document_id)
        if limit is not None:
            body["limit"] = limit
        return await self._request(
            "POST",
            f"/api/v1/agent/projects/{project_id}/chunks/search",
            headers={
                "X-Agent-Secret": self._settings.agent_secret,
                "Content-Type": "application/json",
            },
            json=body,
        )
