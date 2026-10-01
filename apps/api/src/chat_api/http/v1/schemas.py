from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field, model_validator


class CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class CreateUserRequest(BaseModel):
    """Payload for `POST /users`."""

    email: EmailStr = Field(..., description="Unique email used as login identity.")
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        validation_alias=AliasChoices("displayName", "display_name"),
        description="Human-readable name shown in the UI.",
        examples=["Poka"],
    )

    model_config = ConfigDict(populate_by_name=True)


class RegenerateTokenRequest(BaseModel):
    """Payload for `POST /users/regenerate-token` (local prototype recovery)."""

    email: EmailStr = Field(..., description="Email of an existing user.")

    model_config = ConfigDict(populate_by_name=True)


class UserResponse(CamelModel):
    id: UUID
    email: str
    display_name: str = Field(serialization_alias="displayName")
    created_at: Optional[str] = Field(None, serialization_alias="createdAt")
    api_token: Optional[str] = Field(
        None,
        serialization_alias="apiToken",
        description="Plaintext token returned only at registration. Store it securely.",
    )


class CreateProjectRequest(BaseModel):
    """Payload for `POST /projects`."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Workspace / tab name (e.g. 'Shell annual report 2024').",
        examples=["Project1"],
    )


class ProjectResponse(CamelModel):
    id: UUID
    name: str
    user_id: UUID = Field(serialization_alias="userId")
    thread_id: UUID = Field(serialization_alias="threadId")
    created_at: Optional[str] = Field(None, serialization_alias="createdAt")
    updated_at: Optional[str] = Field(None, serialization_alias="updatedAt")


class PrepareDocumentRequest(BaseModel):
    """Payload for `POST .../documents/prepare`."""

    filename: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Original filename including extension (e.g. shell.pdf).",
        examples=["shell-annual-report.pdf"],
    )
    title: Optional[str] = Field(
        None,
        max_length=512,
        description="Optional display title; defaults to filename.",
    )


class DocumentResource(CamelModel):
    id: str
    project_id: str = Field(serialization_alias="projectId")
    title: str
    original_filename: str = Field(serialization_alias="originalFilename")
    mime_type: str = Field(serialization_alias="mimeType")
    process_status: str = Field(
        serialization_alias="processStatus",
        description="pending | processing | extracted | ingesting | ready | completed | failed",
    )
    company_name: Optional[str] = Field(None, serialization_alias="companyName")
    report_year: Optional[int] = Field(None, serialization_alias="reportYear")
    doc_type: Optional[str] = Field(
        None,
        serialization_alias="docType",
        description="annual_report | other; null until classified",
    )
    summary: Optional[str] = Field(None, description="Short description of what the document is about.")
    error: Optional[dict[str, Any]] = None
    created_at: Optional[str] = Field(None, serialization_alias="createdAt")
    updated_at: Optional[str] = Field(None, serialization_alias="updatedAt")
    created_by: Optional[dict[str, str]] = Field(None, serialization_alias="createdBy")
    upload_url: Optional[str] = Field(
        None,
        serialization_alias="uploadUrl",
        description="Relative URL for PUT content upload (local stand-in for S3 presign).",
    )


class DataEnvelope(CamelModel):
    data: Any


class ProjectStateRequest(BaseModel):
    """Payload for `POST /me/projects/state`."""

    ids: list[UUID] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Project UUIDs to resolve (max 20). Unowned ids are dropped.",
    )


class ProjectStateItem(CamelModel):
    id: str
    name: str
    pending_sources_count: int = Field(serialization_alias="pendingSourcesCount")
    sources: list[dict[str, Any]]


class ChunkUpsertItem(BaseModel):
    content: str = Field(..., min_length=1)
    chunk_index: int | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("chunkIndex", "chunk_index"),
    )
    page_start: int | None = Field(
        default=None,
        validation_alias=AliasChoices("pageStart", "page_start"),
    )
    page_end: int | None = Field(
        default=None,
        validation_alias=AliasChoices("pageEnd", "page_end"),
    )
    embedding: list[float] | None = None

    model_config = ConfigDict(populate_by_name=True)


class UpsertChunksRequest(BaseModel):
    """Add or update chunk rows for one document (embeds missing vectors)."""

    document_id: UUID = Field(
        ...,
        validation_alias=AliasChoices("documentId", "document_id"),
    )
    chunks: list[ChunkUpsertItem] = Field(..., min_length=1, max_length=500)
    replace: bool = Field(
        default=False,
        description="If true, delete existing chunks for the document first.",
    )

    model_config = ConfigDict(populate_by_name=True)


class SearchChunksRequest(BaseModel):
    """Search by query text (embedded here) or a precomputed embedding."""

    query: str | None = Field(default=None, min_length=1, max_length=8000)
    embedding: list[float] | None = None
    document_id: UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("documentId", "document_id"),
    )
    limit: int | None = Field(default=None, ge=1, le=50)

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def require_query_or_embedding(self) -> "SearchChunksRequest":
        has_query = bool(self.query and self.query.strip())
        has_embedding = bool(self.embedding)
        if has_query == has_embedding:
            raise ValueError("Provide exactly one of query or embedding")
        return self
