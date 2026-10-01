"""Describe a report from its first chunks: company name, report year and a short description."""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol, Sequence

from pydantic import BaseModel, Field

from chat_api.config import Settings
from chat_api.langfuse_client import langfuse_generation_scope
from chat_api.prompt import ChatPrompt, PromptError, load_prompt
from chat_api.services.chunking import TextChunk
from chat_api.tracing import structured_log

logger = logging.getLogger(__name__)

PROMPT_PATH = ("ingest", "describe_document", "main")
# Rough chars-per-token for English report text; only used to stay inside max_input_tokens.
_CHARS_PER_TOKEN = 4


class DocumentDescription(BaseModel):
    """Structured output of the describe_document prompt."""

    company_name: str | None = Field(
        description="Reporting company as named in the document, with legal suffix if shown; null if unknown."
    )
    report_year: int | None = Field(
        description="Financial/reporting year the document covers (not the publication year); null if unknown.",
        ge=1900,
        le=2100,
    )
    description: str = Field(description="3-5 sentence neutral description of what the document is and covers.")


class DescriptionUnavailable(RuntimeError):
    """The describer is disabled or not configured; ingest continues without a description."""


class ResponsesClient(Protocol):
    def parse(self, **kwargs: Any) -> Any:
        pass


def build_context(chunks: Sequence[TextChunk], *, max_chunks: int, max_chars: int) -> str:
    parts: list[str] = []
    used = 0
    for chunk in chunks[:max_chunks]:
        pages = ""
        if chunk.page_start is not None:
            pages = (
                f"p. {chunk.page_start}"
                if chunk.page_start == chunk.page_end
                else (f"pp. {chunk.page_start}-{chunk.page_end}")
            )
        label = " | ".join(part for part in (pages, chunk.section or "") if part)
        part = f"[{label}]\n{chunk.content}" if label else chunk.content
        if used + len(part) > max_chars:
            part = part[: max(0, max_chars - used)]
        if not part:
            break
        parts.append(part)
        used += len(part)
    return "\n\n---\n\n".join(parts)


class DocumentDescriber:
    def __init__(self, settings: Settings, *, responses: ResponsesClient | None = None) -> None:
        self.enabled = settings.llm_summary_enabled
        self.context_chunks = settings.llm_description_context_chunks
        self._api_key = settings.openai_api_key
        self._timeout = settings.llm_timeout_seconds
        self._responses = responses

    def _client(self) -> ResponsesClient:
        if self._responses is None:
            from openai import OpenAI

            self._responses = OpenAI(api_key=self._api_key, timeout=self._timeout, max_retries=2).responses
        return self._responses

    def describe(
        self,
        chunks: Sequence[TextChunk],
        *,
        file_name: str,
        request_id: str | None = None,
    ) -> DocumentDescription:
        if not self.enabled:
            raise DescriptionUnavailable("llm.summaryEnabled is false")
        prompt = load_prompt(*PROMPT_PATH)
        config = prompt.model_config
        if config.model_provider != "openai":
            raise PromptError(f"Unsupported model_provider {config.model_provider!r} for {prompt.path}")
        if not self._api_key and self._responses is None:
            raise DescriptionUnavailable("OPENAI_API_KEY is not set")

        max_chars = (config.max_input_tokens or 100_000) * _CHARS_PER_TOKEN // 2
        context = build_context(chunks, max_chunks=self.context_chunks, max_chars=max_chars)
        if not context.strip():
            raise DescriptionUnavailable("No text to describe")

        started = time.monotonic()
        messages = prompt.compile(file_name=file_name, context=context)
        parse_kwargs = {
            "model": config.model,
            "input": messages,
            "text_format": DocumentDescription,
            "max_output_tokens": config.max_output_tokens,
            "metadata": _metadata(prompt),
            **config.model_args,
        }
        with langfuse_generation_scope(
            name="ingest/describe_document",
            model=config.model,
            input=messages,
            metadata=_metadata(prompt) | {"file_name": file_name},
            prompt=prompt.langfuse_prompt,
        ) as observation:
            response = self._client().parse(**parse_kwargs)
            result = getattr(response, "output_parsed", None)
            if observation is not None and isinstance(result, DocumentDescription):
                try:
                    observation.update(output=result.model_dump())
                except Exception:
                    logger.debug("Langfuse describe observation update failed", exc_info=True)
        usage = getattr(response, "usage", None)
        structured_log(
            logger,
            "describe.done",
            request_id=request_id,
            prompt=prompt.path,
            prompt_source=prompt.source,
            model=getattr(response, "model", config.model),
            chunks=min(len(chunks), self.context_chunks),
            context_chars=len(context),
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            duration_ms=int((time.monotonic() - started) * 1000),
            parsed=result is not None,
        )
        if not isinstance(result, DocumentDescription):
            raise RuntimeError(f"Model returned no parsable description (status={getattr(response, 'status', None)})")
        return result


def _metadata(prompt: ChatPrompt) -> dict[str, str]:
    metadata = {"prompt": prompt.path, "prompt_source": prompt.source}
    if prompt.version is not None:
        metadata["prompt_version"] = str(prompt.version)
    return metadata
