"""Auth helpers: regenerate keeps the previous Bearer token valid."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from chat_api.deps import get_current_user
from chat_api.errors import UnauthorizedError
from chat_api.modules.actions import regenerate_user_token
from chat_api.security import hash_api_token


def test_regenerate_user_token_keeps_previous_hash():
    old_hash = hash_api_token("old-token")
    user = SimpleNamespace(
        email="demo@example.com",
        api_token_hash=old_hash,
        previous_api_token_hash=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db = MagicMock()
    db.execute.return_value = result

    updated, new_token = regenerate_user_token(db, email="demo@example.com")

    assert updated is user
    assert new_token and new_token != "old-token"
    assert user.previous_api_token_hash == old_hash
    assert user.api_token_hash == hash_api_token(new_token)
    assert user.api_token_hash != old_hash
    db.commit.assert_called_once()


def test_get_current_user_accepts_token_resolved_by_db():
    user = SimpleNamespace(id="u1", email="demo@example.com")
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db = MagicMock()
    db.execute.return_value = result

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="any-valid-looking-token")
    assert get_current_user(db=db, credentials=creds) is user
    db.execute.assert_called_once()


def test_get_current_user_rejects_unknown_token():
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db = MagicMock()
    db.execute.return_value = result
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="nope")
    with pytest.raises(UnauthorizedError):
        get_current_user(db=db, credentials=creds)
