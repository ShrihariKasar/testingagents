"""Model Adapter Layer.
Provides a vendor-agnostic interface for LLM completions, prompt sanitization,
token accounting, and structured schema outputs.
"""
from __future__ import annotations

import abc
import datetime
import html
from typing import Any, Dict, Optional, Type, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


class ModelUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


class ModelResponse(BaseModel):
    content: str
    parsed_json: Optional[Dict[str, Any]] = None
    model_name: str
    usage: ModelUsage = Field(default_factory=ModelUsage)
    latency_ms: float = 0.0
    timestamp: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())


def sanitize_untrusted_input(untrusted_content: str, tag: str = "untrusted_content") -> str:
    """
    Wraps untrusted repository content, git logs, or test output in safe XML tags,
    escaping closing tags to prevent prompt injection boundary escapes.
    """
    # Prevent injection of closing tag
    safe_content = untrusted_content.replace(f"</{tag}>", f"&lt;/{tag}&gt;")
    return f"<{tag}>\n{safe_content}\n</{tag}>"


class BaseModelAdapter(abc.ABC):
    """Abstract base class for LLM providers (Gemini, OpenAI, Anthropic, local vLLM)."""

    def __init__(self, model_name: str):
        self.model_name = model_name

    @abc.abstractmethod
    def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.1,
        response_model: Optional[Type[T]] = None,
    ) -> ModelResponse:
        """Generates a completion from the underlying model."""
        pass
