"""Load a chat prompt and its model config.

Resolution order: Langfuse (label `dev` then `production` in development) → local
fallback in fallback_prompts/{agent}/{node}/{name}.py. Same Langfuse chat prompt
shape (`{{variable}}` + `config.model_config`).
"""

from __future__ import annotations

import importlib.util
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from langgraph_server.prompt.registry import PROMPT_REGISTRY

logger = logging.getLogger(__name__)

FALLBACK_PROMPTS_DIR = Path(__file__).parent / "fallback_prompts"
_VARIABLE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class PromptError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelConfig:
    model_provider: str
    model: str
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    model_args: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: dict[str, Any], prompt_path: str) -> ModelConfig:
        raw = config.get("model_config") if isinstance(config, dict) else None
        if not isinstance(raw, dict) or not raw.get("model_provider") or not raw.get("model"):
            raise PromptError(f"Prompt {prompt_path} has no model_config with model_provider and model")
        return cls(
            model_provider=str(raw["model_provider"]),
            model=str(raw["model"]),
            max_input_tokens=raw.get("max_input_tokens"),
            max_output_tokens=raw.get("max_output_tokens"),
            model_args=dict(raw.get("model_args") or {}),
        )


@dataclass(frozen=True)
class ChatPrompt:
    path: str
    messages: list[dict[str, str]]
    model_config: ModelConfig
    source: Literal["langfuse", "fallback"]
    version: int | None = None
    langfuse_prompt: Any = field(default=None, compare=False, repr=False)

    @property
    def variables(self) -> set[str]:
        return {name for message in self.messages for name in _VARIABLE.findall(message["content"])}

    def compile(self, **variables: Any) -> list[dict[str, str]]:
        missing = self.variables - variables.keys()
        if missing:
            raise PromptError(f"Prompt {self.path} is missing variables: {sorted(missing)}")
        return [
            {
                "role": message["role"],
                "content": _VARIABLE.sub(lambda match: str(variables[match.group(1)]), message["content"]),
            }
            for message in self.messages
        ]


def load_prompt(agent_name: str, node_name: str, prompt_name: str = "main") -> ChatPrompt:
    path = f"{agent_name}/{node_name}/{prompt_name}"
    if (agent_name, node_name, prompt_name) not in PROMPT_REGISTRY:
        logger.warning("Prompt %s is not in PROMPT_REGISTRY; it will not be synced from the prompt store", path)
    prompt = _load_remote(path) or _load_fallback(path)
    logger.debug("prompt.loaded path=%s source=%s model=%s", path, prompt.source, prompt.model_config.model)
    return prompt


def _load_remote(path: str) -> ChatPrompt | None:
    try:
        from langgraph_server.config import get_settings
        from langgraph_server.core.langfuse_client import get_langfuse

        settings = get_settings()
        if not settings.langfuse_tracing:
            return None
        langfuse = get_langfuse()
        if langfuse is None:
            return None
        prompt = None
        for label in settings.langfuse_prompt_labels:
            try:
                prompt = langfuse.get_prompt(
                    name=path,
                    label=label,
                    type="chat",
                    cache_ttl_seconds=settings.langfuse_prompt_cache_ttl_seconds,
                    max_retries=0,
                )
            except Exception:
                continue
            if prompt is None or getattr(prompt, "is_fallback", False):
                continue
            break
        else:
            return None
        if prompt is None or getattr(prompt, "is_fallback", False):
            return None
        messages = _messages_from_remote(prompt)
        if not messages:
            return None
        config = prompt.config if isinstance(getattr(prompt, "config", None), dict) else {}
        return ChatPrompt(
            path=path,
            messages=messages,
            model_config=ModelConfig.from_config(config, path),
            source="langfuse",
            version=getattr(prompt, "version", None),
            langfuse_prompt=prompt,
        )
    except Exception:
        logger.warning("Langfuse prompt fetch failed for %s; using local fallback", path, exc_info=True)
        return None


def _messages_from_remote(prompt: Any) -> list[dict[str, str]]:
    raw = getattr(prompt, "prompt", None)
    if not isinstance(raw, list):
        return []
    messages: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        content = str(item.get("content") or "")
        if role and content:
            messages.append({"role": role, "content": content})
    return messages


def _load_fallback(path: str) -> ChatPrompt:
    file = FALLBACK_PROMPTS_DIR.joinpath(*path.split("/")).with_suffix(".py")
    if not file.is_file():
        raise PromptError(f"Fallback prompt not found: {file}")
    spec = importlib.util.spec_from_file_location(f"fallback_prompt_{path.replace('/', '_')}", file)
    if spec is None or spec.loader is None:
        raise PromptError(f"Cannot load fallback prompt module {file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    data = getattr(module, "PROMPT_DATA", None)
    if not isinstance(data, dict) or not isinstance(data.get("prompt"), list):
        raise PromptError(f"{file} must define PROMPT_DATA with a 'prompt' message list")
    messages = [
        {"role": str(message["role"]), "content": str(message["content"])}
        for message in data["prompt"]
        if isinstance(message, dict) and message.get("role") and message.get("content")
    ]
    if not messages:
        raise PromptError(f"{file} has no chat messages")
    return ChatPrompt(
        path=path,
        messages=messages,
        model_config=ModelConfig.from_config(data.get("config") or {}, path),
        source="fallback",
    )
