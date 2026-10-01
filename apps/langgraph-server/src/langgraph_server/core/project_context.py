from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class ConversationStarted(BaseModel):
    type: str = "ConversationStarted"
    id: UUID


class ConversationDelta(BaseModel):
    type: str = "ConversationDelta"
    id: UUID
    content: str


class ConversationEnded(BaseModel):
    type: str = "ConversationEnded"
    id: UUID


class ReportSearchSource(BaseModel):
    document_id: str | None = None
    filename: str
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None


class ReportSearchStarted(BaseModel):
    type: str = "ReportSearchStarted"
    id: UUID
    query: str
    document_id: str | None = None
    limit: int


class ReportSearched(BaseModel):
    type: str = "ReportSearched"
    id: UUID
    query: str
    document_id: str | None = None
    limit: int
    count: int = 0
    sources: list[ReportSearchSource] = Field(default_factory=list)


SupervisorMode = Literal["chat", "datapoints"]
SupervisorPromptVariant = Literal["main", "detail-extractor"]


class SupervisorRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    thread_id: UUID
    user_id: UUID
    project_id: UUID
    document_id: UUID | None = None
    document_name: str | None = Field(
        default=None,
        max_length=512,
        description="Filename / title of the document open in the UI, if any.",
    )
    mode: SupervisorMode = Field(
        default="chat",
        description="chat = stream Q&A; datapoints = mute SSE and run structured extract after search.",
    )
    prompt: SupervisorPromptVariant = Field(
        default="main",
        description="Prompt branch under supervisor_agent/supervisor/{prompt}.",
    )
    hidden: bool = Field(
        default=False,
        description="When true, keep messages in checkpoint state but omit them from UI history.",
    )

    @model_validator(mode="after")
    def datapoints_defaults(self) -> "SupervisorRequest":
        if self.mode == "datapoints":
            if self.prompt == "main":
                self.prompt = "detail-extractor"
            self.hidden = True
        return self


class HistoryMessage(BaseModel):
    role: str
    content: str
    events: list[dict[str, Any]] = Field(default_factory=list)
    ui_hidden: bool = False


def format_project_context(snapshot: dict[str, Any]) -> str:
    """Render the project context block from the API agent snapshot."""
    pending = int(snapshot.get("pendingSourcesCount") or snapshot.get("pending_sources_count") or 0)
    sources = snapshot.get("sources") or []
    lines: list[str] = []
    if pending > 0:
        lines.extend(
            [
                "PROJECT CONTEXT STATUS",
                "- Some project materials are still processing.",
                f"- Pending sources: {pending}",
                "- The context below includes only completed/ready materials.",
                "",
            ]
        )
    lines.append("AVAILABLE SOURCES")
    if not sources:
        lines.append("")
        lines.append("No sources available.")
        return "\n".join(lines)

    lines.append("SOURCES:")
    for index, source in enumerate(sources, 1):
        title = source.get("title") or source.get("originalFilename") or "Untitled"
        lines.append(f"\n{index}. {title}")
        lines.append(f"   • document_id: {source.get('id')}")
        if source.get("originalFilename"):
            lines.append(f"   • filename: {source.get('originalFilename')}")
        if source.get("companyName"):
            lines.append(f"   • company: {source.get('companyName')}")
        if source.get("reportYear") is not None:
            lines.append(f"   • report_year: {source.get('reportYear')}")
        if source.get("processStatus"):
            lines.append(f"   • status: {source.get('processStatus')}")
        if source.get("docType"):
            lines.append(f"   • type: {source.get('docType')}")
        summary = source.get("summary") or "No description"
        lines.append(f"   • description: {summary}")
        if source.get("keyDatapoints") or source.get("key_datapoints"):
            lines.append("   • key_datapoints: present")
        if source.get("extractedTextPath"):
            lines.append(f"   • extracted_text_path: {source.get('extractedTextPath')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def usable_document_ids(snapshot: dict[str, Any]) -> list[str]:
    return [str(source["id"]) for source in (snapshot.get("sources") or []) if source.get("id")]


def document_catalog(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for source in snapshot.get("sources") or []:
        source_id = source.get("id")
        if not source_id:
            continue
        catalog[str(source_id)] = {
            "title": source.get("title"),
            "originalFilename": source.get("originalFilename"),
            "companyName": source.get("companyName"),
            "reportYear": source.get("reportYear"),
        }
    return catalog
