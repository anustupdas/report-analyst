import pytest

from chat_api.config import Settings
from chat_api.modules.embeddings import (
    normalize_provider,
    provider_default_model,
    provider_dimension,
    provider_table_name,
)
from chat_api.services.vectors import VectorStore


def test_normalize_provider_aliases() -> None:
    assert normalize_provider("OpenAI") == "openai"
    assert normalize_provider("oai") == "openai"
    assert normalize_provider("hf") == "huggingface"
    assert normalize_provider("local") == "huggingface"
    assert normalize_provider(None) == "openai"
    assert normalize_provider("") == "openai"


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown embedding.provider"):
        normalize_provider("cohere")


def test_provider_tables_match_their_widths() -> None:
    assert provider_dimension("openai") == 1536
    assert provider_default_model("openai") == "text-embedding-3-small"
    assert provider_table_name("openai") == "document_chunk_embeddings_openai"
    assert provider_dimension("huggingface") == 768
    assert provider_table_name("huggingface") == "document_chunk_embeddings_hf"


def test_omitted_settings_fall_back_to_pymupdf_and_openai() -> None:
    assert Settings.model_fields["text_extraction_use_ocr"].default is False
    assert Settings.model_fields["embedding_provider"].default == "openai"
    assert Settings.model_fields["embedding_model_name"].default == "text-embedding-3-small"
    assert Settings.model_fields["embedding_dimension"].default == 1536
    store = VectorStore()
    assert store.provider == "openai"
    assert store._Embedding.__tablename__ == "document_chunk_embeddings_openai"
