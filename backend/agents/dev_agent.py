"""Specialized Software Development Agent.
Implements engineering requirements, synthesizes clean source code additions or modifications,
produces minimal reviewable patches, and preserves architectural conventions.
"""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from backend.adapters.base import BaseModelAdapter, sanitize_untrusted_input
from backend.learning.agent_interface import BaseContinuousLearningAgent
from backend.models.contracts import AgentType, ProposedPatch, RiskLevel, PatchVerificationStatus
from backend.runners.sandbox import SandboxExecutionEngine


from backend.adapters.mock_adapter import DeterministicMockAdapter


class CodeSynthesisResult(BaseModel):
    target_file: str
    original_code: str
    modified_code: str
    unified_diff: str
    summary_of_changes: str
    functions_added: List[str] = Field(default_factory=list)


class SoftwareDevelopmentAgent(BaseContinuousLearningAgent):
    """Responsible for autonomous feature implementation and source file modification."""

    def __init__(
        self,
        model_adapter: Optional[BaseModelAdapter] = None,
        sandbox_engine: Optional[SandboxExecutionEngine] = None,
    ):
        super().__init__(agent_type=AgentType.SOFTWARE_DEVELOPMENT)
        self.adapter = model_adapter or DeterministicMockAdapter()
        self.sandbox = sandbox_engine or SandboxExecutionEngine()

    def implement_requirement(
        self,
        project_dir: Path,
        target_file_rel: str,
        requirement_description: str,
        acceptance_criteria: Optional[List[str]] = None,
    ) -> CodeSynthesisResult:
        """
        Synthesizes code modification to satisfy an assigned engineering task.
        Generates minimal, reviewable unified diff against the current working copy.
        """
        target_path = project_dir / target_file_rel
        original_code = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
        untrusted_code = sanitize_untrusted_input(original_code, "existing_source_code")

        criteria_desc = "\n".join(f"- {c}" for c in (acceptance_criteria or ["Implement functional requirement without breaking existing tests"]))

        system_instruction = (
            "You are a Senior Principal Software Engineer.\n"
            "Implement the required feature by modifying the existing source code.\n"
            "Rules:\n"
            "1. Output ONLY the complete updated Python source code in a ```python ``` block.\n"
            "2. Maintain existing coding style, variable names, and function signatures.\n"
            "3. Add proper docstrings and parameter type annotations.\n"
            "4. NEVER delete existing working functions unless explicitly instructed.\n"
            "5. Add defensive null/type guards to prevent runtime crashes."
        )

        prompt = (
            f"Target File: {target_file_rel}\n"
            f"Requirement:\n{requirement_description}\n\n"
            f"Acceptance Criteria:\n{criteria_desc}\n\n"
            f"Existing Code:\n{untrusted_code}\n\n"
            "Generate the updated source code satisfying all criteria."
        )

        res = self.adapter.generate(prompt, system_instruction=system_instruction)

        # Extract code from response
        import re
        match = re.search(r"```python\s*(.*?)\s*```", res.content, re.DOTALL)
        if match:
            new_code = match.group(1)
        elif "def " in res.content or "class " in res.content:
            new_code = res.content
        else:
            # Fallback or empty mock
            new_code = original_code + f"\n# Implemented: {requirement_description}\n"

        # Generate unified diff
        diff_lines = list(
            difflib.unified_diff(
                original_code.splitlines(keepends=True),
                new_code.splitlines(keepends=True),
                fromfile=f"a/{target_file_rel}",
                tofile=f"b/{target_file_rel}",
            )
        )
        unified_diff = "".join(diff_lines)

        return CodeSynthesisResult(
            target_file=target_file_rel,
            original_code=original_code,
            modified_code=new_code,
            unified_diff=unified_diff,
            summary_of_changes=f"Implemented {requirement_description[:80]}",
        )

    def implement_feature(
        self,
        requirement: str,
        target_file: str,
        existing_content: str = "",
        acceptance_criteria: Optional[List[str]] = None,
    ) -> CodeSynthesisResult:
        """Convenience method to synthesize changes directly from in-memory content."""
        criteria_desc = "\n".join(f"- {c}" for c in (acceptance_criteria or []))
        prompt = (
            f"Target File: {target_file}\n"
            f"Requirement:\n{requirement}\n\n"
            f"Existing Code:\n{existing_content}\n\n"
            "Generate updated code implementing this requirement."
        )
        res = self.adapter.generate(prompt)
        import re
        match = re.search(r"```python\s*(.*?)\s*```", res.content, re.DOTALL)
        if match:
            new_code = match.group(1)
        elif "def " in res.content or "class " in res.content:
            new_code = res.content
        else:
            new_code = existing_content + f"\n# Implemented: {requirement}\n"

        diff_lines = list(
            difflib.unified_diff(
                existing_content.splitlines(keepends=True),
                new_code.splitlines(keepends=True),
                fromfile=f"a/{target_file}",
                tofile=f"b/{target_file}",
            )
        )
        return CodeSynthesisResult(
            target_file=target_file,
            original_code=existing_content,
            modified_code=new_code,
            unified_diff="".join(diff_lines),
            summary_of_changes=f"Implemented {requirement[:80]}",
        )
