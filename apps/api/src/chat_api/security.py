from __future__ import annotations

import hashlib
import hmac
import secrets
from pathlib import Path


def generate_api_token() -> str:
    return secrets.token_urlsafe(32)


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def extension_of(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


def safe_join(root: Path, *parts: str) -> Path:
    """Join path parts under root; reject traversal."""
    root_resolved = root.resolve()
    candidate = root_resolved.joinpath(*parts).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("Path escapes data root") from exc
    return candidate
