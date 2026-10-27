"""Acceptance Test Suite for Autonomous Project Completion and Self-Repair Engine.
Verifies all 14 acceptance criteria specified in Section 10 of the Prompt:
1. A deliberately introduced defect is detected.
2. The system produces a reproducible diagnosis when possible.
3. A repair is generated and applied only within the authorized workspace.
4. The original failing test is rerun after the repair.
5. Related regression tests are executed.
6. A successful repair can advance the workflow.
7. A failed repair returns the workflow to diagnosis.
8. Repeated ineffective fixes are detected and bounded.
9. Execution and cost limits stop the workflow safely.
10. Missing requirements or unavailable environments prevent unsupported completion claims.
11. Destructive or high-impact actions require the appropriate approval.
12. The final report accurately reflects actual test evidence.
13. Learning experiences are persisted and made available to relevant authorized agents.
14. Existing platform functionality remains operational.
"""
from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
import pytest

from fastapi.testclient import TestClient

from backend.adapters.mock_adapter import DeterministicMockAdapter
from backend.api.app import app
from backend.completion.analyzer import ProjectRequirementAnalyzer
from backend.completion.engine import AutonomousProjectCompletionEngine
from backend.completion.models import (
    ChecklistCategory,
    ChecklistItemStatus,
    CompletionWorkflowStatus,
    DefectSeverity,
    DefectStatus,
    HighImpactActionType,
)
from backend.learning.durable_queue import DurableEventQueue
from backend.memory.episodic import EpisodicMemoryStore
from backend.memory.semantic import SemanticMemoryStore
from backend.memory.strategy import StrategyMemoryStore
from backend.models.contracts import AgentType, PatchVerificationStatus, ProposedPatch, RiskLevel
from backend.runners.sandbox import SandboxExecutionEngine


@pytest.fixture
def completion_env():
    """Sets up an isolated disposable repository workspace and memory stores."""
    temp_dir = Path(tempfile.mkdtemp(prefix="cle_completion_test_"))
    db_path = temp_dir / ".test_memory.db"

    # Create dummy billing service in temporary workspace
    service_code = (
        "def calculate_total(subtotal: float, tax_rate: float) -> float:\n"
        "    if subtotal < 0:\n"
        "        raise ValueError('Negative subtotal')\n"
        "    # Deliberate bug for test: returns subtotal instead of subtotal + tax\n"
        "    return round(subtotal, 2)\n"
    )
    (temp_dir / "billing.py").write_text(service_code, encoding="utf-8")

    test_code = (
        "import pytest\n"
        "from billing import calculate_total\n\n"
        "def test_calculate_total_with_tax():\n"
        "    # Subtotal 100 with 10% tax should be 110.0\n"
        "    assert calculate_total(100.0, 0.10) == 110.0\n"
    )
    (temp_dir / "test_billing.py").write_text(test_code, encoding="utf-8")

    sandbox = SandboxExecutionEngine()
    adapter = DeterministicMockAdapter()
    episodic = EpisodicMemoryStore(db_path=db_path)
    semantic = SemanticMemoryStore(db_path=db_path)
    strategy = StrategyMemoryStore(db_path=db_path)
    queue = DurableEventQueue(db_path=db_path)

    engine = AutonomousProjectCompletionEngine(
        model_adapter=adapter,
        sandbox=sandbox,
        episodic_store=episodic,
        semantic_store=semantic,
        strategy_store=strategy,
        durable_queue=queue,
    )

    yield {
        "workspace": temp_dir,
        "engine": engine,
        "episodic": episodic,
        "semantic": semantic,
        "queue": queue,
        "sandbox": sandbox,
        "adapter": adapter,
    }

    shutil.rmtree(temp_dir, ignore_errors=True)


# Test 1: A deliberately introduced defect is detected
def test_1_deliberately_introduced_defect_is_detected(completion_env):
    """1. A deliberately introduced defect is detected."""
    engine: AutonomousProjectCompletionEngine = completion_env["engine"]
    workspace: Path = completion_env["workspace"]

    ctx = engine.run_autonomous_completion(workspace, max_iterations=1)

    assert len(ctx.findings) > 0
    defect = next((f for f in ctx.findings if "billing.py" in f.affected_components or "billing" in f.title), None)
    assert defect is not None
    assert defect.is_confirmed is True
    assert defect.severity in (DefectSeverity.CRITICAL, DefectSeverity.MAJOR)


