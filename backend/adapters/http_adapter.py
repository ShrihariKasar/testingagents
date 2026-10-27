"""HTTP-based Model Adapter supporting OpenAI-compatible and Gemini endpoints."""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional, Type, TypeVar
import httpx
from pydantic import BaseModel

from backend.adapters.base import BaseModelAdapter, ModelResponse, ModelUsage

T = TypeVar("T", bound=BaseModel)


class HTTPModelAdapter(BaseModelAdapter):
    """
    Vendor-agnostic adapter connecting to OpenAI-compatible endpoints
    (OpenAI, Gemini via v1beta/openai, DeepSeek, Ollama, vLLM).
    """

    def __init__(
        self,
        model_name: str = "gemini-2.5-flash",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        super().__init__(model_name)
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
        self.base_url = base_url or os.environ.get("LLM_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta/openai"

    def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.1,
        response_model: Optional[Type[T]] = None,
    ) -> ModelResponse:
        start = time.perf_counter()

        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
        }

        if response_model is not None:
            # Enforce JSON output mode
            payload["response_format"] = {"type": "json_object"}

        try:
            with httpx.Client(timeout=60.0) as client:
                res = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                res.raise_for_status()
                data = res.json()

            duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
            choice = data["choices"][0]["message"]["content"]
            usage_data = data.get("usage", {})
            prompt_tokens = usage_data.get("prompt_tokens", 0)
            comp_tokens = usage_data.get("completion_tokens", 0)

            parsed_json = None
            try:
                parsed_json = json.loads(choice)
            except Exception:
                pass

            return ModelResponse(
                content=choice,
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

        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
            # Return structured error model response
            return ModelResponse(
                content=f'{{"error": "{str(exc)}"}}',
                parsed_json={"error": str(exc)},
                model_name=self.model_name,
                usage=ModelUsage(),
                latency_ms=duration_ms,
            )
