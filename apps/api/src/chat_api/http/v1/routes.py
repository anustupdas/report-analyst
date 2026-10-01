from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from chat_api.config import get_settings
from chat_api.deps import CurrentUser, DbDep, SettingsDep, request_ids, require_agent_secret
from chat_api.errors import AppError, ConflictError
from chat_api.http.v1.schemas import (
    CreateProjectRequest,
    CreateUserRequest,
    PrepareDocumentRequest,
    ProjectStateRequest,
    RegenerateTokenRequest,
)
from chat_api.modules import actions
from chat_api.modules.documents.enums import DocumentStatus
from chat_api.modules.models import Document, Project
from chat_api.modules.policies import get_owned_project, get_project_document, pending_sources_count
from chat_api.workflows.ingest import (
    refresh_document_datapoints as run_datapoints_refresh,
)
from chat_api.workflows.ingest import (
    run_document_ingest,
    schedule_datapoints_backfill_if_needed,
)

router = APIRouter()


def _user_response(user, api_token: str | None = None) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "displayName": user.display_name,
        "createdAt": user.created_at.isoformat() if user.created_at else None,
        "apiToken": api_token,
    }


def _project_response(project: Project) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "userId": project.user_id,
        "threadId": project.thread_id,
        "createdAt": project.created_at.isoformat() if project.created_at else None,
        "updatedAt": project.updated_at.isoformat() if project.updated_at else None,
    }


@router.get(
    "/health",
    tags=["Health"],
    summary="Health check",
)
def health() -> dict:
    """Liveness probe for local ops and load balancers.

    No authentication. Returns `{"status": "OK"}` when the process is up.
    Does not verify Postgres or text-extraction connectivity.
    """
    return {"status": "OK"}


@router.post(
    "/users",
    status_code=status.HTTP_201_CREATED,
    tags=["Users"],
    summary="Register a user",
)
def create_user(body: CreateUserRequest, db: DbDep) -> dict:
    """Create a user and issue a one-time API token.

    **Auth:** none (bootstrap).

    **Body**
    - `email` — unique login identity
    - `displayName` — human-readable name

    **Returns** `data` with `id`, `email`, `displayName`, `createdAt`, and
    **`apiToken`**. Copy `apiToken` now — it is stored only as a hash and is
    never returned again (including on `GET /me`).

    Use `Authorization: Bearer <apiToken>` (or Swagger **Authorize**) for all
    other user endpoints.
    """
    user, token = actions.create_user(db, email=body.email, display_name=body.display_name)
    return {"data": _user_response(user, api_token=token)}


@router.post(
    "/users/regenerate-token",
    tags=["Users"],
    summary="Regenerate API token (local recovery)",
)
def regenerate_token(body: RegenerateTokenRequest, db: DbDep) -> dict:
    """Issue a new `apiToken` for an existing email (local prototype only).

    **Auth:** none — intended for clone-and-run demos where logout clears
    browser storage and the old plaintext token cannot be recovered from the
    DB hash.

    The previous token remains accepted until the next regenerate so open
    sessions for the same user keep working. Copy the new `apiToken` from the
    response.
    """
    user, token = actions.regenerate_user_token(db, email=body.email)
    return {"data": _user_response(user, api_token=token)}


@router.get(
    "/me",
    tags=["Users"],
    summary="Current authenticated user",
)
def me(user: CurrentUser) -> dict:
    """Return the user that owns the Bearer token.

    **Auth:** required (`HTTPBearer`).

    `apiToken` is always `null` here — tokens are only shown at registration
    or regenerate-token.
    """
    return {"data": _user_response(user)}


@router.post(
    "/projects",
    status_code=status.HTTP_201_CREATED,
    tags=["Projects"],
    summary="Create a project (tab)",
)
def create_project(body: CreateProjectRequest, db: DbDep, user: CurrentUser) -> dict:
    """Create a workspace / tab owned by the current user.

    **Auth:** required.

    A project scopes uploads, artifacts, vector filters, and chat threads.
    """
    project = actions.create_project(db, user=user, name=body.name)
    return {"data": _project_response(project)}


@router.get(
    "/projects",
    tags=["Projects"],
    summary="List my projects",
)
def list_projects(db: DbDep, user: CurrentUser) -> dict:
    """List all projects owned by the current user (newest first).

    **Auth:** required.
    """
    projects = (
        db.execute(select(Project).where(Project.user_id == user.id).order_by(Project.created_at.desc()))
        .scalars()
        .all()
    )
    return {"data": [_project_response(p) for p in projects]}


@router.get(
    "/projects/{project_id}",
    tags=["Projects"],
    summary="Get a project",
)
def get_project(project_id: UUID, db: DbDep, user: CurrentUser) -> dict:
    """Fetch one project you own, including ingest backlog.

    **Auth:** required. Returns 403/404 if the project is missing or not yours.

    Extra field: `pendingSourcesCount` — documents in `pending` or `processing`.
    """
    project = get_owned_project(db, project_id=project_id, user_id=user.id)
    return {
        "data": {
            **_project_response(project),
            "pendingSourcesCount": pending_sources_count(db, project.id),
        }
    }


