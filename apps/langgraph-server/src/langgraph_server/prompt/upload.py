"""Upload local fallback prompts to Langfuse (new version, labels production/latest/dev)."""

from __future__ import annotations

import logging
from typing import Any

from langgraph_server.core.langfuse_client import initialize_langfuse
from langgraph_server.prompt.loader import FALLBACK_PROMPTS_DIR, PromptError
from langgraph_server.prompt.registry import PROMPT_REGISTRY

logger = logging.getLogger(__name__)


def _load_fallback_data(agent_name: str, node_name: str, prompt_name: str) -> dict[str, Any]:
    from importlib.util import module_from_spec, spec_from_file_location

    path = f"{agent_name}/{node_name}/{prompt_name}"
    file = FALLBACK_PROMPTS_DIR.joinpath(*path.split("/")).with_suffix(".py")
    if not file.is_file():
        raise PromptError(f"Fallback prompt not found: {file}")
    spec = spec_from_file_location(f"upload_prompt_{path.replace('/', '_')}", file)
    if spec is None or spec.loader is None:
        raise PromptError(f"Cannot load fallback prompt module {file}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    data = getattr(module, "PROMPT_DATA", None)
    if not isinstance(data, dict) or not isinstance(data.get("prompt"), list):
        raise PromptError(f"{file} must define PROMPT_DATA with a prompt list")
    return data


def upload_prompts() -> int:
    langfuse = initialize_langfuse()
    if langfuse is None:
        raise RuntimeError(
            "Langfuse is not configured. Set LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY, and LANGFUSE_HOST."
        )
    uploaded = 0
    for agent_name, node_name, prompt_name in PROMPT_REGISTRY:
        path = f"{agent_name}/{node_name}/{prompt_name}"
        data = _load_fallback_data(agent_name, node_name, prompt_name)
        messages = [
            {"role": str(item["role"]), "content": str(item["content"])}
            for item in data["prompt"]
            if isinstance(item, dict) and item.get("role") and item.get("content")
        ]
        langfuse.create_prompt(
            name=path,
            type="chat",
            prompt=messages,
            labels=["production", "latest", "dev"],
            config=data.get("config") or {},
        )
        logger.info("Uploaded Langfuse prompt %s", path)
        uploaded += 1
    langfuse.flush()
    return uploaded


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    count = upload_prompts()
    print(f"Uploaded {count} prompt(s) to Langfuse.")


if __name__ == "__main__":
    main()
