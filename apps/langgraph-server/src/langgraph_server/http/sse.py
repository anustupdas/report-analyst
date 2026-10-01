from __future__ import annotations

import json
from typing import Any


def model_to_sse_str(event: dict[str, Any]) -> str:
    event_type = event.get("type", "message")
    return f"event: {event_type}\ndata: {json.dumps(event)}\n\n"


def sse_keepalive() -> str:
    return ": keepalive\n\n"
