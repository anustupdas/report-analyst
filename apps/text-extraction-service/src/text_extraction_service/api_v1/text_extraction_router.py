import logging
import threading
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, Request, status
from pydantic import BaseModel, Field, field_validator

from text_extraction_service.service import ExtractedFile, TextExtractionService
from text_extraction_service.utils import initialize_configuration

text_extraction_service_router = APIRouter(tags=["text"])
logger = logging.getLogger(__name__)

__all__ = ("text_extraction_service_router",)

_service_instance: TextExtractionService | None = None
_service_lock = threading.Lock()


def get_service() -> TextExtractionService:
    global _service_instance
    if _service_instance is None:
        with _service_lock:
            if _service_instance is None:
                config = initialize_configuration()
                _service_instance = TextExtractionService(config=config)
    return _service_instance


class TextRequest(BaseModel):
    file_ref: str = Field(
        ...,
        description=("Local filename under data.location when USE_S3=false, " "or S3 object key when USE_S3=true."),
    )
    use_ocr: bool = Field(
        False,
        description=(
            "If true (and USE_MISTRAL=true), process the whole file with Mistral OCR. "
            "Images always use Mistral when enabled."
        ),
    )

    @field_validator("file_ref")
    @classmethod
    def check_file_ref(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("file_ref cannot be empty")
        return value.strip()


class ExtractTextResponse(BaseModel):
    data: ExtractedFile


@text_extraction_service_router.post(
    "/extract/file",
    status_code=status.HTTP_200_OK,
    response_model=ExtractTextResponse,
    responses={
        200: {"description": "Success - Text extracted from file."},
        404: {"description": "File not found"},
        415: {"description": "Unsupported file format"},
        422: {"description": "Extraction failed or Mistral disabled for this request"},
        429: {"description": "OCR rate limit exceeded"},
        500: {"description": "Unexpected server error"},
        502: {"description": "OCR provider error (Mistral failure)"},
    },
)
async def extract_text(
    request: Request,
    body: TextRequest = Body(...),
    service: TextExtractionService = Depends(get_service),
    application: Optional[str] = Header(
        None,
        description="Optional caller label for logging (e.g. local-dev)",
    ),
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID"),
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
):
    """
    Extract text from a local file or S3 object.

    - Documents → PyMuPDF for the whole file (default)
    - Images → Mistral OCR when enabled
    - `use_ocr=true` → whole file via Mistral (requires USE_MISTRAL=true)
    """
    logger.info(
        "Received request for %s application=%s file_ref=%s use_ocr=%s request_id=%s session_id=%s",
        request.url.path,
        application,
        body.file_ref,
        body.use_ocr,
        x_request_id,
        x_session_id,
    )
    result = service.extract_text_from_file(body.file_ref, use_ocr=body.use_ocr)
    return ExtractTextResponse(data=result)
