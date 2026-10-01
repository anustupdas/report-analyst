from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from chat_api.config import Settings
from chat_api.prompt import PromptError, load_prompt
from chat_api.services.chunking import TextChunk
from chat_api.services.document_description import (
    DescriptionUnavailable,
    DocumentDescriber,
    DocumentDescription,
    build_context,
)


def _chunk(index: int, content: str, section: str | None = None, pages: tuple[int, int] = (1, 1)) -> TextChunk:
    return TextChunk(
        chunk_index=index,
        content=content,
        section=section,
        page_start=pages[0],
        page_end=pages[1],
        token_count=len(content.split()),
    )


class FakeResponses:
    def __init__(self, parsed: DocumentDescription | None) -> None:
        self.parsed = parsed
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_parsed=self.parsed,
            model=kwargs["model"],
            status="completed",
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        )


def _settings(**overrides: Any) -> Settings:
    return Settings(llm_summary_enabled=True, openai_api_key="sk-test", **overrides)


def test_fallback_prompt_has_model_config_and_compiles() -> None:
    prompt = load_prompt("ingest", "describe_document", "main")
    assert prompt.source == "fallback"
    assert prompt.model_config.model_provider == "openai"
    assert prompt.model_config.model
    assert prompt.variables == {"file_name", "context"}

    messages = prompt.compile(file_name="acme.pdf", context="ACME Annual Report 2025")
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "acme.pdf" in messages[1]["content"] and "{{" not in messages[1]["content"]
    with pytest.raises(PromptError):
        prompt.compile(file_name="acme.pdf")


def test_build_context_uses_first_chunks_with_page_and_section_labels() -> None:
    chunks = [_chunk(i, f"Body {i}.", section="Strategy" if i else None, pages=(i + 1, i + 2)) for i in range(12)]
    context = build_context(chunks, max_chunks=10, max_chars=10_000)
    assert context.startswith("[pp. 1-2]\nBody 0.")
    assert "[pp. 2-3 | Strategy]\nBody 1." in context
    assert "Body 9." in context and "Body 10." not in context
    assert len(build_context(chunks, max_chunks=10, max_chars=30)) <= 30 + len("\n\n---\n\n")


def test_describe_sends_prompt_model_and_structured_format() -> None:
    expected = DocumentDescription(company_name="ACME N.V.", report_year=2025, description="ACME's annual report.")
    fake = FakeResponses(expected)
    describer = DocumentDescriber(_settings(llm_description_context_chunks=2), responses=fake)

    result = describer.describe(
        [_chunk(0, "ACME Annual Report 2025"), _chunk(1, "Contents"), _chunk(2, "Later")], file_name="acme.pdf"
    )

    assert result == expected
    call = fake.calls[0]
    prompt = load_prompt("ingest", "describe_document", "main")
    assert call["model"] == prompt.model_config.model
    assert call["text_format"] is DocumentDescription
    assert call["max_output_tokens"] == prompt.model_config.max_output_tokens
    for key, value in prompt.model_config.model_args.items():
        assert call[key] == value
    user = call["input"][1]["content"]
    assert "ACME Annual Report 2025" in user and "Contents" in user and "Later" not in user


def test_describe_disabled_or_unparsed() -> None:
    with pytest.raises(DescriptionUnavailable):
        DocumentDescriber(Settings(llm_summary_enabled=False)).describe([_chunk(0, "x")], file_name="a.pdf")
    with pytest.raises(DescriptionUnavailable):
        DocumentDescriber(Settings(llm_summary_enabled=True, openai_api_key="")).describe(
            [_chunk(0, "x")], file_name="a.pdf"
        )
    with pytest.raises(RuntimeError, match="no parsable"):
        DocumentDescriber(_settings(), responses=FakeResponses(None)).describe([_chunk(0, "x")], file_name="a.pdf")


def test_report_year_is_validated() -> None:
    with pytest.raises(ValueError):
        DocumentDescription(company_name=None, report_year=25, description="x")
