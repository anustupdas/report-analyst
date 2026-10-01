from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from typing import Literal
from uuid import UUID

from sqlalchemy import select

from chat_api.config import get_settings
from chat_api.db import session_scope
from chat_api.modules import vectors as vector_actions
from chat_api.modules.documents.enums import ArtifactIdentifier, DocumentStatus
from chat_api.modules.models import Artifact, Document, Project
from chat_api.services.chunking import PageText, TextChunk, chunk_pages
from chat_api.services.document_description import DescriptionUnavailable, DocumentDescriber
from chat_api.services.embedding import EmbeddingClient
from chat_api.services.langgraph_datapoints import LangGraphDatapointsClient
from chat_api.services.placeholders import PlaceholderDocumentClassifier
from chat_api.services.storage import LocalStorageService
from chat_api.services.text_extraction import TextExtractionClient
from chat_api.services.vectors import ChunkRecord, VectorStore
from chat_api.tracing import structured_log

logger = logging.getLogger(__name__)

# One embedding-service request. Each batch is stored before the next is sent,
# so the first pages are searchable while the rest of the report is still embedding.
EMBED_BATCH_SIZE = 32

DatapointsJobMode = Literal["ready", "completed", "refresh"]

# Serialize per-document LangGraph datapoints jobs so ready/completed/backfill/refresh
# cannot race on read-modify-write merges into documents.key_datapoints.
_datapoints_job_lock = threading.Lock()
_datapoints_jobs_inflight: set[UUID] = set()
_datapoints_job_cv = threading.Condition(_datapoints_job_lock)


def _normalize_datapoints_mode(mode: str) -> DatapointsJobMode:
    if mode in ("ready", "completed", "refresh"):
        return mode  # type: ignore[return-value]
    return "completed"


def _acquire_datapoints_job(document_id: UUID, *, wait: bool, timeout_s: float = 600.0) -> bool:
    """Exclusive lease for one document's datapoints job. Returns False if skipped/timed out."""
    deadline = time.monotonic() + timeout_s
    with _datapoints_job_cv:
        while document_id in _datapoints_jobs_inflight:
            if not wait:
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _datapoints_job_cv.wait(timeout=min(remaining, 1.0))
        _datapoints_jobs_inflight.add(document_id)
        return True


def _release_datapoints_job(document_id: UUID) -> None:
    with _datapoints_job_cv:
        _datapoints_jobs_inflight.discard(document_id)
        _datapoints_job_cv.notify_all()


