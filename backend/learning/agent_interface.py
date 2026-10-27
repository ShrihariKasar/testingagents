"""Consistent Continuous Learning Interface for all Specialized Testing Agents.
Provides standard learning operations across Unit, Integration, Regression, Performance,
and End-to-End testing agents.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple

from backend.intelligence.bus import KnowledgeExchangeBus, KnowledgeMessage
from backend.intelligence.learning_evaluator import (
    IndependentStrategyEvaluator,
    StrategyComparisonVerdict,
)
from backend.learning.durable_queue import DurableEventQueue
from backend.learning.models import LearningEvent, LearningEventType
from backend.memory.episodic import EpisodicMemoryStore
from backend.memory.models import (
    ExperienceRecord,
    KnowledgeScope,
    LessonValidationStatus,
    SemanticLesson,
    StrategyRecord,
)
from backend.memory.semantic import SemanticMemoryStore
from backend.memory.strategy import StrategyMemoryStore
from backend.models.contracts import AgentType


class BaseContinuousLearningAgent:
    """
    Base class providing a consistent continuous learning interface for all testing agents.
    Every specialized agent inherits or implements these methods.
    """

    def __init__(
        self,
        agent_type: AgentType,
        episodic_store: Optional[EpisodicMemoryStore] = None,
        semantic_store: Optional[SemanticMemoryStore] = None,
        strategy_store: Optional[StrategyMemoryStore] = None,
        bus: Optional[KnowledgeExchangeBus] = None,
        durable_queue: Optional[DurableEventQueue] = None,
        evaluator: Optional[IndependentStrategyEvaluator] = None,
    ):
        self.agent_type = agent_type
        self.episodic = episodic_store or EpisodicMemoryStore()
        self.semantic = semantic_store or SemanticMemoryStore()
        self.strategy = strategy_store or StrategyMemoryStore()
        self.bus = bus or KnowledgeExchangeBus()
        self.durable_queue = durable_queue or DurableEventQueue()
        self.evaluator = evaluator or IndependentStrategyEvaluator(self.strategy)

    def attach_learning_context(
        self,
        episodic_store: EpisodicMemoryStore,
        semantic_store: SemanticMemoryStore,
        strategy_store: StrategyMemoryStore,
        bus: KnowledgeExchangeBus,
        durable_queue: DurableEventQueue,
        evaluator: Optional[IndependentStrategyEvaluator] = None,
    ):
        """Attaches shared learning engines when orchestrated centrally."""
        self.episodic = episodic_store
        self.semantic = semantic_store
        self.strategy = strategy_store
        self.bus = bus
        self.durable_queue = durable_queue
        self.evaluator = evaluator or IndependentStrategyEvaluator(self.strategy)

    def record_experience(self, experience: ExperienceRecord) -> ExperienceRecord:
        """Publishes and persists an empirical experience record to episodic memory."""
        experience.agent_type = self.agent_type
        saved_exp = self.episodic.record_experience(experience)

        # Enqueue durable learning event
        event_id = f"ev_{uuid.uuid4().hex[:8]}"
        idempotency_key = f"{experience.run_id}_{experience.experience_id}_{experience.task_type}"
        learning_ev = LearningEvent(
            event_id=event_id,
            idempotency_key=idempotency_key,
            project_id=experience.project_id,
            run_id=experience.run_id,
            agent_type=self.agent_type,
            event_type=LearningEventType.TEST_TASK_COMPLETED if experience.produced_measurable_improvement else LearningEventType.FAILURE_DETECTED,
            payload={
                "task_type": experience.task_type,
                "objective": experience.original_objective,
                "hypothesis": experience.root_cause_hypothesis,
            },
        )
        self.durable_queue.enqueue(learning_ev)
        return saved_exp

    def retrieve_relevant_knowledge(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
        min_confidence: float = 0.3,
        allow_candidate: bool = False,
    ) -> List[Tuple[SemanticLesson, float]]:
        """
        Retrieves applicable validated lessons before generating tests or diagnosing failures.
        Enforces tenant isolation and recipient role matching.
        """
        return self.semantic.search_lessons(
            query_text=query,
            target_agent=self.agent_type,
            current_project_id=project_id,
            min_confidence=min_confidence,
            top_k=top_k,
            allow_candidate=allow_candidate,
        )

    def propose_lesson(
        self,
        title: str,
        problem_context: str,
        recommendation: str,
        project_id: str,
        target_agents: Optional[List[AgentType]] = None,
        scope: KnowledgeScope = KnowledgeScope.CANDIDATE,
        confidence: float = 0.6,
        evidence_run_ids: Optional[List[str]] = None,
        applicable_conditions: Optional[Dict[str, Any]] = None,
    ) -> SemanticLesson:
        """Proposes a new candidate or project-scoped lesson based on verified observations."""
        lesson = SemanticLesson(
            lesson_id=f"les_{uuid.uuid4().hex[:8]}",
            source_agent=self.agent_type,
            target_agents=target_agents or [],
            title=title,
            problem_context=problem_context,
            recommendation=recommendation,
            confidence_score=confidence,
            validation_status=LessonValidationStatus.CANDIDATE,
            scope=scope,
            project_id=project_id,
            evidence_run_ids=evidence_run_ids or [],
            applicable_conditions=applicable_conditions or {},
        )
        return self.semantic.store_lesson(lesson)

    def validate_lesson(
        self,
        lesson_id: str,
        status: LessonValidationStatus,
        confidence_delta: float = 0.0,
    ) -> Optional[SemanticLesson]:
        """Validates or rejects a lesson after empirical verification."""
        updated = self.semantic.update_validation_status(
            lesson_id=lesson_id,
            new_status=status,
            confidence_delta=confidence_delta,
        )
        # Enqueue audit event
        ev_type = LearningEventType.LESSON_PROMOTED if status == LessonValidationStatus.VALIDATED else LearningEventType.LESSON_REJECTED
        self.durable_queue.enqueue(
            LearningEvent(
                event_id=f"ev_{uuid.uuid4().hex[:8]}",
                idempotency_key=f"val_{lesson_id}_{status.value}",
                project_id="system",
                run_id="validation",
                agent_type=self.agent_type,
                event_type=ev_type,
                payload={"lesson_id": lesson_id, "status": status.value},
            )
        )
        return updated

    def publish_knowledge(
        self,
        recipient: AgentType,
        topic: str,
        problem_context: str,
        recommendation: str,
        supporting_evidence: Optional[List[str]] = None,
        applicable_conditions: Optional[Dict[str, Any]] = None,
        task_key: str = "default",
    ) -> KnowledgeMessage:
        """Publishes evidence-backed recommendation to another specialized agent via Knowledge Bus."""
        msg = KnowledgeMessage(
            source_agent=self.agent_type,
            recipient_agent=recipient,
            topic=topic,
            problem_context=problem_context,
            proposed_recommendation=recommendation,
            supporting_evidence=supporting_evidence or [],
            applicable_conditions=applicable_conditions or {},
            confidence=0.8,
        )
        return self.bus.publish_message(msg, task_key=task_key)

    def request_agent_assistance(
        self,
        target_agent: AgentType,
        topic: str,
        problem_context: str,
        task_key: str = "default",
    ) -> List[KnowledgeMessage]:
        """
        Requests assistance or domain findings from another testing agent.
        Logs the knowledge request and inspects responses on the bus.
        """
        # Publish request message
        req_msg = KnowledgeMessage(
            source_agent=self.agent_type,
            recipient_agent=target_agent,
            topic=f"Assistance Request: {topic}",
            problem_context=problem_context,
            proposed_recommendation="Requesting relevant domain insights or contract details.",
            confidence=0.5,
        )
        self.bus.publish_message(req_msg, task_key=task_key)

        # Durable audit event
        self.durable_queue.enqueue(
            LearningEvent(
                event_id=f"ev_{uuid.uuid4().hex[:8]}",
                idempotency_key=f"req_{req_msg.message_id}",
                project_id="system",
                run_id=task_key,
                agent_type=self.agent_type,
                event_type=LearningEventType.KNOWLEDGE_REQUESTED,
                payload={"target_agent": target_agent.value, "topic": topic},
            )
        )

        # Retrieve any incoming messages targeted to this agent
        return self.bus.get_messages_for_agent(self.agent_type)

    def record_lesson_outcome(
        self,
        lesson_id: str,
        helpful: bool,
    ) -> Optional[SemanticLesson]:
        """Records empirical outcome of applying a lesson (rewarding helpful lessons, penalizing harmful ones)."""
        return self.semantic.record_application_outcome(lesson_id, is_success=helpful)

    def evaluate_strategy(
        self,
        candidate_strategy: StrategyRecord,
    ) -> StrategyComparisonVerdict:
        """Evaluates a candidate strategy version against baseline using held-out benchmarks."""
        candidate_strategy.agent_type = self.agent_type
        return self.evaluator.compare_and_decide_promotion(candidate_strategy, self.agent_type)
