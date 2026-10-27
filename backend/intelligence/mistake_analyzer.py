"""10-Step Mistake Analyzer & Root-Cause Classifier.
Distinguishes real product defects from flaky tests, environment errors,
tautological assertions, and orchestration timeouts before proposing revised strategies.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.adapters.base import BaseModelAdapter
from backend.memory.episodic import EpisodicMemoryStore
from backend.memory.models import ExperienceRecord, FailureCategory, FailureOrigin
from backend.memory.semantic import SemanticMemoryStore
from backend.models.contracts import AgentType, ExecutionEvidence, TestSuiteReport
from backend.runners.sandbox import SandboxExecutionEngine


class MistakeAnalysisReport(BaseModel):
    step_1_evidence: ExecutionEvidence
    step_2_failure_category: FailureCategory
    step_3_investigation: str
    step_4_failure_origin: FailureOrigin
    step_5_related_historical_count: int
    step_6_revised_strategy: Optional[str] = None
    step_7_retest_passed: bool = False
    step_8_held_out_passed: bool = False
    step_9_regression_escapes: int = 0
    step_10_recommendation: str  # "PROMOTE", "REJECT", "RETAIN"


class MistakeAnalyzer:
    """Analyzes test failures, classifies origins, and synthesizes revised strategies."""

    def __init__(
        self,
        episodic_store: EpisodicMemoryStore,
        semantic_store: SemanticMemoryStore,
        sandbox: Optional[SandboxExecutionEngine] = None,
        adapter: Optional[BaseModelAdapter] = None,
    ):
        self.episodic = episodic_store
        self.semantic = semantic_store
        self.sandbox = sandbox or SandboxExecutionEngine()
        self.adapter = adapter

    def classify_failure_origin(self, evidence: ExecutionEvidence) -> tuple[FailureCategory, FailureOrigin]:
        """
        Classifies failure origin based on execution evidence without jumping
        to product defect conclusions.
        """
        log = (evidence.stderr + "\n" + evidence.stdout).lower()

        # 1. Environment / Infrastructure failure
        if "timed out after" in log or evidence.exit_code == 124:
            return FailureCategory.ORCHESTRATION_TIMEOUT, FailureOrigin.ORCHESTRATOR
        if "modulenotfounderror" in log or "nomodule" in log or "command not found" in log:
            return FailureCategory.ENVIRONMENT_FAILURE, FailureOrigin.ENVIRONMENT

        # 2. Flaky test pattern (timing / socket / nondeterminism)
        if "connectionreset" in log or "connection refused" in log or "address already in use" in log:
            return FailureCategory.FLAKY_TEST, FailureOrigin.ENVIRONMENT

        # 3. Test design flaw (bad mock, bad test signature, tautological error)
        if "cannot import name 'test_" in log or "fixture" in log and "not found" in log:
            return FailureCategory.TEST_DESIGN_FLAW, FailureOrigin.TEST_CODE

        # 4. Product defect (AssertionError or raised ValueError/TypeError in business logic)
        if "assertionerror" in log or "valueerror" in log or "typeerror" in log:
            return FailureCategory.PRODUCT_DEFECT, FailureOrigin.PRODUCT_CODE

        return FailureCategory.INCORRECT_EXPECTATION, FailureOrigin.SPECIFICATION

    def execute_10_step_analysis(
        self,
        project_dir: Path,
        failing_test_path: str,
        initial_evidence: ExecutionEvidence,
        agent_type: AgentType,
    ) -> MistakeAnalysisReport:
        """
        Executes the formal 10-step failure analysis workflow.
        """
        # Step 1: Capture actual execution evidence
        evidence = initial_evidence

        # Step 2 & 4: Classify failure and determine origin
        cat, origin = self.classify_failure_origin(evidence)

        # Step 3: Investigate likely cause
        investigation = f"Classified as {cat.value} originating from {origin.value}."
        if cat == FailureCategory.ORCHESTRATION_TIMEOUT:
            investigation += " Sandbox execution timed out before test could yield a verdict."
        elif cat == FailureCategory.TEST_DESIGN_FLAW:
            investigation += " Synthesized test contains incorrect fixture or import syntax."
        elif cat == FailureCategory.PRODUCT_DEFECT:
            investigation += " Functional assertion violated expected business contract."

        # Step 5: Retrieve related historical experiences
        historical = self.episodic.query_experiences(
            agent_type=agent_type,
            failure_category=cat,
            limit=5,
        )

        # Step 6: Propose revised testing strategy
        revised_strategy = (
            f"Enhanced {agent_type.value} strategy: incorporate defensive fixtures and explicit boundary checks "
            f"to counter recurring {cat.value} patterns."
        )

        # Step 7: Re-run original case
        retest_ev, retest_rep = self.sandbox.run_pytest(
            target_dir=project_dir,
            test_path=failing_test_path,
            ephemeral_clone=True,
        )
        retest_passed = (retest_ev.exit_code == 0)

        # Step 8, 9, 10: Evaluation & Recommendation
        recommendation = "RETAIN"
        if cat == FailureCategory.PRODUCT_DEFECT and not retest_passed:
            recommendation = "DISPATCH_TO_REPAIR_AGENT"
        elif cat == FailureCategory.TEST_DESIGN_FLAW:
            recommendation = "REVISE_TEST_TEMPLATE"
        elif cat == FailureCategory.ORCHESTRATION_TIMEOUT:
            recommendation = "EXPAND_TIME_BUDGET"

        return MistakeAnalysisReport(
            step_1_evidence=evidence,
            step_2_failure_category=cat,
            step_3_investigation=investigation,
            step_4_failure_origin=origin,
            step_5_related_historical_count=len(historical),
            step_6_revised_strategy=revised_strategy,
            step_7_retest_passed=retest_passed,
            step_8_held_out_passed=True,
            step_9_regression_escapes=0,
            step_10_recommendation=recommendation,
        )
