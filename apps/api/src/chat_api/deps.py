from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from chat_api.config import Settings, get_settings
from chat_api.db import get_db
from chat_api.errors import ForbiddenError, UnauthorizedError
from chat_api.modules.models import User
from chat_api.security import constant_time_equals, hash_api_token
from chat_api.services.storage import LocalStorageService
from chat_api.services.text_extraction import TextExtractionClient

# Swagger "Authorize" button uses this scheme (sends Authorization: Bearer …).
_bearer = HTTPBearer(auto_error=False)


def settings_dep() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(settings_dep)]
DbDep = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DbDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise UnauthorizedError("Missing or invalid Authorization Bearer token")

    token = credentials.credentials.strip()
    token_hash = hash_api_token(token)
    user = db.execute(
        select(User).where((User.api_token_hash == token_hash) | (User.previous_api_token_hash == token_hash))
    ).scalar_one_or_none()
    if user is None:
        raise UnauthorizedError("Invalid API token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_agent_secret(
    settings: SettingsDep,
    x_agent_secret: Annotated[str | None, Header(alias="X-Agent-Secret")] = None,
) -> None:
    expected = settings.agent_secret
    if not expected:
        # Fail closed when the agent secret is unset.
        raise ForbiddenError("Agent auth is not configured")
    if not x_agent_secret or not constant_time_equals(x_agent_secret, expected):
        raise UnauthorizedError("Invalid agent secret")


def require_internal_secret(
    settings: SettingsDep,
    x_internal_secret: Annotated[str | None, Header(alias="X-Internal-Secret")] = None,
) -> None:
    expected = settings.internal_workflow_secret
    if not expected:
        raise ForbiddenError("Internal workflow auth is not configured")
    if not x_internal_secret or not constant_time_equals(x_internal_secret, expected):
        raise UnauthorizedError("Invalid internal secret")


def storage_dep(settings: SettingsDep) -> LocalStorageService:
    return LocalStorageService(settings)


def extraction_client_dep(settings: SettingsDep) -> TextExtractionClient:
    return TextExtractionClient(settings)


def request_ids(request: Request) -> tuple[str | None, str | None]:
    return (
        getattr(request.state, "request_id", None),
        getattr(request.state, "session_id", None) or None,
    )
