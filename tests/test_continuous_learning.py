"""Acceptance test suite for Continuous Learning Across Every Project.
Verifies all 10 criteria:
1. Experience generation from failures in Project A
2. Verified lesson availability
3. Cross-project lesson retrieval in Project B
4. Irrelevant lesson filtered out by applicability gate
5. Empirical strategy candidate evaluation
6. Harmful strategy rejection and rollback
7. Idempotent deduplication (events not learned twice)
8. Service outage resilience (no events lost during restart)
9. Strict project isolation & privacy protection
10. Queue metrics and measurable performance tracking
"""
import tempfile
import uuid
from pathlib import Path
import pytest

from backend.adapters.mock_adapter import DeterministicMockAdapter
from backend.intelligence.bus import KnowledgeExchangeBus
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
from backend.memory.semantic import SemanticMemoryStore
from backend.memory.strategy import StrategyMemoryStore
from backend.models.contracts import AgentType, ExecutionEvidence


@pytest.fixture
def temp_env():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        db_path = Path(f.name)

    queue = DurableEventQueue(db_path=db_path)
    episodic = EpisodicMemoryStore(db_path=db_path)
    semantic = SemanticMemoryStore(db_path=db_path)
    strategy = StrategyMemoryStore(db_path=db_path)
    bus = KnowledgeExchangeBus(max_budget_per_task=10)
    teacher = CrossAgentTeacher(bus, semantic)
    analyzer = MistakeAnalyzer(episodic, semantic)
    service = ContinuousLearningService(queue, episodic, semantic, teacher, analyzer)

    yield {
        "db_path": db_path,
        "queue": queue,
        "episodic": episodic,
        "semantic": semantic,
        "strategy": strategy,
        "bus": bus,
        "service": service,
    }

    db_path.unlink(missing_ok=True)


def test_1_failure_in_project_a_generates_recorded_experience(temp_env):
    """Criterion 1: A failure in Project A generates a recorded experience."""
    service: ContinuousLearningService = temp_env["service"]
    episodic: EpisodicMemoryStore = temp_env["episodic"]
    queue: DurableEventQueue = temp_env["queue"]

    ev = LearningEvent(
        event_id="ev_fail_1",
        idempotency_key="hash_fail_1",
        project_id="project_alpha",
        run_id="run_001",
        agent_type=AgentType.UNIT,
        event_type=LearningEventType.FAILURE_DETECTED,
        payload={
            "objective": "Test discount calculations",
            "evidence": {
                "command": "pytest",
                "exit_code": 1,
                "stdout": "",
                "stderr": "AssertionError: calculate_tax() assert 10 == 12",
                "duration_ms": 45.0,
            },
        },
    )
    queue.enqueue(ev)
    processed = service.process_all_pending()
    assert processed == 1

    records = episodic.query_experiences(project_id="project_alpha")
    assert len(records) == 1
    assert records[0].failure_category == FailureCategory.PRODUCT_DEFECT
    assert records[0].failure_origin == FailureOrigin.PRODUCT_CODE


def test_2_and_3_verified_lesson_available_and_retrieved_in_project_b(temp_env):
    """
    Criteria 2 & 3:
    Verified lesson becomes available and Project B retrieves it when applicable.
    """
    service: ContinuousLearningService = temp_env["service"]
    semantic: SemanticMemoryStore = temp_env["semantic"]
    queue: DurableEventQueue = temp_env["queue"]

    # 1. Enqueue verified repair from Project A
    repair_ev = LearningEvent(
        event_id="ev_rep_1",
        idempotency_key="hash_rep_1",
        project_id="project_alpha",
        run_id="run_001",
        agent_type=AgentType.REPAIR,
        event_type=LearningEventType.REPAIR_VERIFIED,
        payload={
            "diagnosis": "Boundary condition failure on negative numbers: add bounds check",
            "patch_diff": "--- a/billing.py\n+++ b/billing.py\n",
            "project_profile": {"primary_language": "python", "framework": "fastapi"},
        },
    )
    queue.enqueue(repair_ev)
    service.process_all_pending()

    # 2. Project B profiles its workspace (also Python / FastAPI)
    project_b_profile = ProjectProfile(
        project_id="project_beta",
        project_name="project_beta",
        root_path="/tmp/project_beta",
        primary_language="python",
        framework="fastapi",
    )

    # 3. Project B searches semantic memory
    results = semantic.search_lessons(
        "negative numbers boundary condition",
        target_agent=AgentType.UNIT,
        current_project_id=project_b_profile.project_id,
    )
    assert len(results) > 0
    top_lesson, score = results[0]
    assert top_lesson.scope == KnowledgeScope.GLOBAL_VERIFIED
    assert "Boundary condition" in top_lesson.problem_context

    # Applicability check
    is_app, reason = ProjectProfiler.check_applicability(top_lesson, project_b_profile)
    assert is_app is True


