"""Autonomous Diagnosis and Repair Agent.
Implements the 8-step fault reproduction, localization, minimal patch synthesis,
and multi-stage independent verification loop.
"""
from __future__ import annotations

import difflib
import json
import re
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from backend.adapters.base import BaseModelAdapter, sanitize_untrusted_input
from backend.agents.verifier import IndependentVerifier, apply_unified_diff
from backend.models.contracts import (
    AgentType,
    ExecutionEvidence,
    PatchVerificationStatus,
    ProposedPatch,
    RiskLevel,
    TestCaseResult,
    TestStatus,
)
from backend.runners.sandbox import SandboxExecutionEngine
from backend.learning.agent_interface import BaseContinuousLearningAgent


class AutonomousRepairAgent(BaseContinuousLearningAgent):
    """Orchestrates automated failure diagnosis and verified patch synthesis."""

    def __init__(
        self,
        model_adapter: BaseModelAdapter,
        sandbox_engine: Optional[SandboxExecutionEngine] = None,
        verifier: Optional[IndependentVerifier] = None,
    ):
        super().__init__(agent_type=AgentType.REPAIR)
        self.adapter = model_adapter
        self.sandbox = sandbox_engine or SandboxExecutionEngine()
        self.verifier = verifier or IndependentVerifier(self.sandbox)

    def diagnose_and_repair(
        self,
        project_dir: Path,
        failing_test_path: str,
        target_source_path: Path,
        regression_test_paths: Optional[List[str]] = None,
    ) -> ProposedPatch:
        """
        Executes the full 8-step autonomous diagnosis and repair workflow.
        """
        patch_id = f"patch_{uuid.uuid4().hex[:8]}"
        reg_paths = regression_test_paths or []

        # Step 1: Reproduce failure in isolated container/sandbox
        repro_evidence, repro_report = self.sandbox.run_pytest(
            target_dir=project_dir,
            test_path=failing_test_path,
            ephemeral_clone=True,
        )

        original_failure_log = repro_evidence.stderr or repro_evidence.stdout
        if repro_report and repro_report.test_cases:
            for tc in repro_report.test_cases:
                if tc.status in (TestStatus.FAILED, TestStatus.ERROR):
                    original_failure_log = f"{tc.error_message}\n{tc.stack_trace or ''}"
                    break

        # Step 2: Collect source code & context
        target_source = target_source_path.read_text(encoding="utf-8") if target_source_path.exists() else ""
        safe_source = sanitize_untrusted_input(target_source, "source_code")
        safe_log = sanitize_untrusted_input(original_failure_log, "test_failure_log")

        # Step 3 & 4: Investigate root cause and propose minimal patch
        system_instruction = (
            "You are a Senior Principal Systems Engineer specializing in automated program repair.\n"
            "Analyze the failing test output and target source code.\n"
            "Provide a minimal, targeted bug fix.\n"
            "Rules:\n"
            "1. Output a valid unified diff (e.g., --- a/file.py\\n+++ b/file.py\\n@@ ... @@) inside a ```diff ``` code block.\n"
            "2. Keep the patch strictly minimal to address the root cause. Do NOT reformat unrelated code.\n"
            "3. Provide a brief 2-sentence explanation of the root cause.\n"
            "4. Never introduce security vulnerabilities or remove assertions."
        )

        prompt = (
            f"Failing Test: {failing_test_path}\n"
            f"Target Source File: {target_source_path.name}\n\n"
            f"Test Failure Log:\n{safe_log}\n\n"
            f"Source Code:\n{safe_source}\n\n"
            "Generate the root cause explanation and minimal unified diff patch."
        )

        response = self.adapter.generate(prompt, system_instruction=system_instruction)

        # Extract unified diff
        diff_match = re.search(r"```diff\s*(.*?)\s*```", response.content, re.DOTALL)
        if diff_match:
            diff_text = diff_match.group(1).strip()
        else:
            diff_text = response.content.strip()

        # Build initial patch model
        proposed_patch = ProposedPatch(
            patch_id=patch_id,
            target_files=[str(target_source_path.relative_to(project_dir)) if target_source_path.is_relative_to(project_dir) else target_source_path.name],
            unified_diff=diff_text,
            root_cause_analysis=response.content[:300],
            fault_location=target_source_path.name,
            risk_level=RiskLevel.LOW,
            original_failure_log=original_failure_log,
            reproduction_evidence=repro_evidence,
        )

        # Step 5, 6, 7: Independent multi-stage verification
        status, target_ev, reg_ev = self.verifier.verify_patch(
            base_repo_dir=project_dir,
            failing_test_path=failing_test_path,
            regression_test_paths=reg_paths,
            patch=proposed_patch,
        )

        # Step 8: Final report and status assignment
        proposed_patch.verification_status = status
        proposed_patch.isolated_test_evidence = target_ev
        proposed_patch.regression_evidence = reg_ev
        proposed_patch.independent_verification_evidence = target_ev

        return proposed_patch
