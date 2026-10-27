"""Tests for Three-Tier Shared Memory: Episodic, Semantic, and Strategy Memory."""
import tempfile
from pathlib import Path

from backend.memory.models import (
    ExperienceRecord,
    FailureCategory,
    FailureOrigin,
    LessonValidationStatus,
    SemanticLesson,
    StrategyRecord,
    StrategyStatus,
)
from backend.memory.episodic import EpisodicMemoryStore
from backend.memory.semantic import SemanticMemoryStore, compute_text_embedding, cosine_similarity
from backend.memory.strategy import StrategyMemoryStore
from backend.models.contracts import AgentType, PatchVerificationStatus


def test_episodic_experience_capture_and_query():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        db_path = Path(f.name)

    store = EpisodicMemoryStore(db_path=db_path)

    # 1. Record an unconfirmed hypothesis
    exp1 = ExperienceRecord(
        experience_id="exp_001",
        project_id="billing_proj",
        run_id="run_100",
        agent_type=AgentType.UNIT,
        task_type="unit_test_synthesis",
        original_objective="Cover negative price branch",
        failure_category=FailureCategory.PRODUCT_DEFECT,
        failure_origin=FailureOrigin.PRODUCT_CODE,
        root_cause_hypothesis="Missing bounds check on negative input",
        confirmed_root_cause=None,
        is_root_cause_confirmed=False,
        produced_measurable_improvement=False,
    )
    store.record_experience(exp1)

    # 2. Record a confirmed repair outcome
    exp2 = ExperienceRecord(
        experience_id="exp_002",
        project_id="billing_proj",
        run_id="run_101",
        agent_type=AgentType.REPAIR,
        task_type="repair",
        original_objective="Fix tier bonus off-by-one",
        failure_category=FailureCategory.PRODUCT_DEFECT,
        failure_origin=FailureOrigin.PRODUCT_CODE,
        root_cause_hypothesis="Boundary operator > instead of >=",
        confirmed_root_cause="Boundary operator > instead of >= at line 2",
        is_root_cause_confirmed=True,
        verification_result=PatchVerificationStatus.VERIFIED,
        produced_measurable_improvement=True,
    )
    store.record_experience(exp2)

    # 3. Query experiences
    all_exps = store.query_experiences(project_id="billing_proj")
    assert len(all_exps) == 2

    # Query only confirmed improvements
    improved = store.query_experiences(project_id="billing_proj", only_improvements=True)
    assert len(improved) == 1
    assert improved[0].is_root_cause_confirmed is True
    assert improved[0].confirmed_root_cause is not None

    # Recurring patterns
    patterns = store.get_recurring_failure_patterns(min_count=2)
    assert len(patterns) == 1
    assert patterns[0]["failure_category"] == FailureCategory.PRODUCT_DEFECT.value
    assert patterns[0]["frequency"] == 2

    db_path.unlink(missing_ok=True)


def test_semantic_memory_vector_search_and_validation():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        db_path = Path(f.name)

    store = SemanticMemoryStore(db_path=db_path)

    lesson1 = SemanticLesson(
        lesson_id="les_001",
        source_agent=AgentType.UNIT,
        target_agents=[AgentType.INTEGRATION, AgentType.UNIT],
        title="Zero and negative subtotal edge boundaries",
        problem_context="Billing calculations fail when customer order total is 0.0 or negative currency amount",
        recommendation="Always seed boundary fixtures with 0.0 and -1.0 to assert ValueError",
        confidence_score=0.8,
        validation_status=LessonValidationStatus.VALIDATED,
    )
    store.store_lesson(lesson1)

    lesson2 = SemanticLesson(
        lesson_id="les_002",
        source_agent=AgentType.PERFORMANCE,
        target_agents=[AgentType.INTEGRATION],
        title="Database connection pool exhaustion under load",
        problem_context="Concurrent API requests hang when database session fixtures do not call close()",
        recommendation="Use context-managed session fixtures with explicit rollback teardown",
        confidence_score=0.9,
        validation_status=LessonValidationStatus.VALIDATED,
    )
    store.store_lesson(lesson2)

    # Search with semantic query about negative numbers in billing
    results = store.search_lessons("test negative currency amount in order total", target_agent=AgentType.INTEGRATION)
    assert len(results) > 0
    top_lesson, score = results[0]
    assert top_lesson.lesson_id == "les_001"
    assert "Zero and negative" in top_lesson.title
    assert score > 0.3

    # Test application outcome tracking
    updated = store.record_application_outcome("les_001", is_success=True)
    assert updated.times_applied == 1
    assert updated.times_successful == 1
    assert updated.confidence_score > 0.8

    db_path.unlink(missing_ok=True)


def test_strategy_memory_versioning_and_rollback():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        db_path = Path(f.name)

    store = StrategyMemoryStore(db_path=db_path)

    # 1. Register initial production strategy v1.0.0
    strat_v1 = StrategyRecord(
        strategy_id="strat_unit_v1",
        agent_type=AgentType.UNIT,
        version="1.0.0",
        is_production=True,
        status=StrategyStatus.PRODUCTION,
        prompt_template="Standard unit test template v1",
        system_instruction="Generate tests",
        benchmark_scores={"mutation_score": 0.72, "defect_detection": 0.65},
    )
    store.register_strategy(strat_v1)

    current = store.get_production_strategy(AgentType.UNIT)
    assert current is not None
    assert current.version == "1.0.0"

    # 2. Register candidate strategy v1.1.0
    strat_v2 = StrategyRecord(
        strategy_id="strat_unit_v2",
        agent_type=AgentType.UNIT,
        version="1.1.0",
        is_production=False,
        status=StrategyStatus.CANDIDATE,
        prompt_template="Enhanced boundary and mock template v2",
        system_instruction="Generate edge case tests",
    )
    store.register_strategy(strat_v2)

    # 3. Promote candidate to production
    promoted = store.promote_to_production("strat_unit_v2", benchmark_scores={"mutation_score": 0.84, "defect_detection": 0.80})
    assert promoted.is_production is True
    assert promoted.version == "1.1.0"
    assert promoted.rollback_target_version == "1.0.0"

    # Verify active prod is now v1.1.0
    active = store.get_production_strategy(AgentType.UNIT)
    assert active.version == "1.1.0"

    # 4. Trigger automated rollback if performance regresses
    rolled_back = store.rollback(AgentType.UNIT)
    assert rolled_back is not None
    assert rolled_back.version == "1.0.0"
    assert rolled_back.is_production is True

    db_path.unlink(missing_ok=True)
