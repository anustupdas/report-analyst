import logging
import threading
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, Request, status

from embedding_service.api_v1.schemas import (
    BatchTextEmbRequest,
    EmbeddingsResponse,
    OpenAIBatchEmbRequest,
    TextEmbRequest,
)
from embedding_service.metrics import track_embedding
from embedding_service.openai_embed import embed_texts_openai
from embedding_service.pool import DedicatedEncodePool
from embedding_service.service import EmbeddingService
from embedding_service.utils import initialize_configuration

embedding_service_router = APIRouter(tags=["embeddings"])
logger = logging.getLogger(__name__)

__all__ = ("embedding_service_router", "get_service", "get_pool", "reset_runtime")

_service_instance: EmbeddingService | None = None
_pool_instance: DedicatedEncodePool | None = None
_runtime_lock = threading.Lock()


def reset_runtime() -> None:
    """Test helper: drop singletons so the next request rebuilds them."""
    global _service_instance, _pool_instance
    with _runtime_lock:
        if _pool_instance is not None:
            _pool_instance.stop()
        _pool_instance = None
        _service_instance = None


def get_service() -> EmbeddingService:
    """Legacy single-service accessor (info endpoint / tests). Prefer get_pool."""
    global _service_instance
    if _service_instance is None:
        with _runtime_lock:
            if _service_instance is None:
                _service_instance = EmbeddingService(config=initialize_configuration())
    return _service_instance


def get_pool() -> DedicatedEncodePool:
    """Shared query and passage encode lanes for this process."""
    global _pool_instance
    if _pool_instance is None:
        with _runtime_lock:
            if _pool_instance is None:
                config = initialize_configuration()
                # Two EmbeddingService instances → two in-memory model copies once used,
                # so query and passage can encode truly in parallel.
                pool = DedicatedEncodePool(config=config)
                pool.start()
                _pool_instance = pool
    return _pool_instance


@embedding_service_router.post(
    "/embed_text",
    status_code=status.HTTP_200_OK,
    response_model=EmbeddingsResponse,
    responses={
        200: {"description": "Success - embedding returned"},
        400: {"description": "Bad Request - input text missing or invalid"},
        404: {"description": "Model is not configured"},
        422: {"description": "Embedding computation failed"},
        503: {"description": "Model failed to load"},
    },
)
async def embed_text(
    request: Request,
    body: TextEmbRequest = Body(...),
    pool: DedicatedEncodePool = Depends(get_pool),
    application: Optional[str] = Header(None, description="Optional caller label"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID"),
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
) -> EmbeddingsResponse:
    logger.info(
        "embed_text path=%s application=%s model=%s input_type=%s request_id=%s session_id=%s",
        request.url.path,
        application,
        body.model_name,
        body.input_type,
        x_request_id,
        x_session_id,
    )
    with track_embedding(
        provider="huggingface",
        model=body.model_name,
        input_type=body.input_type,
        texts=1,
    ):
        items, meta = await pool.embed_texts([body.text], model_name=body.model_name, input_type=body.input_type)
    return EmbeddingsResponse(embeddings=items, meta=meta)


@embedding_service_router.post(
    "/embed_texts",
    status_code=status.HTTP_200_OK,
    response_model=EmbeddingsResponse,
    responses={
        200: {"description": "Success - embeddings returned"},
        400: {"description": "Bad Request - input list missing or invalid"},
        404: {"description": "Model is not configured"},
        422: {"description": "Embedding computation failed"},
        503: {"description": "Model failed to load"},
    },
)
async def embed_texts(
    request: Request,
    body: BatchTextEmbRequest = Body(...),
    pool: DedicatedEncodePool = Depends(get_pool),
    application: Optional[str] = Header(None, description="Optional caller label"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID"),
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
) -> EmbeddingsResponse:
    logger.info(
        "embed_texts path=%s application=%s model=%s input_type=%s n=%s request_id=%s session_id=%s",
        request.url.path,
        application,
        body.model_name,
        body.input_type,
        len(body.texts),
        x_request_id,
        x_session_id,
    )
    with track_embedding(
        provider="huggingface",
        model=body.model_name,
        input_type=body.input_type,
        texts=len(body.texts),
    ):
        items, meta = await pool.embed_texts(body.texts, model_name=body.model_name, input_type=body.input_type)
    return EmbeddingsResponse(embeddings=items, meta=meta)


@embedding_service_router.post(
    "/embeddings/openai",
    status_code=status.HTTP_200_OK,
    response_model=EmbeddingsResponse,
    responses={
        200: {"description": "Success - OpenAI embeddings returned"},
        400: {"description": "Bad Request - input list missing or invalid"},
        502: {"description": "OpenAI embedding request failed"},
        503: {"description": "OPENAI_API_KEY not configured"},
    },
)
async def embed_texts_openai_route(
    request: Request,
    body: OpenAIBatchEmbRequest = Body(...),
    application: Optional[str] = Header(None, description="Optional caller label"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID"),
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
) -> EmbeddingsResponse:
    """Embed texts with OpenAI (e.g. text-embedding-3-small). Does not use the local HF lanes."""
    logger.info(
        "embeddings/openai path=%s application=%s model=%s n=%s request_id=%s session_id=%s",
        request.url.path,
        application,
        body.model_name,
        len(body.texts),
        x_request_id,
        x_session_id,
    )
    with track_embedding(provider="openai", model=body.model_name, input_type="na", texts=len(body.texts)):
        items, meta = embed_texts_openai(body.texts, model_name=body.model_name)
    return EmbeddingsResponse(embeddings=items, meta=meta)
