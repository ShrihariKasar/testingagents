"""Specialized Integration Testing Agent.
Generates stateful API and service contract tests, manages ephemeral database
or service fixtures, and verifies inter-component contracts.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from backend.adapters.base import BaseModelAdapter, sanitize_untrusted_input
from backend.models.contracts import AgentType, ExecutionEvidence, TestSuiteReport
from backend.runners.sandbox import SandboxExecutionEngine
from backend.learning.agent_interface import BaseContinuousLearningAgent


class APIEndpointModel(BaseModel):
    path: str
    method: str
    summary: Optional[str] = None
    parameters: List[str] = Field(default_factory=list)
    request_body_schema: Optional[str] = None
    response_status_codes: List[int] = Field(default_factory=lambda: [200])


class IntegrationTestSuite(BaseModel):
    test_code: str
    endpoints_tested: List[str] = Field(default_factory=list)
    fixture_definitions: List[str] = Field(default_factory=list)


class IntegrationTestingAgent(BaseContinuousLearningAgent):
    """Coordinates integration and API contract test generation and execution."""

    def __init__(self, model_adapter: BaseModelAdapter, sandbox_engine: Optional[SandboxExecutionEngine] = None):
        super().__init__(agent_type=AgentType.INTEGRATION)
        self.adapter = model_adapter
        self.sandbox = sandbox_engine or SandboxExecutionEngine()

    def discover_endpoints_from_fastapi_app(self, route_file: Path) -> List[APIEndpointModel]:
        """Statically inspects route declarations in FastAPI / Flask / Starlette files."""
        endpoints = []
        text = route_file.read_text(encoding="utf-8")
        # Match router.(get|post|put|delete|patch)
        pattern = re.compile(r'@(?:app|router)\.(get|post|put|delete|patch)\(\s*["\']([^"\']+)["\']', re.IGNORECASE)
        for match in pattern.finditer(text):
            method = match.group(1).upper()
            route_path = match.group(2)
            endpoints.append(APIEndpointModel(path=route_path, method=method))
        return endpoints

    def synthesize_integration_tests(
        self,
        route_file: Path,
        endpoints: List[APIEndpointModel],
        db_model_file: Optional[Path] = None,
    ) -> IntegrationTestSuite:
        """Synthesizes integration test suite using FastAPI TestClient / httpx."""
        endpoints_desc = "\n".join(f"- {ep.method} {ep.path}" for ep in endpoints)
        untrusted_routes = sanitize_untrusted_input(route_file.read_text(encoding="utf-8"), "route_definitions")

        system_instruction = (
            "You are a Senior Principal Integration QA Engineer.\n"
            "Write comprehensive API integration tests using `fastapi.testclient.TestClient` or `httpx`.\n"
            "Include realistic lifecycle flows (e.g. Create -> Retrieve -> Update -> Verify 404 on invalid ID).\n"
            "Output valid Python test code in ```python ``` blocks."
        )

        prompt = (
            f"Discovered Endpoints:\n{endpoints_desc}\n\n"
            f"Route Source Code:\n{untrusted_routes}\n\n"
            "Generate a stateful integration test suite with proper status assertions and rollback fixtures."
        )

        res = self.adapter.generate(prompt, system_instruction=system_instruction)

        diff_match = re.search(r"```python\s*(.*?)\s*```", res.content, re.DOTALL)
        code = diff_match.group(1).strip() if diff_match else res.content.strip()

        return IntegrationTestSuite(
            test_code=code,
            endpoints_tested=[f"{ep.method} {ep.path}" for ep in endpoints],
        )

    def execute_integration_suite(
        self,
        project_dir: Path,
        test_file_name: str,
        suite: IntegrationTestSuite,
    ) -> Tuple[ExecutionEvidence, Optional[TestSuiteReport]]:
        """Deploys and executes integration suite inside the sandbox."""
        test_path = project_dir / test_file_name
        test_path.write_text(suite.test_code, encoding="utf-8")

        return self.sandbox.run_pytest(
            target_dir=project_dir,
            test_path=test_file_name,
            ephemeral_clone=False,
        )