def test_4_irrelevant_lesson_is_not_blindly_applied(temp_env):
    """Criterion 4: An irrelevant lesson is not blindly applied."""
    # Lesson explicitly requires django framework
    django_lesson = SemanticLesson(
        lesson_id="les_django_1",
        source_agent=AgentType.INTEGRATION,
        title="Django transaction rollback test fixture",
        problem_context="Django TestCase database state isolation",
        recommendation="Use django.test.TransactionTestCase with serialized_rollback=True",
        applicable_conditions={"language": "python", "framework": "django"},
        scope=KnowledgeScope.GLOBAL_VERIFIED,
    )

    # Target Project is a FastAPI service
    fastapi_profile = ProjectProfile(
        project_id="project_fastapi",
        project_name="fastapi_service",
        root_path="/tmp/fastapi_service",
        primary_language="python",
        framework="fastapi",
    )

    is_app, reason = ProjectProfiler.check_applicability(django_lesson, fastapi_profile)
    assert is_app is False
    assert "Framework mismatch" in reason


def test_5_and_6_strategy_evaluation_promotion_and_rollback(temp_env):
    """
    Criteria 5 & 6:
    Candidate strategy evaluated before promotion; harmful strategy rejected or rolled back.
    """
    strategy_store: StrategyMemoryStore = temp_env["strategy"]
    evaluator = IndependentStrategyEvaluator(strategy_store=strategy_store)

    # 1. Baseline strategy v1
    baseline = StrategyRecord(
        strategy_id="strat_v1",
        agent_type=AgentType.UNIT,
        version="1.0.0",
        is_production=True,
        status=StrategyStatus.PRODUCTION,
        prompt_template="v1 prompt",
        system_instruction="v1 sys",
        benchmark_scores={"mutation_score": 0.70, "defect_detection": 0.65, "false_positive_rate": 0.02},
    )
    strategy_store.register_strategy(baseline)

    # 2. Promising candidate v2 (+10% mutation kill score)
    cand_good = StrategyRecord(
        strategy_id="strat_v2_good",
        agent_type=AgentType.UNIT,
        version="2.0.0",
        status=StrategyStatus.CANDIDATE,
        prompt_template="v2 prompt",
        system_instruction="v2 sys",
        benchmark_scores={"mutation_score": 0.80, "defect_detection": 0.75, "false_positive_rate": 0.02},
    )
    v_good = evaluator.compare_and_decide_promotion(cand_good, AgentType.UNIT)
    assert v_good.is_promoted is True
    assert strategy_store.get_production_strategy(AgentType.UNIT).version == "2.0.0"

    # 3. Harmful candidate v3 (high false positive rate 8%)
    cand_bad = StrategyRecord(
        strategy_id="strat_v3_bad",
        agent_type=AgentType.UNIT,
        version="3.0.0",
        status=StrategyStatus.CANDIDATE,
        prompt_template="v3 prompt",
        system_instruction="v3 sys",
        benchmark_scores={"mutation_score": 0.85, "defect_detection": 0.80, "false_positive_rate": 0.08},
    )
    v_bad = evaluator.compare_and_decide_promotion(cand_bad, AgentType.UNIT)
    assert v_bad.is_promoted is False
    assert v_bad.verdict == "REJECT_DEGRADATION"

    # 4. Rollback capability
    rolled_back = strategy_store.rollback(AgentType.UNIT)
    assert rolled_back.version == "1.0.0"
    assert strategy_store.get_production_strategy(AgentType.UNIT).version == "1.0.0"


