from pathlib import Path

import pytest

from chat_api.security import constant_time_equals, extension_of, generate_api_token, hash_api_token, safe_join


def test_hash_api_token_is_sha256_hex():
    digest = hash_api_token("secret")
    assert len(digest) == 64
    assert digest == hash_api_token("secret")
    assert digest != hash_api_token("other")


def test_generate_api_token_is_unique():
    assert generate_api_token() != generate_api_token()


def test_constant_time_equals():
    assert constant_time_equals("abc", "abc") is True
    assert constant_time_equals("abc", "abd") is False


def test_extension_of():
    assert extension_of("report.PDF") == "pdf"
    assert extension_of("noext") == ""


def test_safe_join_allows_nested(tmp_path: Path):
    target = safe_join(tmp_path, "user", "project", "doc")
    assert target.is_relative_to(tmp_path.resolve())


def test_safe_join_rejects_traversal(tmp_path: Path):
    with pytest.raises(ValueError, match="escapes"):
        safe_join(tmp_path, "..", "etc")
