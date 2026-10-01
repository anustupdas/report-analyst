"""Non-streaming key-datapoints job via the shared supervisor graph.

`POST /datapoints/run` (agent secret) ainvokes the same compiled graph as chat,
with mode=datapoints on a throwaway thread_id so project chat history stays clean.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from langgraph_server.config import Settings, get_settings
from langgraph_server.core.api_client import ApiClient, ApiClientError
from langgraph_server.core.key_datapoints import job_message
from langgraph_server.core.langfuse_client import create_langfuse_callback, flush_langfuse, langfuse_trace_scope
from langgraph_server.core.project_context import document_catalog, usable_document_ids
from langgraph_server.core.supervisor.constants import DETAIL_EXTRACTOR_PROMPT
from langgraph_server.http.auth import snapshot_configurable

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/datapoints")

DatapointsMode = Literal["ready", "completed", "refresh"]


class RunDatapointsRequest(BaseModel):
    project_id: UUID
    document_id: UUID
    user_id: UUID | None = None
    file_name: str | None = None
    mode: DatapointsMode = Field(
        default="completed",
        description="ready = early index; completed/refresh = full index job message.",
    )
    prompt: Literal["detail-extractor"] = Field(
        default="detail-extractor",
        description="Supervisor prompt branch (supervisor_agent/supervisor/{prompt}).",
    )


def _require_agent_secret(
    settings: Annotated[Settings, Depends(get_settings)],
    x_agent_secret: Annotated[str | None, Header(alias="X-Agent-Secret")] = None,
) -> None:
    expected = (settings.agent_secret or "").strip()
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent auth is not configured")
    if not x_agent_secret or x_agent_secret != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent secret")


def _graph(request: Request) -> CompiledStateGraph:
    graph = getattr(request.app.state, "supervisor_graph", None)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Supervisor graph is not ready")
    return graph


@router.post("/run")
async def run_datapoints(
    body: RunDatapointsRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    _: Annotated[None, Depends(_require_agent_secret)],
) -> dict[str, Any]:
    """Run supervisor in datapoints mode (no SSE) and return structured JSON."""
    client = ApiClient(settings)
    try:
        snapshot = await client.get_project_snapshot(body.project_id)
    except ApiClientError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc
    if not snapshot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    catalog = document_catalog(snapshot)
    usable = usable_document_ids(snapshot)
    doc_id = str(body.document_id)
    if doc_id not in usable and doc_id not in catalog:
        sources = snapshot.get("sources") or []
        if not any(str(s.get("id")) == doc_id for s in sources):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not in project")
        usable = list({*usable, doc_id})

    meta = catalog.get(doc_id) or {}
    file_name = body.file_name or meta.get("originalFilename") or doc_id
    if doc_id not in catalog:
        catalog = {
            **catalog,
            doc_id: {"originalFilename": file_name, "title": file_name},
        }

    user = {"id": str(body.user_id) if body.user_id else None, "displayName": None}
    extra = snapshot_configurable(snapshot, user=user)
    extra["usable_document_ids"] = usable or [doc_id]
    extra["document_catalog"] = catalog
    extra["empty_project"] = not (usable or [doc_id])

    # Dedicated thread so project chat history stays clean.
    thread_id = str(uuid4())
    callbacks = []
    handler = create_langfuse_callback()
    if handler is not None:
        callbacks.append(handler)

    prompt_variant = body.prompt or DETAIL_EXTRACTOR_PROMPT
    config = {
        "configurable": {
            "thread_id": thread_id,
            "user_id": str(body.user_id) if body.user_id else None,
            "project_id": str(body.project_id),
            "document_id": doc_id,
            "file_name": str(file_name),
            "mode": "datapoints",
            "prompt_variant": prompt_variant,
            "datapoints_job_mode": body.mode,
            "ui_hidden": True,
            **extra,
        },
        "callbacks": callbacks,
        "metadata": {
            "user_id": str(body.user_id) if body.user_id else None,
            "project_id": str(body.project_id),
            "thread_id": thread_id,
            "document_id": doc_id,
            "mode": "datapoints",
            "prompt_variant": prompt_variant,
            "datapoints_job_mode": body.mode,
            "ui_hidden": True,
        },
        "run_name": "supervisor",
    }
    human = HumanMessage(
        content=job_message(document_id=doc_id, file_name=str(file_name), mode=body.mode),
        additional_kwargs={"ui_hidden": True},
        response_metadata={"ui_hidden": True},
    )

    try:
        with langfuse_trace_scope(
            name="datapoints",
            user_id=str(body.user_id) if body.user_id else None,
            session_id=f"datapoints:{doc_id}:{body.mode}",
            input=human.content,
            metadata={
                "project_id": str(body.project_id),
                "document_id": doc_id,
                "mode": body.mode,
                "prompt_variant": prompt_variant,
            },
            tags=["datapoints", body.mode, prompt_variant],
        ) as root:
            # ainvoke — never StreamingResponse — so the client gets no SSE.
            result = await _graph(request).ainvoke(
                {"messages": [human]},
                config=config,
            )
            payload = result.get("key_datapoints") if isinstance(result, dict) else None
            pages = result.get("datapoints_pages") if isinstance(result, dict) else None
            response = {
                "ok": True,
                "documentId": doc_id,
                "mode": body.mode,
                "prompt": prompt_variant,
                "pages": pages or [],
                "keyDatapoints": payload or {"fte": None, "sustainability_goals": []},
            }
            if root is not None:
                try:
                    root.update(output=response)
                except Exception:
                    logger.debug("Langfuse datapoints root update failed", exc_info=True)
            return response
    except Exception as exc:
        logger.exception("datapoints run failed document_id=%s mode=%s", body.document_id, body.mode)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    finally:
        flush_langfuse()
