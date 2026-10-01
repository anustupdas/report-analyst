from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-Id"
SESSION_ID_HEADER = "X-Session-Id"

logger = logging.getLogger("chat_api.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        session_id = request.headers.get(SESSION_ID_HEADER) or ""
        request.state.request_id = request_id
        request.state.session_id = session_id

        response: Response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        if session_id:
            response.headers[SESSION_ID_HEADER] = session_id

        logger.info(
            "http_request",
            extra={
                "request_id": request_id,
                "session_id": session_id or None,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
            },
        )
        return response


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


def structured_log(logger_: logging.Logger, event: str, **fields: Any) -> None:
    """Emit a single-line structured event for tracking / cost later."""
    parts = [f"event={event}"]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    logger_.info(" ".join(parts))
