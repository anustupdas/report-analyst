from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from langgraph_server.config import Settings, get_settings
from langgraph_server.core.api_client import ApiClient, ApiClientError
from langgraph_server.core.project_context import document_catalog, format_project_context, usable_document_ids

_bearer = HTTPBearer(auto_error=False)


async def require_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> tuple[str, dict[str, Any]]:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization Bearer token")
    token = credentials.credentials.strip()
    client = ApiClient(settings)
    try:
        user = await client.get_me(token)
    except ApiClientError as exc:
        if exc.status_code == 401:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token") from exc
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable.",
        ) from exc
    if not isinstance(user, dict) or not user.get("id"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token")
    return token, user


async def load_authorized_snapshot(
    *,
    settings: Settings,
    token_user: dict[str, Any],
    user_id: UUID,
    project_id: UUID,
    thread_id: UUID,
    require_project_thread: bool = True,
) -> dict[str, Any]:
    if str(token_user["id"]) != str(user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user_id does not match the Bearer token")

    client = ApiClient(settings)
    try:
        snapshot = await client.get_project_snapshot(project_id)
    except ApiClientError as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found") from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not load project context from the API.",
        ) from exc

    if not snapshot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    owner_id = snapshot.get("userId") or snapshot.get("user_id")
    if owner_id and str(owner_id) != str(user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project is not owned by this user")

    if require_project_thread:
        snapshot_thread = snapshot.get("threadId") or snapshot.get("thread_id")
        if snapshot_thread and str(snapshot_thread) != str(thread_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="thread_id does not match this project")

    return snapshot


def require_agent_secret(settings: Settings, x_agent_secret: str | None) -> None:
    expected = (settings.agent_secret or "").strip()
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent auth is not configured")
    if not x_agent_secret or x_agent_secret != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent secret")


def snapshot_configurable(snapshot: dict[str, Any], *, user: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_inventory_context": format_project_context(snapshot),
        "usable_document_ids": usable_document_ids(snapshot),
        "document_catalog": document_catalog(snapshot),
        "empty_project": not usable_document_ids(snapshot),
        "user_display_name": user.get("displayName") or user.get("display_name"),
    }