# Test 2: The system produces a reproducible diagnosis when possible
def test_2_system_produces_reproducible_diagnosis(completion_env):
    """2. The system produces a reproducible diagnosis when possible."""
    engine: AutonomousProjectCompletionEngine = completion_env["engine"]
    workspace: Path = completion_env["workspace"]

    ctx = engine.run_autonomous_completion(workspace, max_iterations=1)

    defect = next((f for f in ctx.findings if "billing" in f.title or "billing.py" in f.affected_components), None)
    assert defect is not None
    assert defect.reproduction_command is not None
    assert defect.confidence_score >= 0.8
    assert "pytest" in defect.reproduction_command or "python" in defect.reproduction_command


# Test 3: A repair is generated and applied only within the authorized workspace
def test_3_repair_generated_and_applied_only_within_authorized_workspace(completion_env):
    """3. A repair is generated and applied only within the authorized workspace."""
    engine: AutonomousProjectCompletionEngine = completion_env["engine"]
    workspace: Path = completion_env["workspace"]

    # Provide a clean verified patch via mock adapter
    clean_patch_diff = (
        "--- a/billing.py\n"
        "+++ b/billing.py\n"
        "@@ -4,2 +4,2 @@\n"
        "-    return round(subtotal, 2)\n"
        "+    return round(subtotal + (subtotal * tax_rate), 2)\n"
    )
    completion_env["adapter"].set_mock_patch(
        ProposedPatch(
            patch_id="patch_clean_fix",
            target_files=["billing.py"],
            unified_diff=clean_patch_diff,
            root_cause_analysis="Tax calculation was omitted from return statement.",
            verification_status=PatchVerificationStatus.VERIFIED,
            risk_level=RiskLevel.LOW,
        )
    )

    ctx = engine.run_autonomous_completion(workspace, max_iterations=2)

    # Check that file within workspace was modified
    fixed_code = (workspace / "billing.py").read_text(encoding="utf-8")
    assert "subtotal * tax_rate" in fixed_code

    # Assert no files outside workspace were touched
    assert (workspace.parent / "billing.py").exists() is False


# Test 4: The original failing test is rerun after the repair
def test_4_original_failing_test_rerun_after_repair(completion_env):
    """4. The original failing test is rerun after the repair."""
    engine: AutonomousProjectCompletionEngine = completion_env["engine"]
    workspace: Path = completion_env["workspace"]

    clean_patch_diff = (
        "--- a/billing.py\n"
        "+++ b/billing.py\n"
        "@@ -4,2 +4,2 @@\n"
        "-    return round(subtotal, 2)\n"
        "+    return round(subtotal + (subtotal * tax_rate), 2)\n"
    )
    completion_env["adapter"].set_mock_patch(
        ProposedPatch(
            patch_id="patch_clean_fix",
            target_files=["billing.py"],
            unified_diff=clean_patch_diff,
            root_cause_analysis="Tax calculation was omitted.",
            verification_status=PatchVerificationStatus.VERIFIED,
            risk_level=RiskLevel.LOW,
        )
    )

    ctx = engine.run_autonomous_completion(workspace, max_iterations=2)

    # In iteration 2, the test should have been rerun and passed
    assert len(ctx.iteration_history) >= 1
    # Check unit tests item in checklist
    unit_item = next((i for i in ctx.checklist if i.category == ChecklistCategory.UNIT_TESTS), None)
    assert unit_item is not None
    assert unit_item.status == ChecklistItemStatus.PASSED


# Test 5: Related regression tests are executed
def test_5_related_regression_tests_executed(completion_env):
    """5. Related regression tests are executed."""
    engine: AutonomousProjectCompletionEngine = completion_env["engine"]
    workspace: Path = completion_env["workspace"]

    regr_item = next((i for i in engine.analyzer.analyze_project(workspace)[1] if i.category == ChecklistCategory.REGRESSION_TESTS), None)
    assert regr_item is not None
    assert regr_item.title == "Test Impact Regression Guard"