def run_document_ingest(
    *,
    document_id: UUID,
    request_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """
    Ingest one uploaded file in this process.

    extract → artifacts → extracted
    → classify (placeholder) → chunk → ingesting
    → describe (parallel, non-fatal) while embeddings stream in batches of 32
    → ready as soon as the first batch is stored
    → completed when every batch is stored
    """
    settings = get_settings()
    storage = LocalStorageService(settings)
    extractor = TextExtractionClient(settings)
    classifier = PlaceholderDocumentClassifier(settings)
    describer = DocumentDescriber(settings)
    embedder = EmbeddingClient(settings)
    vectors = VectorStore(provider=settings.embedding_provider)

    with session_scope() as db:
        document = db.get(Document, document_id)
        if document is None:
            structured_log(logger, "ingest.missing_document", document_id=document_id)
            return

        structured_log(
            logger,
            "ingest.started",
            request_id=request_id,
            document_id=document_id,
            project_id=document.project_id,
            user_id=document.user_id,
        )

        try:
            original = db.execute(
                select(Artifact).where(
                    Artifact.document_id == document.id,
                    Artifact.identifier.in_(
                        [
                            ArtifactIdentifier.ORIGINAL_PDF.value,
                            ArtifactIdentifier.ORIGINAL.value,
                        ]
                    ),
                )
            ).scalar_one_or_none()
            if original is None:
                raise RuntimeError("Missing original upload artifact")

            file_ref = original.storage_path
            extracted = extractor.extract_file(
                file_ref,
                request_id=request_id,
                session_id=session_id,
            )
            text = extracted.get("text") or ""
            if not text.strip():
                raise RuntimeError("Extraction returned empty text")

            _path, rel, size = storage.write_text_artifact(
                user_id=document.user_id,
                project_id=document.project_id,
                document_id=document.id,
                identifier=ArtifactIdentifier.EXTRACTED_TEXT,
                text=text,
                request_id=request_id,
            )
            _upsert_artifact(
                db,
                document_id=document.id,
                identifier=ArtifactIdentifier.EXTRACTED_TEXT.value,
                storage_path=rel,
                mime_type="text/plain",
                size_bytes=size,
            )
            _path, rel, size = storage.write_layout_artifact(
                user_id=document.user_id,
                project_id=document.project_id,
                document_id=document.id,
                extraction=extracted,
                request_id=request_id,
            )
            _upsert_artifact(
                db,
                document_id=document.id,
                identifier=ArtifactIdentifier.EXTRACTED_LAYOUT.value,
                storage_path=rel,
                mime_type="application/json",
                size_bytes=size,
            )

            _set_status(db, document, DocumentStatus.EXTRACTED)

            try:
                document.doc_type = classifier.classify(text, request_id=request_id)
                db.commit()
            except NotImplementedError:
                structured_log(logger, "ingest.classify_skipped", request_id=request_id, document_id=document_id)

            windows = chunk_pages(
                _pages_from_extraction(extracted, text),
                target_chars=settings.chunk_target_chars,
                max_chars=settings.chunk_max_chars,
                overlap_sentences=settings.chunk_overlap_sentences,
                text_format="markdown" if extracted.get("text_format") == "markdown" else "plain",
            )
            if not windows:
                raise RuntimeError("No text chunks produced for embedding")

            _set_status(db, document, DocumentStatus.INGESTING)
            # Re-processing replaces the index. New batches are inserted after this delete.
            vectors.delete_document_chunks(db, document_id=document.id, request_id=request_id)

            # Own DB session: the ingest session is not safe to share across threads.
            describer_thread = threading.Thread(
                target=_describe_document,
                kwargs={
                    "document_id": document.id,
                    "file_name": document.original_filename,
                    "user_id": document.user_id,
                    "project_id": document.project_id,
                    "describer": describer,
                    "storage": storage,
                    "chunks": windows,
                    "request_id": request_id,
                },
                name=f"ingest-describe-{document.id}",
            )
            describer_thread.start()
            try:
                stored = stream_embeddings(
                    windows,
                    batch_size=EMBED_BATCH_SIZE,
                    embed=lambda texts: embedder.embed_texts(
                        texts,
                        input_type="passage",
                        request_id=request_id,
                        session_id=session_id,
                    ),
                    store=lambda batch, batch_vectors, first: _store_embedding_batch(
                        db,
                        document,
                        batch=batch,
                        vectors=batch_vectors,
                        model_name=settings.embedding_model_name,
                        vector_store=vectors,
                        request_id=request_id,
                        first=first,
                    ),
                )
            except Exception:
                structured_log(logger, "ingest.embed_failed", request_id=request_id, document_id=document_id)
                raise
            finally:
                describer_thread.join()

            project = db.get(Project, document.project_id)
            thread_id = project.thread_id if project is not None else None
            _set_status(db, document, DocumentStatus.COMPLETED)
            structured_log(
                logger,
                "ingest.embedded",
                request_id=request_id,
                document_id=document_id,
                chunks=stored,
            )
            structured_log(
                logger,
                "ingest.completed",
                request_id=request_id,
                document_id=document_id,
                chars=len(text),
            )
            if thread_id is not None:
                threading.Thread(
                    target=_trigger_completed_datapoints_pass,
                    kwargs={
                        "project_id": document.project_id,
                        "document_id": document.id,
                        "user_id": document.user_id,
                        "thread_id": thread_id,
                        "file_name": document.original_filename,
                        "request_id": request_id,
                    },
                    name=f"ingest-datapoints-completed-{document.id}",
                    daemon=True,
                ).start()

        except Exception as exc:
            db.rollback()
            document = db.get(Document, document_id)
            if document is not None:
                document.status = DocumentStatus.FAILED.value
                document.error_message = str(exc)[:2000]
                db.commit()
            structured_log(
                logger,
                "ingest.failed",
                request_id=request_id,
                document_id=document_id,
                error=type(exc).__name__,
            )
            logger.exception("ingest failed document_id=%s", document_id)


def stream_embeddings(
    windows: Sequence[TextChunk],
    *,
    batch_size: int,
    embed: Callable[[list[str]], list[list[float]]],
    store: Callable[[Sequence[TextChunk], list[list[float]], bool], None],
) -> int:
    """Embed in order, handing each batch to `store` before the next request.

    `store` receives `(batch, vectors, first)` where `first` is true only for the
    opening batch — that is the moment the document becomes searchable.
    """
    stored = 0
    for start in range(0, len(windows), batch_size):
        batch = list(windows[start : start + batch_size])
        vectors = embed([window.embed_text for window in batch])
        if len(vectors) != len(batch):
            raise RuntimeError(f"Embedding returned {len(vectors)} vectors for {len(batch)} chunks")
        store(batch, vectors, stored == 0)
        stored += len(batch)
    return stored


def _store_embedding_batch(
    db,
    document: Document,
    *,
    batch: Sequence[TextChunk],
    vectors: list[list[float]],
    model_name: str,
    vector_store: VectorStore,
    request_id: str | None,
    first: bool,
) -> None:
    records = [
        ChunkRecord(
            document_id=document.id,
            project_id=document.project_id,
            user_id=document.user_id,
            chunk_index=window.chunk_index,
            content=window.content,
            section=window.section,
            page_start=window.page_start,
            page_end=window.page_end,
            embedding=vector,
            token_count=window.token_count,
            model_name=model_name,
        )
        for window, vector in zip(batch, vectors)
    ]
    vector_store.upsert_chunks(db, records, request_id=request_id)
    if first:
        _set_status(db, document, DocumentStatus.READY)
        structured_log(
            logger,
            "ingest.searchable",
            request_id=request_id,
            document_id=document.id,
            chunks=len(records),
        )
        threading.Thread(
            target=_trigger_ready_datapoints_extract,
            kwargs={
                "project_id": document.project_id,
                "document_id": document.id,
                "user_id": document.user_id,
                "file_name": document.original_filename,
                "request_id": request_id,
            },
            name=f"ingest-datapoints-ready-{document.id}",
            daemon=True,
        ).start()


def _trigger_ready_datapoints_extract(
    *,
    project_id: UUID,
    document_id: UUID,
    user_id: UUID,
    file_name: str,
    request_id: str | None,
) -> None:
    """Early extract against the partial index (first embed batch → status ready)."""
    if not _acquire_datapoints_job(document_id, wait=False):
        structured_log(
            logger,
            "ingest.datapoints_ready_skipped",
            request_id=request_id,
            document_id=document_id,
            reason="job_inflight",
        )
        return
    try:
        settings = get_settings()
        client = LangGraphDatapointsClient(settings)
        if not client.enabled:
            structured_log(
                logger,
                "ingest.datapoints_ready_skipped",
                request_id=request_id,
                document_id=document_id,
                reason="langgraph_not_configured",
            )
            return
        try:
            result = client.run(
                project_id=project_id,
                document_id=document_id,
                user_id=user_id,
                file_name=file_name,
                mode="ready",
            )
            ok = _persist_datapoints_result(
                project_id=project_id,
                document_id=document_id,
                user_id=user_id,
                result=result,
            )
            structured_log(
                logger,
                "ingest.datapoints_ready",
                request_id=request_id,
                document_id=document_id,
                ok=ok,
                pages=(result or {}).get("pages"),
            )
        except Exception:
            logger.exception("ready datapoints extract failed document_id=%s", document_id)
    finally:
        _release_datapoints_job(document_id)


def _trigger_completed_datapoints_pass(
    *,
    project_id: UUID,
    document_id: UUID,
    user_id: UUID,
    thread_id: UUID,
    file_name: str,
    request_id: str | None,
    mode: str = "completed",
    wait_for_slot: bool = True,
) -> dict | None:
    """Run supervisor datapoints mode and merge into documents.key_datapoints.

    `mode` is forwarded to LangGraph as-is (`ready` | `completed` | `refresh`).
    When `wait_for_slot` is true (ingest completed + UI refresh), wait for any
    in-flight job on this document (e.g. the ready pass) so merges do not race.
    Backfill uses wait_for_slot=False and skips if another job already holds the lease.
    """
    job_mode = _normalize_datapoints_mode(mode)
    if not _acquire_datapoints_job(document_id, wait=wait_for_slot):
        structured_log(
            logger,
            "ingest.datapoints_completed_skipped",
            request_id=request_id,
            document_id=document_id,
            reason="job_inflight",
            mode=job_mode,
        )
        return None
    try:
        settings = get_settings()
        client = LangGraphDatapointsClient(settings)
        if not client.enabled:
            structured_log(
                logger,
                "ingest.datapoints_completed_skipped",
                request_id=request_id,
                document_id=document_id,
                reason="langgraph_not_configured",
            )
            return None
        try:
            result = client.run(
                project_id=project_id,
                document_id=document_id,
                user_id=user_id,
                file_name=file_name,
                mode=job_mode,
            )
            ok = _persist_datapoints_result(
                project_id=project_id,
                document_id=document_id,
                user_id=user_id,
                result=result,
            )
            structured_log(
                logger,
                "ingest.datapoints_completed",
                request_id=request_id,
                document_id=document_id,
                ok=ok,
                mode=job_mode,
                project_thread_id=str(thread_id),
                pages=(result or {}).get("pages"),
            )
            return result if ok else None
        except Exception:
            logger.exception("completed datapoints pass failed document_id=%s", document_id)
            return None
    finally:
        _release_datapoints_job(document_id)


def _persist_datapoints_result(
    *,
    project_id: UUID,
    document_id: UUID,
    user_id: UUID,
    result: dict | None,
) -> bool:
    """Merge LangGraph keyDatapoints into the document row (additive FTE/goals merge)."""
    if not result or not result.get("ok"):
        return False
    payload = result.get("keyDatapoints") or result.get("key_datapoints")
    if not isinstance(payload, dict):
        return False
    with session_scope() as db:
        vector_actions.update_document_datapoints(
            db,
            user_id=user_id,
            project_id=project_id,
            document_id=document_id,
            key_datapoints=payload,
        )
    return True


def refresh_document_datapoints(
    *,
    project_id: UUID,
    document_id: UUID,
    user_id: UUID,
    thread_id: UUID,
    file_name: str,
    mode: str = "refresh",
) -> dict | None:
    """Synchronously re-run datapoints extraction and persist key_datapoints.

    For documents still in `ready`, pass mode=`ready` so LangGraph searches only
    the partial index and can attach the early-index status_note when empty.
    """
    job_mode = _normalize_datapoints_mode("ready" if mode == "ready" else "refresh")
    return _trigger_completed_datapoints_pass(
        project_id=project_id,
        document_id=document_id,
        user_id=user_id,
        thread_id=thread_id,
        file_name=file_name,
        request_id="datapoints-refresh",
        mode=job_mode,
        wait_for_slot=True,
    )


def schedule_datapoints_backfill_if_needed(
    *,
    project_id: UUID,
    document_id: UUID,
    user_id: UUID,
    thread_id: UUID,
    file_name: str,
    key_datapoints: dict | None,
    status: str,
) -> bool:
    """One-shot completed datapoints run for completed docs missing key_datapoints.

    Used after restart or for docs ingested before the datapoints hooks existed.
    Shares the per-document job lease with ingest so list-polling cannot start a
    second concurrent LangGraph run. Returns True when a background job was started.
    """
    if status != DocumentStatus.COMPLETED.value or key_datapoints is not None:
        return False
    # Fast reject when ingest already holds the lease (avoids spawning no-op threads).
    with _datapoints_job_lock:
        if document_id in _datapoints_jobs_inflight:
            return False

    def _run() -> None:
        _trigger_completed_datapoints_pass(
            project_id=project_id,
            document_id=document_id,
            user_id=user_id,
            thread_id=thread_id,
            file_name=file_name,
            request_id="datapoints-backfill",
            mode="completed",
            wait_for_slot=False,
        )

    threading.Thread(
        target=_run,
        name=f"datapoints-backfill-{document_id}",
        daemon=True,
    ).start()
    return True


def _set_status(db, document: Document, status: DocumentStatus) -> None:
    document.status = status.value
    document.error_message = None
    db.commit()


def _describe_document(
    *,
    document_id: UUID,
    file_name: str,
    user_id: UUID,
    project_id: UUID,
    describer: DocumentDescriber,
    storage: LocalStorageService,
    chunks: list[TextChunk],
    request_id: str | None,
) -> None:
    """Fill company_name, report_year and summary. Failures are logged; ingest continues.

    Runs on its own thread and its own database session, overlapping the embedding batches.
    It only writes the description columns, never the status.
    """
    try:
        result = describer.describe(chunks, file_name=file_name, request_id=request_id)
    except DescriptionUnavailable as exc:
        structured_log(
            logger, "ingest.describe_skipped", request_id=request_id, document_id=document_id, reason=str(exc)
        )
        return
    except Exception as exc:
        structured_log(
            logger,
            "ingest.describe_failed",
            request_id=request_id,
            document_id=document_id,
            error=type(exc).__name__,
            detail=str(exc)[:300],
        )
        return

    with session_scope() as db:
        document = db.get(Document, document_id)
        if document is None:
            return
        _path, rel, size = storage.write_text_artifact(
            user_id=user_id,
            project_id=project_id,
            document_id=document_id,
            identifier=ArtifactIdentifier.SUMMARY,
            text=result.description,
            request_id=request_id,
        )
        _upsert_artifact(
            db,
            document_id=document_id,
            identifier=ArtifactIdentifier.SUMMARY.value,
            storage_path=rel,
            mime_type="text/plain",
            size_bytes=size,
        )
        document.company_name = result.company_name
        document.report_year = result.report_year
        document.summary = result.description
        db.commit()
    structured_log(
        logger,
        "ingest.described",
        request_id=request_id,
        document_id=document_id,
        company_name=result.company_name,
        report_year=result.report_year,
    )


def _pages_from_extraction(extracted: dict, text: str) -> list[PageText]:
    # Mistral separates running headers/footers from the body: the header becomes the section
    # hint and the footer (page number, report title) is dropped.
    pages = [
        PageText(
            page_number=page.get("page_number"),
            text=page.get("text") or "",
            header=page.get("header"),
        )
        for page in extracted.get("pages") or []
        if isinstance(page, dict)
    ]
    return pages or [PageText(page_number=None, text=text)]


def _upsert_artifact(
    db,
    *,
    document_id: UUID,
    identifier: str,
    storage_path: str,
    mime_type: str,
    size_bytes: int,
) -> None:
    existing = db.execute(
        select(Artifact).where(
            Artifact.document_id == document_id,
            Artifact.identifier == identifier,
        )
    ).scalar_one_or_none()
    if existing:
        existing.storage_path = storage_path
        existing.mime_type = mime_type
        existing.size_bytes = size_bytes
    else:
        db.add(
            Artifact(
                document_id=document_id,
                identifier=identifier,
                storage_path=storage_path,
                mime_type=mime_type,
                size_bytes=size_bytes,
            )
        )
    db.commit()
