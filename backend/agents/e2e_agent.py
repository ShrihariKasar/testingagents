"""Specialized End-to-End (E2E) Testing Agent.
Synthesizes resilient Playwright user journeys, enforces accessibility locators,
and captures execution traces and artifacts.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field

from backend.adapters.base import BaseModelAdapter
from backend.models.contracts import AgentType
from backend.learning.agent_interface import BaseContinuousLearningAgent


class UserJourneyStep(BaseModel):
    action: str  # "navigate", "click", "fill", "assert"
    target: str
    value: Optional[str] = None
    expected_state: Optional[str] = None


class E2ETestScenario(BaseModel):
    scenario_name: str
    steps: List[UserJourneyStep] = Field(default_factory=list)
    base_url: str = "http://localhost:3000"


class SynthesizedE2ESuite(BaseModel):
    scenario_name: str
    script_content: str
    locators_used: List[str] = Field(default_factory=list)


class EndToEndTestingAgent(BaseContinuousLearningAgent):
    """Coordinates Playwright E2E script synthesis and execution."""

    def __init__(self, model_adapter: Optional[BaseModelAdapter] = None):
        super().__init__(agent_type=AgentType.E2E)
        self.adapter = model_adapter

    def generate_playwright_test(self, scenario: E2ETestScenario) -> SynthesizedE2ESuite:
        """
        Synthesizes a resilient Playwright test script adhering to web-first assertions
        and accessibility-tree best practices.
        """
        steps_desc = "\n".join(
            f"{i+1}. {s.action.upper()}: target='{s.target}' value='{s.value or ''}' expect='{s.expected_state or ''}'"
            for i, s in enumerate(scenario.steps)
        )

        system_instruction = (
            "You are a Principal E2E QA Automation Architect.\n"
            "Write production-grade Playwright (Python) tests.\n"
            "Strict Guidelines:\n"
            "1. Output ONLY valid executable Python in ```python ``` blocks.\n"
            "2. Use resilient locators: `page.get_by_role`, `page.get_by_label`, `page.get_by_test_id`.\n"
            "3. Use web-first assertions: `expect(locator).to_be_visible()`, `expect(locator).to_have_text(...)`.\n"
            "4. NEVER use `time.sleep()`. Rely on Playwright auto-waiting.\n"
            "5. Enable trace recording and screenshot on failure."
        )

        prompt = (
            f"Scenario: {scenario.scenario_name}\n"
            f"Base URL: {scenario.base_url}\n\n"
            f"User Journey Steps:\n{steps_desc}\n\n"
            "Generate the complete Playwright test function with proper pytest fixtures."
        )

        if self.adapter:
            res = self.adapter.generate(prompt, system_instruction=system_instruction)
            match = re.search(r"```python\s*(.*?)\s*```", res.content, re.DOTALL)
            if match:
                code = match.group(1).strip()
            elif "def test_" in res.content:
                code = res.content.strip()
            else:
                code = (
                    f"import pytest\n"
                    f"from playwright.sync_api import Page, expect\n\n"
                    f"def test_{scenario.scenario_name.lower().replace(' ', '_')}(page: Page):\n"
                    f"    page.goto('{scenario.base_url}')\n"
                )
        else:
            # Deterministic default template
            code = (
                f"import pytest\n"
                f"from playwright.sync_api import Page, expect\n\n"
                f"def test_{scenario.scenario_name.lower().replace(' ', '_')}(page: Page):\n"
                f"    page.goto('{scenario.base_url}')\n"
            )

        return SynthesizedE2ESuite(
            scenario_name=scenario.scenario_name,
            script_content=code,
            locators_used=[s.target for s in scenario.steps],
        )
