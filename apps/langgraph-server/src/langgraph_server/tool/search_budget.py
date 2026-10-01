"""Per-turn budget for report-search-tool calls."""

from __future__ import annotations

import contextvars

from langgraph_server.config import get_settings

_search_calls: contextvars.ContextVar[int] = contextvars.ContextVar(
    "report_search_calls",
    default=0,
)

SEARCH_BUDGET_EXCEEDED_REASON = "search_budget_exceeded"

__all__ = (
    "SEARCH_BUDGET_EXCEEDED_REASON",
    "reset_report_search_budget",
    "consume_report_search_budget",
    "report_search_budget_status",
    "search_budget_exceeded_message",
)


def reset_report_search_budget() -> None:
    _search_calls.set(0)


def report_search_budget_status() -> tuple[int, int]:
    """Return (used, limit) for the current turn."""
    settings = get_settings()
    limit = max(1, int(settings.supervisor_max_report_searches_per_turn))
    return int(_search_calls.get()), limit


def consume_report_search_budget() -> tuple[bool, int, int]:
    """Consume one search slot. Returns (allowed, used, limit)."""
    used, limit = report_search_budget_status()
    if used >= limit:
        return False, used, limit
    used += 1
    _search_calls.set(used)
    return True, used, limit


def search_budget_exceeded_message(*, used: int, limit: int) -> str:
    return (
        f"Search budget for this turn is exhausted ({used}/{limit} report-search-tool calls). "
        "Do not call report-search-tool again. Answer now with the evidence you already have, "
        "and say briefly if something is still missing."
    )
