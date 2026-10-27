"""Tests for Collaborative Agent Intelligence: Bus, Mistake Analysis, Cross Teaching, and Evaluator."""
import tempfile
from pathlib import Path
import pytest

from backend.intelligence.bus import (
    KnowledgeExchangeBus,
    KnowledgeMessage,
    KnowledgeExchangeBudgetExceeded,
)
from backend.intelligence.mistake_analyzer import MistakeAnalyzer
from backend.intelligence.cross_teaching import CrossAgentTeacher
from backend.intelligence.learning_evaluator import IndependentStrategyEvaluator
from backend.memory.episodic import EpisodicMemoryStore
from backend.memory.semantic import SemanticMemoryStore
from backend.memory.strategy import StrategyMemoryStore
from backend.memory.models import (
    FailureCategory,
    FailureOrigin,
    LessonValidationStatus,
    SemanticLesson,
    StrategyRecord,
    StrategyStatus,
)
from backend.models.contracts import AgentType, ExecutionEvidence


def test_knowledge_exchange_bus_budget_and_circular_guard():
    bus = KnowledgeExchangeBus(max_budget_per_task=3)

    received_messages = []
    bus.subscribe(AgentType.INTEGRATION, lambda m: received_messages.append(m))

    msg1 = KnowledgeMessage(
        source_agent=AgentType.UNIT,
        recipient_agent=AgentType.INTEGRATION,
        topic="Boundary checks",
        problem_context="Negative inputs fail",
        proposed_recommendation="Assert 400 status on negative values",
    )
    bus.publish_message(msg1, task_key="task_1")
    assert len(received_messages) == 1

    msg2 = KnowledgeMessage(
        source_agent=AgentType.INTEGRATION,
        recipient_agent=AgentType.E2E,
        topic="Route validation",
        problem_context="Checkout route requires auth",
        proposed_recommendation="Ensure bearer token in header",
    )
    bus.publish_message(msg2, task_key="task_1")

    # 1. Circular loop detection (INTEGRATION -> E2E -> INTEGRATION)
    msg_circular = KnowledgeMessage(
        source_agent=AgentType.E2E,
        recipient_agent=AgentType.INTEGRATION,
        topic="Loop check",
        problem_context="Loop context",
        proposed_recommendation="Loop rec",
    )
    with pytest.raises(ValueError, match="Circular delegation loop detected"):
        bus.publish_message(msg_circular, task_key="task_1")

    # 2. Budget limit enforcement
    msg3 = KnowledgeMessage(
        source_agent=AgentType.PERFORMANCE,
        recipient_agent=AgentType.INTEGRATION,
        topic="DB pool",
        problem_context="Pool size",
        proposed_recommendation="Increase pool",
    )
    bus.publish_message(msg3, task_key="task_1")  # message 3 (reaches budget of 3)

    msg4 = KnowledgeMessage(
        source_agent=AgentType.REGRESSION,
        recipient_agent=AgentType.UNIT,
        topic="Diff impact",
        problem_context="Impact",
        proposed_recommendation="Test billing",
    )
    with pytest.raises(KnowledgeExchangeBudgetExceeded, match="exceeded message budget limit"):
        bus.publish_message(msg4, task_key="task_1")


def test_mistake_analyzer_classification():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        db_path = Path(f.name)

    episodic = EpisodicMemoryStore(db_path=db_path)
    semantic = SemanticMemoryStore(db_path=db_path)
    analyzer = MistakeAnalyzer(episodic_store=episodic, semantic_store=semantic)

    # 1. Product defect evidence
    ev_product = ExecutionEvidence(
        command="pytest",
        exit_code=1,
        stdout="",
        stderr="AssertionError: assert calculate_tier_bonus(100) == 50\n + where 10 == 50",
        duration_ms=120.0,
    )
    cat1, orig1 = analyzer.classify_failure_origin(ev_product)
    assert cat1 == FailureCategory.PRODUCT_DEFECT
    assert orig1 == FailureOrigin.PRODUCT_CODE

    # 2. Timeout / Orchestration failure
    ev_timeout = ExecutionEvidence(
        command="pytest",
        exit_code=124,
        stdout="",
        stderr="Execution timed out after 45.0 seconds.",
        duration_ms=45000.0,
    )
    cat2, orig2 = analyzer.classify_failure_origin(ev_timeout)
    assert cat2 == FailureCategory.ORCHESTRATION_TIMEOUT
    assert orig2 == FailureOrigin.ORCHESTRATOR

    # 3. Flaky / Socket connection reset
    ev_flaky = ExecutionEvidence(
        command="pytest",
        exit_code=1,
        stdout="",
        stderr="ConnectionResetError: [Errno 104] Connection reset by peer",
        duration_ms=50.0,
    )
    cat3, orig3 = analyzer.classify_failure_origin(ev_flaky)
    assert cat3 == FailureCategory.FLAKY_TEST

    db_path.unlink(missing_ok=True)