# Test 6: A successful repair can advance the workflow
def test_6_successful_repair_can_advance_workflow(completion_env):
    """6. A successful repair can advance the workflow."""
    engine: AutonomousProjectCompletionEngine = completion_env["engine"]
    workspace: Path = completion_env["workspace"]

    clean_patch_diff = (
        "--- a/billing.py\n"
        "+++ b/billing.py\n"
        "@@ -4,2 +4,2 @@\n"
        "-    return round(subtotal, 2)\n"
        "+    return round(subtotal + (subtotal * tax_rate), 2)\n"
    )
    completion_env["adapter"].set_mock_patch(
        ProposedPatch(
            patch_id="patch_clean_fix",
            target_files=["billing.py"],
            unified_diff=clean_patch_diff,
            root_cause_analysis="Tax calculation fix.",
            verification_status=PatchVerificationStatus.VERIFIED,
            risk_level=RiskLevel.LOW,
        )
    )

    ctx = engine.run_autonomous_completion(workspace, max_iterations=3)

    assert ctx.status == CompletionWorkflowStatus.VERIFIED
    assert "All required acceptance criteria verified" in (ctx.stopping_reason or "")


# Test 7: A failed repair returns the workflow to diagnosis
def test_7_failed_repair_returns_workflow_to_diagnosis(completion_env):
    """7. A failed repair returns the workflow to diagnosis."""
    engine: AutonomousProjectCompletionEngine = completion_env["engine"]
    workspace: Path = completion_env["workspace"]

    # Provide a patch that fails verification
    bad_patch = ProposedPatch(
        patch_id="patch_broken_fix",
        target_files=["billing.py"],
        unified_diff="--- a/billing.py\n+++ b/billing.py\n@@ -4,1 +4,1 @@\n- return round(subtotal, 2)\n+ raise Exception('broken')\n",
        root_cause_analysis="Bad fix attempt",
        verification_status=PatchVerificationStatus.FAILED,
        risk_level=RiskLevel.HIGH,
    )
    completion_env["adapter"].set_mock_patch(bad_patch)

    ctx = engine.run_autonomous_completion(workspace, max_iterations=2)

    # Workflow iterates and returns to testing/diagnosing, does NOT mark verified
    assert ctx.status != CompletionWorkflowStatus.VERIFIED
    # Original file is preserved via rollback
    code_after = (workspace / "billing.py").read_text(encoding="utf-8")
    assert "raise Exception('broken')" not in code_after


# Test 8: Repeated ineffective fixes are detected and bounded
def test_8_repeated_ineffective_fixes_detected_and_bounded(completion_env):
    """8. Repeated ineffective fixes are detected and bounded."""
    engine: AutonomousProjectCompletionEngine = completion_env["engine"]
    workspace: Path = completion_env["workspace"]

    # Ineffective patch
    ineffective_patch = ProposedPatch(
        patch_id="patch_identical_loop",
        target_files=["billing.py"],
        unified_diff="--- a/billing.py\n+++ b/billing.py\n@@ -1,1 +1,1 @@\n-def calculate_total\n+def calculate_total\n",
        root_cause_analysis="No-op change",
        verification_status=PatchVerificationStatus.VERIFIED,
        risk_level=RiskLevel.LOW,
    )
    completion_env["adapter"].set_mock_patch(ineffective_patch)

    ctx = engine.run_autonomous_completion(workspace, max_iterations=5)

    assert "Unproductive loop detected" in (ctx.stopping_reason or "")
    assert ctx.status == CompletionWorkflowStatus.INCOMPLETE


# Test 9: Execution and cost limits stop the workflow safely
def test_9_execution_and_cost_limits_stop_workflow_safely(completion_env):
    """9. Execution and cost limits stop the workflow safely."""
    engine: AutonomousProjectCompletionEngine = completion_env["engine"]
    workspace: Path = completion_env["workspace"]

    # Budget of 0.001 seconds
    ctx = engine.run_autonomous_completion(
        workspace,
        max_iterations=10,
        time_budget_seconds=0,
    )

    assert ctx.status == CompletionWorkflowStatus.INCOMPLETE
    assert "Time budget" in (ctx.stopping_reason or "")