@router.post(
    "/me/projects/state",
    tags=["Projects"],
    summary="Batch project state for UI / agent",
)
def me_projects_state(body: ProjectStateRequest, db: DbDep, user: CurrentUser) -> dict:
    """Return state for up to 20 project IDs (unowned IDs are silently dropped).

    **Auth:** required.

    Each item includes `pendingSourcesCount` and only **usable** documents
    (`ready` | `completed`) in `sources`.
    """
    return {"data": actions.build_project_state(db, user_id=user.id, project_ids=body.ids)}


@router.post(
    "/projects/{project_id}/documents/prepare",
    status_code=status.HTTP_201_CREATED,
    tags=["Documents"],
    summary="Prepare document upload",
)
def prepare_document(
    project_id: UUID,
    body: PrepareDocumentRequest,
    db: DbDep,
    user: CurrentUser,
    settings: SettingsDep,
) -> dict:
    """Create a pending document row and local folder.

    **Auth:** required. Extension must be in the server allowlist.

    **Body**
    - `filename` — original name (used for extension / mime)
    - `title` — optional display title (defaults to filename)

    **Returns** a document with `processStatus: pending` and `uploadUrl`
    pointing at `PUT .../content`. Does not accept file bytes yet.
    """
    document = actions.prepare_document(
        db,
        user=user,
        project_id=project_id,
        filename=body.filename,
        title=body.title,
        settings=settings,
    )
    resource = actions.document_to_resource(document)
    resource["uploadUrl"] = f"/api/v1/projects/{project_id}/documents/{document.id}/content"
    return {"data": resource}


@router.put(
    "/projects/{project_id}/documents/{document_id}/content",
    tags=["Documents"],
    summary="Upload document bytes",
)
def upload_document_content(
    project_id: UUID,
    document_id: UUID,
    db: DbDep,
    user: CurrentUser,
    settings: SettingsDep,
    request: Request,
    file: UploadFile = File(..., description="Document file (e.g. annual-report PDF)"),
) -> dict:
    """Upload the file for a prepared document (local stand-in for S3 PUT).

    **Auth:** required. Multipart field name: `file`.

    Writes under `data/{userId}/{projectId}/{documentId}/` and records an
    `artifacts` row. Allowed only while status is `pending` or `failed`.
    Enforces max upload size from settings.

    Sync on purpose: the body is already buffered, and the save is a blocking
    disk write plus a database commit. FastAPI runs this handler in a worker
    thread, so the event loop stays free for polling and other requests.
    """
    data = file.file.read()
    request_id, _ = request_ids(request)
    document = actions.save_document_content(
        db,
        user=user,
        project_id=project_id,
        document_id=document_id,
        filename=file.filename or "upload.bin",
        data=data,
        settings=settings,
        request_id=request_id,
    )
    return {"data": actions.document_to_resource(document)}


@router.get(
    "/projects/{project_id}/documents/{document_id}/original",
    tags=["Documents"],
    summary="Download or inline-preview the original upload",
    response_class=FileResponse,
)
def get_document_original(
    project_id: UUID,
    document_id: UUID,
    db: DbDep,
    user: CurrentUser,
    settings: SettingsDep,
) -> FileResponse:
    """Stream the original uploaded file for inline PDF preview.

    **Auth:** required. Served with `Content-Disposition: inline` so the browser
    can embed it in a PDF preview.
    """
    document, artifact, path = actions.resolve_original_file(
        db,
        user=user,
        project_id=project_id,
        document_id=document_id,
        settings=settings,
    )
    media_type = artifact.mime_type or document.mime_type or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=document.original_filename,
        content_disposition_type="inline",
    )


@router.post(
    "/projects/{project_id}/documents/{document_id}/process",
    tags=["Documents"],
    summary="Start ingest / extraction workflow",
)
def process_document(
    project_id: UUID,
    document_id: UUID,
    background_tasks: BackgroundTasks,
    db: DbDep,
    user: CurrentUser,
    request: Request,
) -> dict:
    """Atomically claim the document and run the local ingest pipeline.

    **Auth:** required.

    Requires an uploaded original artifact. Status must be `pending` or
    `failed` (else 409). On success returns immediately with
    `processStatus: processing` while a background job:

    1. calls text-extraction
    2. writes the text and layout → status `extracted`
    3. embeds in batches; status `ready` when the first batch is stored (searchable,
       indexing continues) and `completed` when every batch is stored

    Poll `GET .../documents/{document_id}` for progress.
    """
    document = actions.trigger_processing(db, user=user, project_id=project_id, document_id=document_id)
    request_id, session_id = request_ids(request)
    background_tasks.add_task(
        run_document_ingest,
        document_id=document.id,
        request_id=request_id,
        session_id=session_id,
    )
    return {"data": actions.document_to_resource(document)}


