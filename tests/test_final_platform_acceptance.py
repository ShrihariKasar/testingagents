"""Comprehensive 20-Point Acceptance Test Suite for the Complete Autonomous AI Testing Platform.
Verifies all 20 explicit acceptance criteria mandated by Part 13 of the Master Prompt:
1. A new project can be registered and analyzed.
2. The orchestrator creates a testing plan.
3. All five agents can execute their supported testing tasks.
4. Real test results are persisted.
5. A deliberately introduced defect can be detected.
6. A repair can be proposed and applied in isolation.
7. The original failure is rerun after repair.
8. Related regression tests are executed.
9. Failed repairs return to diagnosis.
10. Repeated failed repairs are bounded.
11. Completion states reflect actual evidence.
12. Continuous Learning Engine events persist across restarts.
13. Relevant lessons can be shared across authorized agents.
14. Candidate strategies are evaluated before promotion.
15. Harmful strategy changes can be rejected or rolled back.
16. Worker interruptions do not silently lose queued work.
17. Project isolation and permissions are enforced.
18. Protected operations require appropriate approval.
19. The dashboard reflects actual execution records.
20. Existing application functionality remains operational.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.adapters.mock_adapter import DeterministicMockAdapter
from backend.agents.e2e_agent import EndToEndTestingAgent, UserJourneyStep, E2ETestScenario
from backend.agents.integration_agent import IntegrationTestingAgent, APIEndpointModel
from backend.agents.performance_agent import PerformanceTestingAgent
from backend.agents.regression_agent import RegressionTestingAgent
from backend.agents.repair_agent import AutonomousRepairAgent
from backend.agents.unit_agent import UnitTestingAgent
from backend.agents.verifier import IndependentVerifier, apply_unified_diff
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
from backend.intelligence.bus import KnowledgeExchangeBus, KnowledgeMessage
from backend.intelligence.cross_teaching import CrossAgentTeacher
from backend.intelligence.learning_evaluator import IndependentStrategyEvaluator
from backend.intelligence.mistake_analyzer import MistakeAnalyzer
from backend.learning.durable_queue import DurableEventQueue
from backend.learning.models import LearningEvent, LearningEventType, ProjectProfile
from backend.learning.profiler import ProjectProfiler
from backend.learning.service import ContinuousLearningService
from backend.memory.episodic import EpisodicMemoryStore
from backend.memory.models import (
    ExperienceRecord,
    FailureCategory,
    FailureOrigin,
    KnowledgeScope,
    LessonValidationStatus,
    SemanticLesson,
    StrategyRecord,
    StrategyStatus,
)
from backend.memory.project_store import ProjectStore, ProjectRecord
from backend.memory.semantic import SemanticMemoryStore
from backend.memory.strategy import StrategyMemoryStore
from backend.models.contracts import (
    AgentType,
    ExecutionEvidence,
    PatchVerificationStatus,
    ProposedPatch,
    RiskLevel,
    RunContext,
)
from backend.orchestrator.coordinator import CentralTestingOrchestrator
from backend.runners.sandbox import SandboxExecutionEngine


@pytest.fixture
def platform_env():
    """Sets up an isolated disposable repository workspace and test database."""
    temp_dir = Path(tempfile.mkdtemp(prefix="platform_master_test_"))
    db_path = temp_dir / ".master_memory.db"

    # Create dummy service with a known bug
    service_code = (
        "def calculate_total(subtotal: float, tax_rate: float) -> float:\n"
        "    if subtotal < 0:\n"
        "        raise ValueError('Negative subtotal')\n"
        "    # Deliberate bug: returns subtotal without tax\n"
        "    return round(subtotal, 2)\n"
    )
    (temp_dir / "billing.py").write_text(service_code, encoding="utf-8")

    test_code = (
        "import pytest\n"
        "from billing import calculate_total\n\n"
        "def test_calculate_total_with_tax():\n"
        "    assert calculate_total(100.0, 0.10) == 110.0\n"
    )
    (temp_dir / "test_billing.py").write_text(test_code, encoding="utf-8")

    sandbox = SandboxExecutionEngine()
    adapter = DeterministicMockAdapter()
    episodic = EpisodicMemoryStore(db_path=db_path)
    semantic = SemanticMemoryStore(db_path=db_path)
    strategy = StrategyMemoryStore(db_path=db_path)
    queue = DurableEventQueue(db_path=db_path)
    pstore = ProjectStore(db_path=db_path)

    orchestrator = CentralTestingOrchestrator(
        model_adapter=adapter,
        sandbox_engine=sandbox,
        db_path=db_path,
    )

    completion_engine = AutonomousProjectCompletionEngine(
        model_adapter=adapter,
        sandbox=sandbox,
        episodic_store=episodic,
        semantic_store=semantic,
        strategy_store=strategy,
        durable_queue=queue,
    )

    yield {
        "workspace": temp_dir,
        "db_path": db_path,
        "adapter": adapter,
        "sandbox": sandbox,
        "episodic": episodic,
        "semantic": semantic,
        "strategy": strategy,
        "queue": queue,
        "pstore": pstore,
        "orchestrator": orchestrator,
        "completion_engine": completion_engine,
    }

    shutil.rmtree(temp_dir, ignore_errors=True)


# ==============================================================================
# Criterion 1: A new project can be registered and analyzed
# ==============================================================================
def test_platform_1_project_registration_and_analysis(platform_env):
    workspace = platform_env["workspace"]
    pstore: ProjectStore = platform_env["pstore"]

    profile = ProjectProfiler.profile_directory(workspace)
    assert profile.project_id is not None
    assert profile.primary_language == "python"

    project_rec = ProjectRecord(
        project_id=profile.project_id,
        name="BillingService",
        repo_path=str(workspace),
        language=profile.primary_language,
        dependencies=profile.tags,
        metadata=profile.model_dump(),
    )
    saved = pstore.save_project(project_rec)
    retrieved = pstore.get_project(profile.project_id)

    assert retrieved is not None
    assert retrieved.name == "BillingService"
    assert retrieved.repo_path == str(workspace)


# ==============================================================================
# Criterion 2: The orchestrator creates a testing plan
# ==============================================================================
def test_platform_2_orchestrator_creates_testing_plan(platform_env):
    orchestrator: CentralTestingOrchestrator = platform_env["orchestrator"]
    workspace: Path = platform_env["workspace"]

    ctx = orchestrator.execute_test_plan(
        project_dir=workspace,
        target_files=["billing.py"],
        time_budget_seconds=60,
    )
    assert ctx.run_id is not None
    assert ctx.project_path == str(workspace)
    assert len(ctx.execution_logs) > 0


# ==============================================================================
# Criterion 3: All five agents can execute their supported testing tasks
# ==============================================================================
def test_platform_3_all_five_agents_execute_testing_tasks(platform_env):
    workspace = platform_env["workspace"]
    adapter = platform_env["adapter"]
    sandbox = platform_env["sandbox"]

    # 1. Unit Agent
    unit = UnitTestingAgent(adapter, sandbox)
    sym = unit.analyze_module(workspace / "billing.py")
    assert any(fn.name == "calculate_total" for fn in sym.functions)

    # 2. Integration Agent
    integration = IntegrationTestingAgent(adapter, sandbox)
    rf = workspace / "routes.py"
    rf.write_text("@app.post('/api/v1/charge')\ndef charge(): pass\n", encoding="utf-8")
    ep = APIEndpointModel(method="POST", path="/api/v1/charge", request_schema={"amount": "float"}, response_schema={"status": "str"})
    suite = integration.synthesize_integration_tests(rf, [ep])
    assert "POST /api/v1/charge" in suite.endpoints_tested

    # 3. Regression Agent
    regression = RegressionTestingAgent(adapter, sandbox)
    imp = regression.select_regression_tests(["billing.py"], workspace)
    assert "test_billing.py" in imp.selected_tests

    # 4. Performance Agent
    performance = PerformanceTestingAgent(adapter)
    perf = performance.calculate_percentiles([10.0, 20.0, 30.0, 40.0], 1.0, 0)
    assert perf.p95_ms == 40.0
    assert perf.slo_met is True

    # 5. E2E Agent
    e2e = EndToEndTestingAgent(adapter)
    scen = E2ETestScenario(
        scenario_name="checkout_flow",
        steps=[UserJourneyStep(action="navigate", target="/checkout", expected_state="Page loaded")],
    )
    e2e_suite = e2e.generate_playwright_test(scen)
    assert "checkout" in e2e_suite.scenario_name
    assert "/checkout" in e2e_suite.locators_used


# ==============================================================================
# Criterion 4: Real test results are persisted
# ==============================================================================
def test_platform_4_real_test_results_are_persisted(platform_env):
    orchestrator: CentralTestingOrchestrator = platform_env["orchestrator"]
    pstore: ProjectStore = platform_env["pstore"]
    workspace: Path = platform_env["workspace"]

    ctx = orchestrator.execute_test_plan(workspace, target_files=["billing.py"])
    pstore.save_test_run(ctx)

    loaded = pstore.get_test_run(ctx.run_id)
    assert loaded is not None
    assert loaded.run_id == ctx.run_id
    assert loaded.status == ctx.status


# ==============================================================================
# Criterion 5: A deliberately introduced defect can be detected
# ==============================================================================
def test_platform_5_deliberately_introduced_defect_detected(platform_env):
    engine: AutonomousProjectCompletionEngine = platform_env["completion_engine"]
    workspace: Path = platform_env["workspace"]

    ctx = engine.run_autonomous_completion(workspace, max_iterations=1)
    assert len(ctx.findings) > 0
    defect = next((f for f in ctx.findings if "billing" in f.title or "billing.py" in f.affected_components), None)
    assert defect is not None
    assert defect.is_confirmed is True


# ==============================================================================
# Criterion 6: A repair can be proposed and applied in isolation
# ==============================================================================
def test_platform_6_repair_proposed_and_applied_in_isolation(platform_env):
    sandbox: SandboxExecutionEngine = platform_env["sandbox"]
    verifier = IndependentVerifier(sandbox)
    workspace: Path = platform_env["workspace"]

    diff = (
        "--- a/billing.py\n"
        "+++ b/billing.py\n"
        "@@ -4,2 +4,2 @@\n"
        "-    return round(subtotal, 2)\n"
        "+    return round(subtotal + (subtotal * tax_rate), 2)\n"
    )
    patch = ProposedPatch(
        patch_id="iso_patch_1",
        target_files=["billing.py"],
        unified_diff=diff,
        root_cause_analysis="Add tax calculation",
        verification_status=PatchVerificationStatus.VERIFIED,
        risk_level=RiskLevel.LOW,
    )
    status, ev, _ = verifier.verify_patch(workspace, "test_billing.py", [], patch)
    assert status == PatchVerificationStatus.VERIFIED
    assert ev.exit_code == 0


# ==============================================================================
# Criterion 7: The original failure is rerun after repair
# ==============================================================================
def test_platform_7_original_failure_rerun_after_repair(platform_env):
    engine: AutonomousProjectCompletionEngine = platform_env["completion_engine"]
    workspace: Path = platform_env["workspace"]
    adapter: DeterministicMockAdapter = platform_env["adapter"]

    diff = (
        "--- a/billing.py\n"
        "+++ b/billing.py\n"
        "@@ -4,2 +4,2 @@\n"
        "-    return round(subtotal, 2)\n"
        "+    return round(subtotal + (subtotal * tax_rate), 2)\n"
    )
    adapter.set_mock_patch(
        ProposedPatch(
            patch_id="p_rerun",
            target_files=["billing.py"],
            unified_diff=diff,
            root_cause_analysis="Tax fix",
            verification_status=PatchVerificationStatus.VERIFIED,
            risk_level=RiskLevel.LOW,
        )
    )

    ctx = engine.run_autonomous_completion(workspace, max_iterations=2)
    unit_item = next((i for i in ctx.checklist if i.category == ChecklistCategory.UNIT_TESTS), None)
    assert unit_item is not None
    assert unit_item.status == ChecklistItemStatus.PASSED


# ==============================================================================
# Criterion 8: Related regression tests are executed
# ==============================================================================
def test_platform_8_related_regression_tests_executed(platform_env):
    regression = RegressionTestingAgent(platform_env["adapter"], platform_env["sandbox"])
    workspace: Path = platform_env["workspace"]

    report = regression.select_regression_tests(["billing.py"], workspace)
    assert "test_billing.py" in report.selected_tests
    ev, _ = regression.run_selected_regressions(workspace, report.selected_tests)
    assert ev.exit_code == 1  # Unpatched initial failure correctly captured by regression runner


# ==============================================================================
# Criterion 9: Failed repairs return to diagnosis
# ==============================================================================
def test_platform_9_failed_repairs_return_to_diagnosis(platform_env):
    engine: AutonomousProjectCompletionEngine = platform_env["completion_engine"]
    workspace: Path = platform_env["workspace"]
    adapter: DeterministicMockAdapter = platform_env["adapter"]

    bad_patch = ProposedPatch(
        patch_id="p_broken",
        target_files=["billing.py"],
        unified_diff="--- a/billing.py\n+++ b/billing.py\n@@ -1,1 +1,1 @@\n-def\n+syntax error\n",
        root_cause_analysis="Bad patch",
        verification_status=PatchVerificationStatus.FAILED,
        risk_level=RiskLevel.LOW,
    )
    adapter.set_mock_patch(bad_patch)

    ctx = engine.run_autonomous_completion(workspace, max_iterations=1)
    assert any("failed" in h.patch_verification_status.value for h in ctx.iteration_history)
    unit_item = next((i for i in ctx.checklist if i.category == ChecklistCategory.UNIT_TESTS), None)
    assert unit_item.status == ChecklistItemStatus.FAILED
    assert any(f.status == DefectStatus.OPEN for f in ctx.findings)


# ==============================================================================
# Criterion 10: Repeated failed repairs are bounded
# ==============================================================================
def test_platform_10_repeated_failed_repairs_are_bounded(platform_env):
    engine: AutonomousProjectCompletionEngine = platform_env["completion_engine"]
    workspace: Path = platform_env["workspace"]
    adapter: DeterministicMockAdapter = platform_env["adapter"]

    ineffective_diff = (
        "--- a/billing.py\n"
        "+++ b/billing.py\n"
        "@@ -4,1 +4,1 @@\n"
        "-    return round(subtotal, 2)\n"
        "+    return round(subtotal + 0.0, 2)\n"
    )
    adapter.set_mock_patch(
        ProposedPatch(
            patch_id="p_loop",
            target_files=["billing.py"],
            unified_diff=ineffective_diff,
            root_cause_analysis="Ineffective repeated patch",
            verification_status=PatchVerificationStatus.FAILED,
            risk_level=RiskLevel.LOW,
        )
    )

    ctx = engine.run_autonomous_completion(workspace, max_iterations=5)
    assert ctx.status == CompletionWorkflowStatus.INCOMPLETE
    assert "Unproductive loop" in (ctx.stopping_reason or "")
    assert ctx.current_iteration <= 2


# ==============================================================================
# Criterion 11: Completion states reflect actual evidence
# ==============================================================================
def test_platform_11_completion_states_reflect_actual_evidence(platform_env):
    engine: AutonomousProjectCompletionEngine = platform_env["completion_engine"]
    workspace: Path = platform_env["workspace"]

    # Incomplete project with missing implementation
    (workspace / "stub.py").write_text("def incomplete_feature():\n    raise NotImplementedError()\n", encoding="utf-8")

    ctx = engine.run_autonomous_completion(workspace, max_iterations=1)
    assert ctx.status != CompletionWorkflowStatus.VERIFIED
    acc_item = next((i for i in ctx.checklist if i.category == ChecklistCategory.ACCEPTANCE_CRITERIA), None)
    assert acc_item is not None
    assert acc_item.status != ChecklistItemStatus.PASSED
    assert any("incomplete" in f.title.lower() for f in ctx.findings)


# ==============================================================================
# Criterion 12: Continuous Learning Engine events persist across restarts
# ==============================================================================
def test_platform_12_learning_events_persist_across_restarts(platform_env):
    db_path = platform_env["db_path"]
    q1 = DurableEventQueue(db_path=db_path)

    ev = LearningEvent(
        event_id="ev_restart_1",
        run_id="run_restart_1",
        idempotency_key="key_restart_1",
        project_id="proj_restart",
        agent_type=AgentType.UNIT,
        event_type=LearningEventType.FAILURE_DETECTED,
        payload={"msg": "persisted event across restarts"},
    )
    q1.enqueue(ev)

    # Simulate restart by instantiating new queue object on same sqlite db
    q2 = DurableEventQueue(db_path=db_path)
    dequeued = q2.fetch_pending_batch(limit=5)
    assert len(dequeued) == 1
    assert dequeued[0].event_id == "ev_restart_1"


# ==============================================================================
# Criterion 13: Relevant lessons can be shared across authorized agents
# ==============================================================================
def test_platform_13_relevant_lessons_shared_across_authorized_agents(platform_env):
    semantic: SemanticMemoryStore = platform_env["semantic"]
    bus = KnowledgeExchangeBus()
    teacher = CrossAgentTeacher(bus=bus, semantic_store=semantic)

    lesson = SemanticLesson(
        lesson_id="les_share_1",
        source_agent=AgentType.UNIT,
        target_agents=[AgentType.INTEGRATION, AgentType.REGRESSION],
        title="Check HTTP status code 422 for malformed payloads",
        problem_context="Unvalidated payload raises unhandled exception",
        recommendation="Verify schema validation before database write",
        confidence_score=0.92,
        validation_status=LessonValidationStatus.VALIDATED,
        scope=KnowledgeScope.GLOBAL_VERIFIED,
    )
    semantic.store_lesson(lesson)

    delivered = teacher.share_lesson_across_agents(lesson)
    assert len(delivered) >= 2
    recipients = {d.recipient_agent for d in delivered if d.adopted}
    assert AgentType.INTEGRATION in recipients
    assert AgentType.REGRESSION in recipients


# ==============================================================================
# Criterion 14: Candidate strategies are evaluated before promotion
# ==============================================================================
def test_platform_14_candidate_strategies_evaluated_before_promotion(platform_env):
    strategy: StrategyMemoryStore = platform_env["strategy"]
    evaluator = IndependentStrategyEvaluator(strategy)

    cand = StrategyRecord(
        strategy_id="strat_cand_unit",
        agent_type=AgentType.UNIT,
        version="2.0.0",
        prompt_template="Improved unit test synthesis with mutation guards",
        benchmark_scores={"mutation_score": 0.85, "defect_detection": 0.80},
    )
    verdict = evaluator.compare_and_decide_promotion(cand, AgentType.UNIT)
    assert verdict.is_promoted is True
    assert verdict.verdict == "PROMOTE_TO_PRODUCTION"
    assert strategy.get_production_strategy(AgentType.UNIT).version == "2.0.0"


# ==============================================================================
# Criterion 15: Harmful strategy changes can be rejected or rolled back
# ==============================================================================
def test_platform_15_harmful_strategy_rejected_or_rolled_back(platform_env):
    strategy: StrategyMemoryStore = platform_env["strategy"]
    evaluator = IndependentStrategyEvaluator(strategy)

    harmful = StrategyRecord(
        strategy_id="strat_harmful",
        agent_type=AgentType.UNIT,
        version="2.1.0",
        prompt_template="Degraded prompt",
        benchmark_scores={"mutation_score": 0.30, "defect_detection": 0.25},
    )
    verdict = evaluator.compare_and_decide_promotion(harmful, AgentType.UNIT)
    assert verdict.is_promoted is False
    assert "REJECT" in verdict.verdict


# ==============================================================================
# Criterion 16: Worker interruptions do not silently lose queued work
# ==============================================================================
def test_platform_16_worker_interruptions_do_not_lose_queued_work(platform_env):
    db_path = platform_env["db_path"]
    q = DurableEventQueue(db_path=db_path)

    ev = LearningEvent(
        event_id="ev_interrupted",
        run_id="run_interrupted",
        idempotency_key="key_interrupted",
        project_id="proj_interrupted",
        agent_type=AgentType.UNIT,
        event_type=LearningEventType.TEST_TASK_COMPLETED,
        payload={"task": "crash simulated"},
    )
    q.enqueue(ev)

    # Dequeue marks event as PROCESSING
    batch = q.fetch_pending_batch(limit=1)
    assert len(batch) == 1

    # Worker crashes without marking PROCESSED; reset stuck events
    recovered = q.recover_stuck_processing_jobs(max_retry=3)
    assert recovered == 1

    # Event is back to PENDING and recoverable
    batch2 = q.fetch_pending_batch(limit=1)
    assert len(batch2) == 1
    assert batch2[0].event_id == "ev_interrupted"


# ==============================================================================
# Criterion 17: Project isolation and permissions are enforced
# ==============================================================================
def test_platform_17_project_isolation_and_permissions(platform_env):
    semantic: SemanticMemoryStore = platform_env["semantic"]

    # Store project-scoped private lesson
    private_lesson = SemanticLesson(
        lesson_id="les_secret_proj_a",
        source_agent=AgentType.UNIT,
        target_agents=[AgentType.UNIT],
        title="Project A Proprietary Invariant",
        problem_context="Tenant A private API rule",
        recommendation="Use Tenant A token",
        validation_status=LessonValidationStatus.VALIDATED,
        scope=KnowledgeScope.PROJECT_SPECIFIC,
        project_id="Project_A",
    )
    semantic.store_lesson(private_lesson)

    # Query from Project_B must NOT retrieve Project_A private lesson
    results = semantic.search_lessons("Proprietary Invariant", target_agent=AgentType.UNIT, current_project_id="Project_B")
    retrieved_ids = [l.lesson_id for l, s in results]
    assert "les_secret_proj_a" not in retrieved_ids


# ==============================================================================
# Criterion 18: Protected operations require appropriate approval
# ==============================================================================
def test_platform_18_protected_operations_require_approval(platform_env):
    engine: AutonomousProjectCompletionEngine = platform_env["completion_engine"]
    workspace: Path = platform_env["workspace"]

    (workspace / "db_migration.py").write_text(
        "def migrate():\n"
        "    # Destructive operation\n"
        "    sql = 'DROP TABLE customers;'\n",
        encoding="utf-8",
    )

    ctx = engine.run_autonomous_completion(workspace, max_iterations=2, allow_high_impact_auto_approve=False)
    assert ctx.status == CompletionWorkflowStatus.NEEDS_HUMAN_REVIEW
    assert len(ctx.approval_requests) > 0
    assert ctx.approval_requests[0].action_type == HighImpactActionType.DESTRUCTIVE_DB_MIGRATION
    assert ctx.approval_requests[0].is_approved is False


# ==============================================================================
# Criterion 19: The dashboard reflects actual execution records
# ==============================================================================
def test_platform_19_dashboard_reflects_actual_records(platform_env):
    client = TestClient(app)
    pstore: ProjectStore = platform_env["pstore"]

    reg = client.post("/api/projects/register", json={"name": "DashboardTestProj", "repo_path": "."})
    assert reg.status_code == 200

    resp = client.get("/api/projects")
    assert resp.status_code == 200
    projects = resp.json()
    assert any(p["name"] == "DashboardTestProj" for p in projects)

    audits = client.get("/api/audit/logs")
    assert audits.status_code == 200
    assert len(audits.json()) > 0


# ==============================================================================
# Criterion 20: Existing application functionality remains operational
# ==============================================================================
def test_platform_20_existing_functionality_remains_operational(platform_env):
    client = TestClient(app)

    # Health API
    h = client.get("/api/health")
    assert h.status_code == 200
    assert h.json()["status"] in ("online", "healthy")

    # Intelligence stats
    stats = client.get("/api/intelligence/stats")
    assert stats.status_code == 200
    assert "active_production_strategies" in stats.json()

    # Learning stats
    l_stats = client.get("/api/learning/stats")
    assert l_stats.status_code == 200
    assert "queue_depth" in l_stats.json()
