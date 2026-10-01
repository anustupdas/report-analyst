from __future__ import annotations

import inspect
import logging
import time
from typing import Any, cast
from uuid import UUID, uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, SummarizationMiddleware, dynamic_prompt
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.utils.config import get_config
from pydantic import BaseModel, Field

from langgraph_server.config import get_settings
from langgraph_server.core.key_datapoints import build_key_datapoints_payload
from langgraph_server.core.project_context import ConversationDelta, ConversationEnded, ConversationStarted
from langgraph_server.core.stream_writer import get_stream_writer, suppress_stream_writes
from langgraph_server.core.supervisor.constants import (
    AGENT_NAME,
    ALLOWED_PROMPT_VARIANTS,
    DETAIL_EXTRACTOR_PROMPT,
    PROMPT_NAME,
    SUPERVISOR_NODE,
    SUPERVISOR_SUMMARIZATION_PROMPT,
    SUPERVISOR_SUMMARIZATION_RUN_NAME,
)
from langgraph_server.core.supervisor.state import SupervisorGraphState
from langgraph_server.prompt.loader import load_prompt
from langgraph_server.tool.report_search import report_search_tool
from langgraph_server.tool.search_budget import reset_report_search_budget

logger = logging.getLogger(__name__)


class SupervisorRuntimeContext(BaseModel):
    project_inventory_context: str | None = None
    user_display_name: str | None = None
    empty_project: bool = False
    project_id: str | None = None
    usable_document_ids: list[str] = Field(default_factory=list)
    document_catalog: dict[str, dict[str, Any]] = Field(default_factory=dict)
    mode: str = "chat"
    prompt_variant: str = PROMPT_NAME
    active_document_id: str | None = None
    active_document_name: str | None = None


def _is_ui_hidden(message: Any) -> bool:
    metadata = getattr(message, "response_metadata", None) or {}
    additional = getattr(message, "additional_kwargs", None) or {}
    return bool(
        (isinstance(metadata, dict) and metadata.get("ui_hidden"))
        or (isinstance(additional, dict) and additional.get("ui_hidden"))
    )


def _without_ui_hidden(messages: list[Any]) -> list[Any]:
    return [message for message in messages if not _is_ui_hidden(message)]