def test_cross_agent_teaching_relevance():
    bus = KnowledgeExchangeBus(max_budget_per_task=10)
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        db_path = Path(f.name)

    semantic = SemanticMemoryStore(db_path=db_path)
    teacher = CrossAgentTeacher(bus=bus, semantic_store=semantic)

    # Unit agent discovers boundary strategy
    lesson = SemanticLesson(
        lesson_id="les_cross_1",
        source_agent=AgentType.UNIT,
        target_agents=[AgentType.INTEGRATION],
        title="Negative parameter contract",
        problem_context="Negative subtotal crashes calculate_tax_and_discount",
        recommendation="Assert 400 Bad Request on negative values",
        confidence_score=0.85,
        validation_status=LessonValidationStatus.VALIDATED,
    )

    decisions = teacher.share_lesson_across_agents(lesson)
    assert len(decisions) == 1
    d = decisions[0]
    assert d.recipient_agent == AgentType.INTEGRATION
    assert d.adopted is True
    assert d.relevance_score == 0.85

    # Check that knowledge bus recorded the message
    messages = bus.get_messages_for_agent(AgentType.INTEGRATION)
    assert len(messages) == 1
    assert messages[0].topic == "Negative parameter contract"

    db_path.unlink(missing_ok=True)


def test_independent_strategy_evaluator():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        db_path = Path(f.name)

    strategy_store = StrategyMemoryStore(db_path=db_path)
    evaluator = IndependentStrategyEvaluator(strategy_store=strategy_store)

    # 1. Baseline strategy v1.0
    baseline = StrategyRecord(
        strategy_id="strat_v1",
        agent_type=AgentType.UNIT,
        version="1.0.0",
        is_production=True,
        status=StrategyStatus.PRODUCTION,
        prompt_template="v1 prompt",
        system_instruction="v1 sys",
        benchmark_scores={
            "mutation_score": 0.70,
            "defect_detection": 0.65,
            "false_positive_rate": 0.02,
            "latency_ms": 1000.0,
            "cost_usd": 0.001,
        },
    )
    strategy_store.register_strategy(baseline)

    # 2. Superior candidate strategy v2.0 (+15% mutation kill score, +15% defect detection)
    candidate_superior = StrategyRecord(
        strategy_id="strat_v2_superior",
        agent_type=AgentType.UNIT,
        version="2.0.0",
        is_production=False,
        status=StrategyStatus.CANDIDATE,
        prompt_template="v2 prompt with parameterized tests",
        system_instruction="v2 sys",
        benchmark_scores={
            "mutation_score": 0.85,
            "defect_detection": 0.80,
            "false_positive_rate": 0.02,
            "latency_ms": 1100.0,
            "cost_usd": 0.0015,
        },
    )

    verdict_sup = evaluator.compare_and_decide_promotion(candidate_superior, AgentType.UNIT)
    assert verdict_sup.is_promoted is True
    assert verdict_sup.verdict == "PROMOTE_TO_PRODUCTION"
    assert verdict_sup.mutation_score_delta == 0.15

    # Active production should now be v2.0.0
    prod_active = strategy_store.get_production_strategy(AgentType.UNIT)
    assert prod_active.version == "2.0.0"
    assert prod_active.rollback_target_version == "1.0.0"

    # 3. Flawed candidate strategy v3.0 (high false positive rate of 12%)
    candidate_flawed = StrategyRecord(
        strategy_id="strat_v3_flawed",
        agent_type=AgentType.UNIT,
        version="3.0.0",
        is_production=False,
        status=StrategyStatus.CANDIDATE,
        prompt_template="v3 flawed prompt",
        system_instruction="v3 sys",
        benchmark_scores={
            "mutation_score": 0.90,
            "defect_detection": 0.88,
            "false_positive_rate": 0.12,  # EXCEEDS 5%
        },
    )

    verdict_flawed = evaluator.compare_and_decide_promotion(candidate_flawed, AgentType.UNIT)
    assert verdict_flawed.is_promoted is False
    assert verdict_flawed.verdict == "REJECT_DEGRADATION"
    assert "False positive rate" in verdict_flawed.explanation

    # Active production remains v2.0.0
    assert strategy_store.get_production_strategy(AgentType.UNIT).version == "2.0.0"

    db_path.unlink(missing_ok=True)
