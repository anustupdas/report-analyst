"""Prompts managed in Langfuse with a local fallback in fallback_prompts/."""

PROMPT_REGISTRY: list[tuple[str, str, str]] = [
    ("ingest", "describe_document", "main"),
]