def test_7_idempotent_deduplication_not_learned_twice(temp_env):
    """Criterion 7: Events are not learned twice because of retries / identical hash."""
    queue: DurableEventQueue = temp_env["queue"]
    service: ContinuousLearningService = temp_env["service"]

    event1 = LearningEvent(
        event_id="ev_dup_1",
        idempotency_key="unique_hash_xyz_123",
        project_id="proj_1",
        run_id="run_1",
        agent_type=AgentType.UNIT,
        event_type=LearningEventType.FAILURE_DETECTED,
        payload={"evidence": {"command": "pytest", "exit_code": 1, "stdout": "", "stderr": "error"}},
    )
    assert queue.enqueue(event1) is True

    # Attempt to re-enqueue identical event hash
    event1_retry = LearningEvent(
        event_id="ev_dup_1_retry",
        idempotency_key="unique_hash_xyz_123",  # SAME HASH
        project_id="proj_1",
        run_id="run_1",
        agent_type=AgentType.UNIT,
        event_type=LearningEventType.FAILURE_DETECTED,
        payload={"evidence": {"command": "pytest", "exit_code": 1, "stdout": "", "stderr": "error"}},
    )
    assert queue.enqueue(event1_retry) is False  # Safely deduplicated!

    # Queue should only have 1 event
    stats = queue.get_stats()
    assert stats["TOTAL"] == 1


def test_8_service_outage_resilience(temp_env):
    """Criterion 8: Temporary learning-service outage does not lose queued events."""
    db_path = temp_env["db_path"]
    queue1: DurableEventQueue = temp_env["queue"]

    # Enqueue 3 events while learning service is stopped
    for i in range(3):
        queue1.enqueue(
            LearningEvent(
                event_id=f"ev_outage_{i}",
                idempotency_key=f"hash_outage_{i}",
                project_id="proj_outage",
                run_id="run_outage",
                agent_type=AgentType.UNIT,
                event_type=LearningEventType.FAILURE_DETECTED,
                payload={"evidence": {"command": "pytest", "exit_code": 1, "stdout": "", "stderr": f"error {i}"}},
            )
        )

    # Simulate complete process crash / restart by closing and re-opening queue instance
    del queue1

    queue_recovered = DurableEventQueue(db_path=db_path)
    stats = queue_recovered.get_stats()
    assert stats["PENDING"] == 3  # Zero events lost!

    # Restart learning service and process
    service_recovered = ContinuousLearningService(
        queue=queue_recovered,
        episodic_store=temp_env["episodic"],
        semantic_store=temp_env["semantic"],
        cross_teacher=temp_env["service"].cross_teacher,
        mistake_analyzer=temp_env["service"].mistake_analyzer,
    )
    processed = service_recovered.process_all_pending()
    assert processed == 3
    assert queue_recovered.get_stats()["PROCESSED"] == 3


def test_9_project_isolation_prevents_unauthorized_sharing(temp_env):
    """
    Criterion 9: Project isolation and permissions prevent unauthorized knowledge sharing.
    Private project findings cannot be accessed by other projects.
    """
    semantic: SemanticMemoryStore = temp_env["semantic"]

    # Store private project-specific lesson in Project A
    private_lesson_a = SemanticLesson(
        lesson_id="les_private_a",
        source_agent=AgentType.INTEGRATION,
        title="Internal secret API endpoint schema",
        problem_context="Project A proprietary auth header schema x-secret-corp",
        recommendation="Use internal test token x-secret-corp-token",
        scope=KnowledgeScope.PROJECT_SPECIFIC,  # Strictly private!
        project_id="project_alpha",
    )
    semantic.store_lesson(private_lesson_a)

    # 1. Project A querying its own memory can see it
    results_a = semantic.search_lessons("secret API endpoint", current_project_id="project_alpha")
    assert any(l.lesson_id == "les_private_a" for l, s in results_a)

    # 2. Project B querying memory CANNOT see Project A's private lesson!
    results_b = semantic.search_lessons("secret API endpoint", current_project_id="project_beta")
    assert not any(l.lesson_id == "les_private_a" for l, s in results_b)


def test_10_queue_stats_and_metrics_monitoring(temp_env):
    """Criterion 10: Queue stats and performance tracking."""
    queue: DurableEventQueue = temp_env["queue"]
    service: ContinuousLearningService = temp_env["service"]

    ev = LearningEvent(
        event_id="ev_metric_1",
        idempotency_key="hash_metric_1",
        project_id="proj_1",
        run_id="run_1",
        agent_type=AgentType.UNIT,
        event_type=LearningEventType.FAILURE_DETECTED,
        payload={"evidence": {"command": "pytest", "exit_code": 1, "stdout": "", "stderr": "AssertionError"}},
    )
    queue.enqueue(ev)

    stats_before = queue.get_stats()
    assert stats_before["PENDING"] == 1

    service.process_all_pending()
    stats_after = queue.get_stats()
    assert stats_after["PENDING"] == 0
    assert stats_after["PROCESSED"] == 1