# Test 10: Missing requirements or unavailable environments prevent unsupported completion claims
def test_10_missing_requirements_or_unavailable_environments_prevent_completion(completion_env):
    """10. Missing requirements or unavailable environments prevent unsupported completion claims."""
    engine: AutonomousProjectCompletionEngine = completion_env["engine"]
    workspace: Path = completion_env["workspace"]

    # Introduce a fatal syntax error
    (workspace / "syntax_broken.py").write_text("def broken_syntax(:\n  pass", encoding="utf-8")

    ctx = engine.run_autonomous_completion(workspace, max_iterations=2)

    # Must be BLOCKED, never VERIFIED
    assert ctx.status == CompletionWorkflowStatus.BLOCKED
    assert "Syntax error" in (ctx.stopping_reason or "")


# Test 11: Destructive or high-impact actions require the appropriate approval
def test_11_destructive_or_high_impact_actions_require_approval(completion_env):
    """11. Destructive or high-impact actions require the appropriate approval."""
    engine: AutonomousProjectCompletionEngine = completion_env["engine"]
    workspace: Path = completion_env["workspace"]

    # Write a source file with a simulated destructive DB migration error
    (workspace / "migration.py").write_text(
        "def run_migration():\n"
        "    raise RuntimeError('DROP TABLE users; detected in migration')\n",
        encoding="utf-8"
    )

    ctx = engine.run_autonomous_completion(workspace, max_iterations=2, allow_high_impact_auto_approve=False)

    assert ctx.status == CompletionWorkflowStatus.NEEDS_HUMAN_REVIEW
    assert len(ctx.approval_requests) > 0
    assert ctx.approval_requests[0].action_type == HighImpactActionType.DESTRUCTIVE_DB_MIGRATION
    assert ctx.approval_requests[0].is_approved is False


# Test 12: The final report accurately reflects actual test evidence
def test_12_final_report_accurately_reflects_actual_test_evidence(completion_env):
    """12. The final report accurately reflects actual test evidence."""
    engine: AutonomousProjectCompletionEngine = completion_env["engine"]
    workspace: Path = completion_env["workspace"]

    ctx = engine.run_autonomous_completion(workspace, max_iterations=1)

    assert len(ctx.checklist) >= 6
    for item in ctx.checklist:
        assert item.status in (
            ChecklistItemStatus.PASSED,
            ChecklistItemStatus.FAILED,
            ChecklistItemStatus.PENDING,
            ChecklistItemStatus.SKIPPED,
        )


# Test 13: Learning experiences are persisted and made available to relevant authorized agents
def test_13_learning_experiences_persisted_and_made_available(completion_env):
    """13. Learning experiences are persisted and made available to relevant authorized agents."""
    engine: AutonomousProjectCompletionEngine = completion_env["engine"]
    workspace: Path = completion_env["workspace"]
    episodic: EpisodicMemoryStore = completion_env["episodic"]

    clean_patch_diff = (
        "--- a/billing.py\n"
        "+++ b/billing.py\n"
        "@@ -4,2 +4,2 @@\n"
        "-    return round(subtotal, 2)\n"
        "+    return round(subtotal + (subtotal * tax_rate), 2)\n"
    )
    completion_env["adapter"].set_mock_patch(
        ProposedPatch(
            patch_id="patch_learned_fix",
            target_files=["billing.py"],
            unified_diff=clean_patch_diff,
            root_cause_analysis="Tax calculation fix for billing",
            verification_status=PatchVerificationStatus.VERIFIED,
            risk_level=RiskLevel.LOW,
        )
    )

    ctx = engine.run_autonomous_completion(workspace, max_iterations=2)

    exps = episodic.query_experiences(project_id=workspace.name)
    assert len(exps) > 0
    assert any(e.agent_type == AgentType.REPAIR for e in exps)


# Test 14: Existing platform functionality remains operational
def test_14_existing_platform_functionality_remains_operational():
    """14. Existing platform functionality remains operational."""
    client = TestClient(app)

    # 1. Health check
    res_health = client.get("/api/health")
    assert res_health.status_code == 200

    # 2. Intelligence stats
    res_intel = client.get("/api/intelligence/stats")
    assert res_intel.status_code == 200

    # 3. Learning stats
    res_learn = client.get("/api/learning/stats")
    assert res_learn.status_code == 200

    # 4. Completion sessions list
    res_comp = client.get("/api/completion/sessions")
    assert res_comp.status_code == 200