def _normalize_message_content(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                text = getattr(item, "text", None)
                if text:
                    parts.append(str(text))
        return "".join(parts)
    return str(raw or "")


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    return value


def _collect_tool_events(message_list: list[Any]) -> list[dict[str, Any]]:
    """Pull UI event artifacts from ToolMessages."""
    events: list[dict[str, Any]] = []
    for message in message_list or []:
        try:
            if getattr(message, "type", None) != "tool" and not isinstance(message, ToolMessage):
                continue
            artifact = getattr(message, "artifact", None)
            if not artifact:
                continue
            if hasattr(artifact, "model_dump"):
                artifact_dict = artifact.model_dump(mode="json")
            elif isinstance(artifact, dict):
                artifact_dict = dict(artifact)
            else:
                artifact_dict = {"type": artifact.__class__.__name__, "value": str(artifact)}
            if artifact_dict.get("timestamp") is None:
                artifact_dict["timestamp"] = time.time_ns()
            events.append(_to_jsonable(artifact_dict))
        except Exception:
            logger.exception("Failed to collect tool artifact from message")
    events.sort(key=lambda item: item.get("timestamp") or 0)
    return events


def _attach_events_to_conversation(
    conversation_messages: list[AIMessage],
    events: list[dict[str, Any]],
) -> list[AIMessage]:
    """Persist tool UI events on the spoken assistant message for history reload."""
    if not conversation_messages:
        return [
            AIMessage(
                content="",
                response_metadata={
                    **({"events": events} if events else {}),
                    "stream_type": ["content", "response_metadata"] if events else ["content"],
                    "timestamp": time.time_ns(),
                },
            )
        ]
    if not events:
        return conversation_messages

    last_message = conversation_messages[-1]
    prior_meta = dict(getattr(last_message, "response_metadata", None) or {})
    conversation_messages[-1] = AIMessage(
        id=getattr(last_message, "id", None),
        content=last_message.content,
        response_metadata={
            **prior_meta,
            "events": events,
            "stream_type": ["content", "response_metadata"],
            "timestamp": prior_meta.get("timestamp") or time.time_ns(),
        },
    )
    return conversation_messages


def _without_tool_payloads(messages: list[Any]) -> list[AnyMessage]:
    """Drop ToolMessages and AI tool-call shells from model context.

    Live tool results are available on the turn they run; after the turn they are
    stripped so the next call does not replay large report-search JSON. Spoken
    AI text (and human turns) are kept.
    """
    kept: list[AnyMessage] = []
    for message in messages:
        if isinstance(message, ToolMessage):
            continue
        if isinstance(message, AIMessage):
            text = _normalize_message_content(message.content)
            if getattr(message, "tool_calls", None):
                if text.strip():
                    kept.append(AIMessage(content=text, id=getattr(message, "id", None)))
                continue
            kept.append(message)
            continue
        if isinstance(message, (HumanMessage, AIMessage)):
            kept.append(message)
    return kept


def _render_dynamic_context_prompt(
    base_system_prompt: str,
    runtime_context: SupervisorRuntimeContext | dict[str, Any] | None,
) -> str:
    if isinstance(runtime_context, SupervisorRuntimeContext):
        runtime_context = runtime_context.model_dump(exclude_none=True)
    else:
        runtime_context = runtime_context or {}

    sections = [base_system_prompt]
    display_name = runtime_context.get("user_display_name")
    if display_name:
        sections.append(f"## Analyst\n- display_name: {display_name}")
    active_name = runtime_context.get("active_document_name")
    active_id = runtime_context.get("active_document_id")
    if active_name or active_id:
        lines = ["## Active open document"]
        if active_name:
            lines.append(f"- document_name: {active_name}")
        if active_id:
            lines.append(f"- document_id: {active_id}")
        lines.append(
            "- Prefer this report when the analyst's question is ambiguous or does not name a company/file. "
            "Still copy document_id exactly for report-search-tool."
        )
        sections.append("\n".join(lines))
    else:
        sections.append(
            "## Active open document\n"
            "- none (no report selected in the UI). Use Project Context to choose document_id(s)."
        )
    if runtime_context.get("empty_project"):
        sections.append(
            "## Empty Project\n"
            "There are no ready/completed reports. Do not call report-search-tool. "
            "Tell the analyst to upload a report or wait until indexing finishes."
        )
    inventory = runtime_context.get("project_inventory_context")
    if inventory:
        sections.append(f"## Project Context\n{inventory}")
    return "\n\n".join(sections)


def build_llm(llm: Any | None = None) -> Any:
    if llm is not None:
        return llm
    settings = get_settings()
    prompt = load_prompt(AGENT_NAME, SUPERVISOR_NODE, PROMPT_NAME)
    model = prompt.model_config
    params: dict[str, Any] = {
        "model": model.model,
        "model_provider": model.model_provider,
        "streaming": True,
        **model.model_args,
    }
    if settings.openai_api_key:
        params["api_key"] = settings.openai_api_key
    else:
        raise RuntimeError("OPENAI_API_KEY is not set. Add it to apps/api/.env or apps/langgraph-server/.env")
    return init_chat_model(**params)


def _build_supervisor_summarization_middleware() -> SummarizationMiddleware | None:
    settings = get_settings()
    if not settings.supervisor_summarization_enabled:
        return None
    if not settings.openai_api_key:
        logger.warning("Supervisor summarization is on but OPENAI_API_KEY is missing; skipping")
        return None

    prompt = load_prompt(AGENT_NAME, SUPERVISOR_NODE, PROMPT_NAME)
    model = prompt.model_config
    params: dict[str, Any] = {
        "model": model.model,
        "model_provider": model.model_provider,
        "streaming": False,
        "api_key": settings.openai_api_key,
        **model.model_args,
    }
    llm = init_chat_model(**params)
    if hasattr(llm, "with_config"):
        llm = llm.with_config(
            {
                "run_name": SUPERVISOR_SUMMARIZATION_RUN_NAME,
                "tags": ["summarization_middleware"],
            }
        )
    trigger: list[tuple[str, int]] = [("messages", settings.supervisor_summarization_trigger_messages)]
    if settings.supervisor_summarization_trigger_tokens:
        trigger.append(("tokens", settings.supervisor_summarization_trigger_tokens))
    return SummarizationMiddleware(
        model=llm,
        trigger=cast(Any, trigger),
        keep=("messages", settings.supervisor_summarization_keep_messages),
        trim_tokens_to_summarize=settings.supervisor_summarization_trim_tokens,
        summary_prompt=SUPERVISOR_SUMMARIZATION_PROMPT,
    )


def _resolve_prompt_variant(raw: Any) -> str:
    variant = str(raw or PROMPT_NAME).strip() or PROMPT_NAME
    if variant not in ALLOWED_PROMPT_VARIANTS:
        logger.warning("Unknown supervisor prompt variant %r; falling back to %s", variant, PROMPT_NAME)
        return PROMPT_NAME
    return variant


def build_inner_agent(*, llm: Any | None = None) -> Any:
    @dynamic_prompt
    def dynamic_context_prompt(request: ModelRequest) -> str:
        ctx = request.runtime.context
        if isinstance(ctx, SupervisorRuntimeContext):
            variant = _resolve_prompt_variant(ctx.prompt_variant)
            runtime_dict = ctx.model_dump(exclude_none=True)
        else:
            runtime_dict = dict(ctx or {})
            variant = _resolve_prompt_variant(runtime_dict.get("prompt_variant"))
        prompt = load_prompt(AGENT_NAME, SUPERVISOR_NODE, variant)
        settings = get_settings()
        compiled = prompt.compile(
            max_report_searches=settings.supervisor_max_report_searches_per_turn,
        )
        return _render_dynamic_context_prompt(compiled[0]["content"], runtime_dict)

    agent = create_agent(
        model=build_llm(llm),
        tools=[report_search_tool],
        middleware=[dynamic_context_prompt],
        context_schema=SupervisorRuntimeContext,
    )
    agent.name = AGENT_NAME
    agent._summarization_middleware = _build_supervisor_summarization_middleware()
    return agent


def _build_model_context_messages(state: SupervisorGraphState) -> list[AnyMessage]:
    """Build the LLM window from compressed prior context + new transcript tail.

    `model_context_messages` may already contain a `[COMPRESSED_CONTEXT]` note from
    an earlier summarization. New human/AI turns since
    `model_context_transcript_count` are appended. UI-hidden datapoints turns and
    tool payloads are never included.
    """
    visible_transcript = _without_ui_hidden(list(state.get("messages") or []))
    transcript_messages = _without_tool_payloads(visible_transcript)
    model_context_messages = state.get("model_context_messages")
    transcript_count = state.get("model_context_transcript_count")
    raw_messages = list(state.get("messages") or [])

    if (
        model_context_messages is None
        or transcript_count is None
        or transcript_count < 0
        or transcript_count > len(raw_messages)
    ):
        return transcript_messages

    tail = _without_ui_hidden(raw_messages[transcript_count:])
    return _without_tool_payloads([*list(model_context_messages), *tail])


async def _summarize_model_context_messages(
    messages: list[AnyMessage],
    summarization_middleware: Any | None,
) -> list[AnyMessage]:
    """Run SummarizationMiddleware when trigger thresholds are hit.

    `keep=N` applies only on a compress event; between events the window grows by
    appending turns until the next trigger (message count / token budget).
    Failures fall back to the unsummarized messages so chat still works.
    """
    if summarization_middleware is None:
        return messages

    abefore_model = getattr(summarization_middleware, "abefore_model", None)
    if not callable(abefore_model):
        return messages

    try:
        raw_update = abefore_model({"messages": list(messages)}, Runtime())
        if inspect.isawaitable(raw_update):
            update = await raw_update
        else:
            update = raw_update
    except Exception:
        logger.exception("Supervisor model-context summarization failed; continuing with unsummarized messages")
        return messages

    if not update:
        return messages

    updated_messages = update.get("messages") if isinstance(update, dict) else None
    if not isinstance(updated_messages, list):
        return messages

    filtered_messages = [message for message in updated_messages if not isinstance(message, RemoveMessage)]
    filtered_messages = _without_tool_payloads(filtered_messages)
    if not filtered_messages:
        logger.warning(
            "Supervisor model-context summarization produced no retained messages; continuing with unsummarized messages"
        )
        return messages
    return filtered_messages


def _finalize_conversation_segment(
    *,
    stream_writer,
    event_id: UUID | None,
    content: str,
    conversation_started: bool,
    any_delta: bool,
    conversation_messages: list[AIMessage],
    emit: bool = True,
) -> tuple[UUID | None, str, bool, bool]:
    if event_id is None or not conversation_started or not any_delta:
        return None, "", False, False
    if emit:
        stream_writer(ConversationEnded(id=event_id))
    conversation_messages.append(
        AIMessage(
            content=content,
            response_metadata={"stream_type": ["content"], "timestamp": time.time_ns()},
        )
    )
    return None, "", False, False


def _chain_output_messages(output: Any) -> list[Any]:
    if output is None:
        return []
    if isinstance(output, dict):
        messages = output.get("messages")
        return list(messages) if isinstance(messages, list) else []
    messages = getattr(output, "messages", None)
    return list(messages) if isinstance(messages, list) else []


async def run_supervisor_turn(state: SupervisorGraphState, inner_agent: Any) -> dict[str, Any]:
    """One supervisor graph step for chat SSE or muted datapoints extraction.

    Branches on `configurable.mode`:
    - `chat` — stream Conversation*/ReportSearch events; summarize model context.
    - `datapoints` — mute custom SSE, use `detail-extractor` prompt, skip
      summarization, then structure ToolMessage evidence into `key_datapoints`.

    Prompt text comes from `prompt_variant` (`main` | `detail-extractor`).
    """
    reset_report_search_budget()
    config = get_config()
    configurable = dict(config.get("configurable") or {})
    mode = str(configurable.get("mode") or "chat")
    prompt_variant = _resolve_prompt_variant(configurable.get("prompt_variant") or configurable.get("prompt"))
    if mode == "datapoints" and prompt_variant == PROMPT_NAME:
        prompt_variant = DETAIL_EXTRACTOR_PROMPT
    ui_hidden = bool(configurable.get("ui_hidden")) or mode == "datapoints"
    # Datapoints jobs must not emit Conversation* / ReportSearch SSE (and /datapoints/run uses ainvoke).
    mute_stream = ui_hidden or mode == "datapoints"
    runtime_context = SupervisorRuntimeContext(
        project_inventory_context=configurable.get("project_inventory_context"),
        user_display_name=configurable.get("user_display_name"),
        empty_project=bool(configurable.get("empty_project")),
        project_id=str(configurable.get("project_id") or "") or None,
        usable_document_ids=list(configurable.get("usable_document_ids") or []),
        document_catalog=dict(configurable.get("document_catalog") or {}),
        mode=mode,
        prompt_variant=prompt_variant,
        active_document_id=str(configurable.get("document_id") or "") or None,
        active_document_name=(str(configurable.get("document_name") or "").strip() or None),
    )

    with suppress_stream_writes(mute_stream):
        stream_writer = get_stream_writer()
        conversation_messages: list[AIMessage] = []
        event_id: UUID | None = None
        content = ""
        conversation_started = False
        any_delta = False
        active_tool_calls = 0
        last_chain_messages: list[Any] = []

        prompt = load_prompt(AGENT_NAME, SUPERVISOR_NODE, prompt_variant)
        metadata = dict(config.get("metadata") or {})
        metadata["mode"] = mode
        metadata["prompt_variant"] = prompt_variant
        if prompt.langfuse_prompt is not None:
            metadata["langfuse_prompt"] = prompt.langfuse_prompt
        config = {**config, "metadata": metadata}

        model_context_messages = _build_model_context_messages(state)
        summarization_middleware = getattr(inner_agent, "_summarization_middleware", None)
        # Datapoints jobs are one-shot on a dedicated thread — skip summarization noise.
        if mode != "datapoints":
            model_context_messages = await _summarize_model_context_messages(
                model_context_messages,
                summarization_middleware,
            )

        try:
            async for event in inner_agent.astream_events(
                input={"messages": model_context_messages},
                config=config,
                context=runtime_context,
                version="v2",
            ):
                name = event.get("event")
                if name == "on_tool_start":
                    event_id, content, conversation_started, any_delta = _finalize_conversation_segment(
                        stream_writer=stream_writer,
                        event_id=event_id,
                        content=content,
                        conversation_started=conversation_started,
                        any_delta=any_delta,
                        conversation_messages=conversation_messages,
                        emit=not mute_stream,
                    )
                    active_tool_calls += 1
                elif name == "on_tool_end":
                    active_tool_calls = max(0, active_tool_calls - 1)
                elif name == "on_chain_end":
                    messages = _chain_output_messages((event.get("data") or {}).get("output"))
                    if messages:
                        last_chain_messages = messages
                elif name == "on_chat_model_stream" and active_tool_calls == 0:
                    raw_chunk = (event.get("data") or {}).get("chunk")
                    raw_content = getattr(raw_chunk, "content", None) if raw_chunk is not None else None
                    chunk = _normalize_message_content(raw_content) if raw_content else ""
                    if chunk:
                        if event_id is None:
                            event_id = uuid4()
                        if not conversation_started:
                            if not mute_stream:
                                stream_writer(ConversationStarted(id=event_id))
                            conversation_started = True
                        content += chunk
                        if not mute_stream:
                            stream_writer(ConversationDelta(id=event_id, content=chunk))
                        any_delta = True
                elif name == "on_chat_model_end" and active_tool_calls == 0 and not any_delta:
                    raw_output = (event.get("data") or {}).get("output")
                    chunk = _normalize_message_content(
                        getattr(raw_output, "content", None) if raw_output is not None else ""
                    )
                    if chunk:
                        if event_id is None:
                            event_id = uuid4()
                        if not conversation_started:
                            if not mute_stream:
                                stream_writer(ConversationStarted(id=event_id))
                            conversation_started = True
                        content += chunk
                        if not mute_stream:
                            stream_writer(ConversationDelta(id=event_id, content=chunk))
                        any_delta = True
        finally:
            _finalize_conversation_segment(
                stream_writer=stream_writer,
                event_id=event_id,
                content=content,
                conversation_started=conversation_started,
                any_delta=any_delta,
                conversation_messages=conversation_messages,
                emit=not mute_stream,
            )

        events = _collect_tool_events(last_chain_messages)
        conversation_messages = _attach_events_to_conversation(conversation_messages, events)

        key_datapoints: dict[str, Any] | None = None
        datapoints_pages: list[int] = []
        if mode == "datapoints":
            document_id = str(configurable.get("document_id") or "")
            file_name = str(configurable.get("file_name") or document_id or "document")
            job_mode = str(configurable.get("datapoints_job_mode") or "completed")
            if job_mode not in ("ready", "completed", "refresh"):
                job_mode = "completed"
            key_datapoints, datapoints_pages = build_key_datapoints_payload(
                messages=last_chain_messages,
                file_name=file_name,
                document_id=document_id or "unknown",
                job_mode=job_mode,  # type: ignore[arg-type]
            )

        if ui_hidden:
            conversation_messages = [
                message.model_copy(
                    update={"response_metadata": {**(message.response_metadata or {}), "ui_hidden": True}}
                )
                for message in conversation_messages
            ]
            result: dict[str, Any] = {"messages": conversation_messages}
            if key_datapoints is not None:
                result["key_datapoints"] = key_datapoints
                result["datapoints_pages"] = datapoints_pages
            return result

        transcript = list(state.get("messages") or [])
        result = {
            "messages": conversation_messages,
            "model_context_messages": _without_tool_payloads([*model_context_messages, *conversation_messages]),
            "model_context_transcript_count": len(transcript) + len(conversation_messages),
        }
        if key_datapoints is not None:
            result["key_datapoints"] = key_datapoints
            result["datapoints_pages"] = datapoints_pages
        return result


def build_supervisor_graph(checkpointer: Any | None, *, llm: Any | None = None) -> CompiledStateGraph:
    inner_agent = build_inner_agent(llm=llm)

    async def supervisor_agent_node(state: SupervisorGraphState) -> dict[str, Any]:
        return await run_supervisor_turn(state, inner_agent)

    graph = StateGraph(SupervisorGraphState)
    graph.add_node(AGENT_NAME, supervisor_agent_node)
    graph.add_edge(START, AGENT_NAME)
    graph.add_edge(AGENT_NAME, END)
    compiled = graph.compile(checkpointer=checkpointer)
    compiled.name = "supervisor_graph"
    return compiled