@router.get(
    "/projects/{project_id}/documents",
    tags=["Documents"],
    summary="List documents in a project",
)
def list_documents(
    project_id: UUID,
    db: DbDep,
    user: CurrentUser,
    settings: SettingsDep,
    status_filter: list[str] | None = Query(
        default=None,
        alias="status",
        description=(
            "Repeatable filter: pending, processing, extracted, ingesting, ready, completed, failed. "
            "Default excludes failed."
        ),
    ),
) -> dict:
    """List documents for a project you own.

    **Auth:** required.

    Also marks stale `processing` docs as `failed` after the configured timeout
    (default 14 minutes).
    """
    project = get_owned_project(db, project_id=project_id, user_id=user.id)
    actions.fail_stale_processing(db, minutes=settings.stale_processing_minutes)

    allowed = {s.value for s in DocumentStatus}
    if status_filter:
        statuses = [s for s in status_filter if s in allowed]
    else:
        # Default list: exclude failed
        statuses = [
            DocumentStatus.PENDING.value,
            DocumentStatus.PROCESSING.value,
            DocumentStatus.EXTRACTED.value,
            DocumentStatus.INGESTING.value,
            DocumentStatus.READY.value,
            DocumentStatus.COMPLETED.value,
        ]

    docs = (
        db.execute(
            select(Document)
            .where(Document.project_id == project_id, Document.status.in_(statuses))
            .order_by(Document.created_at.desc())
        )
        .scalars()
        .all()
    )
    for document in docs:
        schedule_datapoints_backfill_if_needed(
            project_id=project.id,
            document_id=document.id,
            user_id=user.id,
            thread_id=project.thread_id,
            file_name=document.original_filename,
            key_datapoints=getattr(document, "key_datapoints", None),
            status=document.status,
        )
    return {"data": [actions.document_to_resource(d) for d in docs]}


@router.get(
    "/projects/{project_id}/documents/{document_id}",
    tags=["Documents"],
    summary="Get one document",
)
def get_document(project_id: UUID, document_id: UUID, db: DbDep, user: CurrentUser) -> dict:
    """Fetch a single document (poll this after `process`).

    **Auth:** required. Document must belong to the given project and to you.

    Key field: `processStatus` —
    `pending` → `processing` → `extracted` → `ingesting` → `ready` → `completed`
    (or `failed` + `error`). `ready` means the first pages are searchable while
    later batches are still being stored.
    """
    document = get_project_document(db, project_id=project_id, document_id=document_id, user_id=user.id)
    return {"data": actions.document_to_resource(document)}


@router.post(
    "/projects/{project_id}/documents/{document_id}/datapoints/refresh",
    tags=["Documents"],
    summary="Re-run key datapoints extraction",
)
def refresh_document_datapoints(
    project_id: UUID,
    document_id: UUID,
    db: DbDep,
    user: CurrentUser,
) -> dict:
    """Run the datapoints job (non-streaming) and persist key_datapoints.

    **Auth:** required. Document must be `ready` or `completed` (searchable index exists).
    While `ready`, only currently indexed chunks are searched. May take up to a few minutes.
    """
    project = get_owned_project(db, project_id=project_id, user_id=user.id)
    document = get_project_document(db, project_id=project_id, document_id=document_id, user_id=user.id)
    if document.status not in (DocumentStatus.READY.value, DocumentStatus.COMPLETED.value):
        raise ConflictError("Key datapoints refresh needs a searchable index (ready or completed).")

    result = run_datapoints_refresh(
        project_id=project.id,
        document_id=document.id,
        user_id=user.id,
        thread_id=project.thread_id,
        file_name=document.original_filename,
        mode="ready" if document.status == DocumentStatus.READY.value else "refresh",
    )
    if not result or not result.get("ok"):
        raise AppError(
            "Key datapoints refresh failed. Try again in a moment.", status_code=502, code="datapoints_refresh_failed"
        )

    db.refresh(document)
    return {"data": actions.document_to_resource(document)}


@router.delete(
    "/projects/{project_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Documents"],
    summary="Delete a document",
)
def delete_document(
    project_id: UUID,
    document_id: UUID,
    db: DbDep,
    user: CurrentUser,
    settings: SettingsDep,
) -> None:
    """Delete the document row, artifacts metadata, and on-disk folder.

    **Auth:** required. Response body is empty (`204`).
    """
    actions.delete_document(db, user=user, project_id=project_id, document_id=document_id, settings=settings)


@router.get(
    "/agent/projects/{project_id}",
    tags=["Agent"],
    summary="Project snapshot for LangGraph / agents",
    dependencies=[Depends(require_agent_secret)],
)
def agent_project_state(project_id: UUID, db: DbDep) -> dict:
    """Service-to-service project state for the LangGraph agent.

    **Auth:** `X-Agent-Secret` header (not the user Bearer token). Fails closed
    if `AGENT_SECRET` is unset.

    Returns the same shape as one item from `/me/projects/state`, plus `userId`,
    `threadId`, and artifact paths on each source (usable documents only).
    """
    project = db.get(Project, project_id)
    if project is None:
        from chat_api.errors import NotFoundError

        raise NotFoundError("Project not found")

    settings = get_settings()
    actions.fail_stale_processing(db, minutes=settings.stale_processing_minutes)
    state = actions.build_project_state(db, user_id=project.user_id, project_ids=[project_id])
    return {"data": state[0] if state else None}
