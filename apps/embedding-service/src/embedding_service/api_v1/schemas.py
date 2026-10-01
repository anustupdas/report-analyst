from typing import List, Literal

from pydantic import BaseModel, Field, field_validator

from embedding_service.constants import DEFAULT_MODEL_NAME


class EmbeddingItem(BaseModel):
    text: str
    embedding: List[float]


class EmbeddingMeta(BaseModel):
    model_name: str
    model_version: int
    hf_model_id: str
    dimension: int
    input_type: str


class EmbeddingsResponse(BaseModel):
    embeddings: List[EmbeddingItem]
    meta: EmbeddingMeta


class TextEmbRequest(BaseModel):
    text: str = Field(..., description="Input text to embed.", examples=["Net profit for 2024"])
    model_name: str = Field(default=DEFAULT_MODEL_NAME, description="Configured model name (not a Hub path).")
    input_type: Literal["passage", "query"] = Field(
        default="passage",
        description="passage for document chunks, query for search questions (applies the model's configured prefix, if any).",
    )

    @field_validator("text")
    @classmethod
    def check_text(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("text cannot be empty")
        return value.strip()


class BatchTextEmbRequest(BaseModel):
    texts: List[str] = Field(..., description="List of non-empty strings to embed.")
    model_name: str = Field(default=DEFAULT_MODEL_NAME)
    input_type: Literal["passage", "query"] = Field(default="passage")

    @field_validator("texts")
    @classmethod
    def check_texts(cls, value: List[str]) -> List[str]:
        if not isinstance(value, list) or len(value) == 0:
            raise ValueError("texts must be a non-empty list")
        if not any(item and str(item).strip() for item in value):
            raise ValueError("All text entries are empty")
        return value


class OpenAIBatchEmbRequest(BaseModel):
    texts: List[str] = Field(..., description="List of non-empty strings to embed with OpenAI.")
    model_name: str = Field(
        default="text-embedding-3-small",
        description="OpenAI embedding model id (e.g. text-embedding-3-small).",
    )

    @field_validator("texts")
    @classmethod
    def check_texts(cls, value: List[str]) -> List[str]:
        if not isinstance(value, list) or len(value) == 0:
            raise ValueError("texts must be a non-empty list")
        if not any(item and str(item).strip() for item in value):
            raise ValueError("All text entries are empty")
        return value
