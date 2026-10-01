"""Prompts managed in Langfuse with a local fallback in fallback_prompts/."""

PROMPT_REGISTRY: list[tuple[str, str, str]] = [
    ("supervisor_agent", "supervisor", "main"),
    ("supervisor_agent", "supervisor", "detail-extractor"),
    ("supervisor_agent", "extractor", "main"),
]
