from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from langchain.tools import BaseTool, ToolRuntime
from langgraph.utils.config import get_config
from pydantic import BaseModel, Field

from langgraph_server.config import get_settings
from langgraph_server.core.api_client import ApiClient, ApiClientError
from langgraph_server.core.project_context import ReportSearched, ReportSearchSource, ReportSearchStarted
from langgraph_server.core.stream_writer import emit_stream_event
from langgraph_server.tool.search_budget import (
    SEARCH_BUDGET_EXCEEDED_REASON,
    consume_report_search_budget,
    search_budget_exceeded_message,
)

NO_SOURCES_MESSAGE = (
    "No searchable documents in this project. Do not search again. "
    "Tell the analyst to upload a report or wait until a document is ready or completed."
)
UNKNOWN_DOCUMENT_MESSAGE = "document_id is not in Project Context. Copy the exact document_id from the SOURCES list."
MULTIPLE_DOCUMENTS_MESSAGE = (
    "Multiple reports are in Project Context. Pass document_id for the report you want, "
    "or call this tool once per document_id."
)
MISSING_PROJECT_MESSAGE = "Missing project_id in the run config."
NO_MATCHES_MESSAGE = "No matching chunks were found for that query."


class ReportSearchInput(BaseModel):
    query: str = Field(description="Search query for similar annual-report chunks. Make it specific.")
    document_id: str | None = Field(
        default=None,
        description=(
            "Exact document_id from Project Context. One document per call. "
            "Omit only when there is exactly one Source."
        ),
    )
    limit: int | None = Field(default=None, ge=1, le=20, description="Maximum chunks to return.")


class ReportSearchTool(BaseTool):
    name: str = "report-search-tool"
    description: str = (
        "Search indexed annual-report chunks for this project. "
        "Copy document_id from Project Context. One document per call; call again "
        "(including in parallel) for another report. Prefer one strong query over many "
        "rephrases; this turn has a hard search budget (see system prompt). "
        "Do not call when Project Context has no SOURCES. Returns JSON: ok, "
        "matches[{filename, section, page_start, page_end, score, content}], "
        "or ok=false with reason and message."
    )
    args_schema: type[BaseModel] = ReportSearchInput
    return_direct: bool = False
    response_format: str = "content_and_artifact"

    async def _arun(
        self,
        query: str,
        document_id: str | None = None,
        limit: int | None = None,
        runtime: ToolRuntime | None = None,
    ) -> tuple[str, ReportSearched | None]:
        configurable = _tool_configurable(runtime)
        usable_ids = {str(item) for item in (configurable.get("usable_document_ids") or [])}
        if not usable_ids:
            return _error("no_sources", NO_SOURCES_MESSAGE), None

        allowed, budget_used, budget_limit = consume_report_search_budget()
        if not allowed:
            return (
                _error(
                    SEARCH_BUDGET_EXCEEDED_REASON,
                    search_budget_exceeded_message(used=budget_used, limit=budget_limit),
                    query=query,
                    document_id=document_id,
                ),
                None,
            )

        if document_id:
            document_id = document_id.strip()
            if document_id not in usable_ids:
                return _error("unknown_document", UNKNOWN_DOCUMENT_MESSAGE), None
        elif len(usable_ids) > 1:
            return _error("multiple_documents", MULTIPLE_DOCUMENTS_MESSAGE), None

        project_id = configurable.get("project_id")
        if not project_id:
            return _error("missing_project", MISSING_PROJECT_MESSAGE), None

        settings = get_settings()
        resolved_limit = limit or settings.vector_search_default_limit
        event_id = uuid4()
        emit_stream_event(
            ReportSearchStarted(
                id=event_id,
                query=query,
                document_id=document_id,
                limit=resolved_limit,
            )
        )
        client = ApiClient(settings)
        try:
            payload = await client.search_chunks(
                project_id=UUID(str(project_id)),
                query=query,
                document_id=UUID(document_id) if document_id else None,
                limit=resolved_limit,
            )
        except ApiClientError as exc:
            searched = ReportSearched(
                id=event_id,
                query=query,
                document_id=document_id,
                limit=resolved_limit,
            )
            emit_stream_event(searched)
            return _error("search_failed", f"Search failed: {exc}"), searched

        raw_matches = payload.get("matches") if isinstance(payload, dict) else None
        catalog = configurable.get("document_catalog") or {}
        matches = [_match_record(match, catalog) for match in (raw_matches or [])]
        searched = ReportSearched(
            id=event_id,
            query=query,
            document_id=document_id,
            limit=resolved_limit,
            count=len(matches),
            sources=_sources_from_matches(matches),
        )
        emit_stream_event(searched)
        if not matches:
            return _error("no_matches", NO_MATCHES_MESSAGE, query=query, document_id=document_id), searched

        return (
            _dump(
                {
                    "ok": True,
                    "query": query,
                    "document_id": document_id,
                    "count": len(matches),
                    "matches": matches,
                }
            ),
            searched,
        )

    def _run(self, query: str, document_id: str | None = None, limit: int | None = None) -> tuple[str, None]:
        raise RuntimeError("report-search-tool must be called asynchronously")


def _tool_configurable(runtime: ToolRuntime | None) -> dict[str, Any]:
    configurable: dict[str, Any] = {}
    try:
        configurable.update(dict((get_config() or {}).get("configurable") or {}))
    except RuntimeError:
        pass
    if runtime is None:
        return configurable
    configurable.update(dict((runtime.config or {}).get("configurable") or {}))
    context = runtime.context
    if hasattr(context, "model_dump"):
        context = context.model_dump()
    if isinstance(context, dict):
        for key in ("project_id", "usable_document_ids", "document_catalog", "empty_project"):
            if context.get(key) not in (None, [], {}):
                configurable[key] = context[key]
    return configurable


def _match_record(match: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    doc_id = str(match.get("documentId") or match.get("document_id") or "")
    meta = catalog.get(doc_id) or {}
    filename = match.get("originalFilename") or meta.get("originalFilename") or meta.get("title") or "report"
    section = match.get("section") or None
    page_start = match.get("pageStart") if match.get("pageStart") is not None else match.get("page_start")
    page_end = match.get("pageEnd") if match.get("pageEnd") is not None else match.get("page_end")
    score = match.get("score")
    return {
        "document_id": doc_id or None,
        "filename": filename,
        "section": section,
        "page_start": page_start,
        "page_end": page_end,
        "score": round(float(score), 4) if isinstance(score, (int, float)) else None,
        "content": str(match.get("content") or "").strip(),
    }


def _sources_from_matches(matches: list[dict[str, Any]]) -> list[ReportSearchSource]:
    sources: list[ReportSearchSource] = []
    seen: set[tuple[Any, ...]] = set()
    for match in matches:
        key = (
            match.get("document_id"),
            match.get("filename"),
            match.get("section"),
            match.get("page_start"),
            match.get("page_end"),
        )
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            ReportSearchSource(
                document_id=match.get("document_id"),
                filename=str(match.get("filename") or "report"),
                section=match.get("section"),
                page_start=match.get("page_start"),
                page_end=match.get("page_end"),
            )
        )
    return sources


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _error(reason: str, message: str, **extra: Any) -> str:
    return _dump({"ok": False, "reason": reason, "message": message, **extra})


report_search_tool = ReportSearchTool()
