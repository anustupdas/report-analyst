"""Turn report-search ToolMessages into structured FTE + sustainability_goals.

Used after the supervisor ReAct loop in datapoints mode: collect search evidence,
run the `supervisor_agent/extractor` structured LLM once (Langfuse generation
scope only — no CallbackHandler, to avoid double-logging), and return JSON for
chat-api to merge into `documents.key_datapoints`.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from langchain.chat_models import init_chat_model
from langchain_core.messages import ToolMessage

from langgraph_server.config import get_settings
from langgraph_server.core.langfuse_client import langfuse_generation_scope
from langgraph_server.prompt.loader import load_prompt
from langgraph_server.tool.datapoints_extract import KeyDatapoints, datapoints_payload, is_empty_datapoints

logger = logging.getLogger(__name__)

EXTRACTOR_AGENT = "supervisor_agent"
EXTRACTOR_NODE = "extractor"
EXTRACTOR_PROMPT = "main"

DatapointsJobMode = Literal["ready", "completed", "refresh"]


def job_message(*, document_id: str, file_name: str, mode: DatapointsJobMode) -> str:
    ready_note = (
        "Mode: ready (early index only). Search this document; accept partial or empty evidence.\n"
        if mode == "ready"
        else "Mode: completed/refresh (full index). Search thoroughly for both topics.\n"
    )
    return (
        f"KEY DATAPOINTS JOB\n"
        f"document_id: {document_id}\n"
        f"document name: {file_name}\n"
        f"{ready_note}"
        "1) Call report-search-tool for FTE / headcount / workforce on this document_id.\n"
        "2) Call report-search-tool for sustainability / climate / ESG / net-zero on this document_id "
        "(one combined query is OK if it clearly covers both).\n"
        "3) Stop after at most 2 searches. Downstream will build structured FTE + sustainability_goals JSON."
    )


def _normalize_content(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return "".join(parts)
    return str(raw or "")


def search_context_from_messages(messages: list[Any]) -> str:
    chunks: list[str] = []
    for message in messages or []:
        if not isinstance(message, ToolMessage) and getattr(message, "type", None) != "tool":
            continue
        content = _normalize_content(getattr(message, "content", ""))
        if not content.strip():
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            chunks.append(content[:4000])
            continue
        if not isinstance(payload, dict) or not payload.get("ok"):
            continue
        for match in payload.get("matches") or []:
            if not isinstance(match, dict):
                continue
            page_start = match.get("page_start")
            page_end = match.get("page_end")
            section = match.get("section") or ""
            text = str(match.get("content") or "").strip()
            if not text:
                continue
            header = f"[pages {page_start}-{page_end}" + (f" | {section}" if section else "") + "]"
            chunks.append(f"{header}\n{text}")
    return "\n\n".join(chunks)


def pages_from_messages(messages: list[Any]) -> list[int]:
    pages: list[int] = []
    seen: set[int] = set()
    for message in messages or []:
        if not isinstance(message, ToolMessage) and getattr(message, "type", None) != "tool":
            continue
        content = _normalize_content(getattr(message, "content", ""))
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        for match in payload.get("matches") or []:
            if not isinstance(match, dict):
                continue
            for key in ("page_start", "page_end"):
                value = match.get(key)
                if value is None:
                    continue
                try:
                    page = int(value)
                except (TypeError, ValueError):
                    continue
                if page >= 1 and page not in seen:
                    seen.add(page)
                    pages.append(page)
                if len(pages) >= 5:
                    return pages
    return pages


def finalize_structured(
    *,
    context: str,
    file_name: str,
    document_id: str,
    pages: list[int],
    llm: Any | None = None,
) -> KeyDatapoints:
    if not context.strip():
        return KeyDatapoints(fte=None, sustainability_goals=[])

    prompt = load_prompt(EXTRACTOR_AGENT, EXTRACTOR_NODE, EXTRACTOR_PROMPT)
    model_cfg = prompt.model_config
    max_chars = (model_cfg.max_input_tokens or 100_000) * 4 // 2
    clipped = context[:max_chars]
    messages = prompt.compile(
        file_name=file_name,
        document_id=document_id,
        pages=", ".join(str(p) for p in pages) if pages else "unknown",
        context=clipped,
    )
    settings = get_settings()
    chat = llm or init_chat_model(
        model=model_cfg.model,
        model_provider=model_cfg.model_provider,
        api_key=settings.openai_api_key,
        max_tokens=model_cfg.max_output_tokens,
        streaming=False,
        **model_cfg.model_args,
    )
    structured = chat.with_structured_output(KeyDatapoints)

    # Named Langfuse generation only — do not also pass CallbackHandler (that double-logs
    # the same call as RunnableSequence / ChatOpenAI).
    with langfuse_generation_scope(
        name=f"{EXTRACTOR_AGENT}/{EXTRACTOR_NODE}",
        model=model_cfg.model,
        input=messages,
        metadata={
            "document_id": document_id,
            "pages": ",".join(str(p) for p in pages),
            "prompt": prompt.path,
            "prompt_source": prompt.source,
        },
        prompt=prompt.langfuse_prompt,
    ) as observation:
        result = structured.invoke(messages)
        if observation is not None and isinstance(result, KeyDatapoints):
            try:
                observation.update(output=result.model_dump(mode="json"))
            except Exception:
                logger.debug("Langfuse extractor observation update failed", exc_info=True)

    if not isinstance(result, KeyDatapoints):
        raise RuntimeError("Datapoints finalize returned no parsable KeyDatapoints")
    return result


def build_key_datapoints_payload(
    *,
    messages: list[Any],
    file_name: str,
    document_id: str,
    job_mode: DatapointsJobMode,
    llm: Any | None = None,
) -> tuple[dict[str, Any], list[int]]:
    context = search_context_from_messages(messages)
    pages = pages_from_messages(messages)
    extracted = finalize_structured(
        context=context,
        file_name=file_name,
        document_id=document_id,
        pages=pages,
        llm=llm,
    )
    if is_empty_datapoints(extracted) and context.strip():
        extracted = finalize_structured(
            context=context,
            file_name=file_name,
            document_id=document_id,
            pages=pages,
            llm=llm,
        )
    payload = datapoints_payload(extracted)
    if job_mode == "ready" and is_empty_datapoints(extracted):
        payload["status_note"] = (
            "Not enough information in the first indexed pages to fill FTE or sustainability goals. "
            "A fuller pass runs when indexing completes."
        )
    return payload, pages
