"""Mock & Rule-based Model Adapter for deterministic, offline testing and benchmarking."""
from __future__ import annotations

import json
import time
from typing import Any, Optional, Type, TypeVar
from pydantic import BaseModel

from backend.adapters.base import BaseModelAdapter, ModelResponse, ModelUsage

T = TypeVar("T", bound=BaseModel)


class DeterministicMockAdapter(BaseModelAdapter):
    """
    Predictable model adapter used for reproducible unit testing, local benchmarking,
    and fallback when no API key is set.
    """

    def __init__(self, model_name: str = "mock-deterministic-v1"):
        super().__init__(model_name)
        self.preset_responses: list[str] = []
        self.mock_patch: Optional[str] = None

    def set_next_response(self, text: str):
        self.preset_responses.append(text)

    def set_mock_patch(self, patch: Any):
        """Sets mock response formatted as a unified diff patch block for repair agent."""
        diff_text = getattr(patch, "unified_diff", str(patch))
        analysis = getattr(patch, "root_cause_analysis", "Targeted bug repair.")
        self.mock_patch = f"```diff\n{diff_text}\n```\n{analysis}"

    def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.1,
        response_model: Optional[Type[T]] = None,
    ) -> ModelResponse:
        start = time.perf_counter()

        if "unified diff patch" in prompt.lower() and self.mock_patch:
            content = self.mock_patch
        elif self.preset_responses:
            content = self.preset_responses.pop(0)
        else:
            # Default fallback mock response
            content = '{"status": "ok", "reasoning": "Mock adapter response", "code": ""}'

        duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
        prompt_tokens = len(prompt.split())
        comp_tokens = len(content.split())

        parsed_json = None
        try:
            parsed_json = json.loads(content)
        except Exception:
            pass

        return ModelResponse(
            content=content,
            parsed_json=parsed_json,
            model_name=self.model_name,
            usage=ModelUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=comp_tokens,
                total_tokens=prompt_tokens + comp_tokens,
                estimated_cost_usd=round((prompt_tokens * 0.0000005) + (comp_tokens * 0.0000015), 6),
            ),
            latency_ms=duration_ms,
        )
