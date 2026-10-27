"""Cross-Agent Teaching Engine.
Routes validated lessons from source agents to appropriate target agents,
assesses relevance on the receiving end, and tracks adoption and performance impact.
"""
from __future__ import annotations

from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from backend.intelligence.bus import KnowledgeExchangeBus, KnowledgeMessage
from backend.memory.models import SemanticLesson
from backend.memory.semantic import SemanticMemoryStore
from backend.models.contracts import AgentType

# Standard cross-agent knowledge distribution matrix
CROSS_AGENT_ROUTING_MAP: Dict[AgentType, List[AgentType]] = {
    AgentType.UNIT: [AgentType.INTEGRATION],          # Boundary condition strategies
    AgentType.INTEGRATION: [AgentType.E2E],           # Service dependency failures & contracts
    AgentType.E2E: [AgentType.REGRESSION],            # Critical user journeys
    AgentType.PERFORMANCE: [AgentType.INTEGRATION],   # Validated bottleneck findings
    AgentType.REGRESSION: [                           # Change-impact findings to all
        AgentType.UNIT,
        AgentType.INTEGRATION,
        AgentType.PERFORMANCE,
        AgentType.E2E,
    ],
}


class LessonAdoptionDecision(BaseModel):
    recipient_agent: AgentType
    lesson_id: str
    adopted: bool
    relevance_score: float
    reason: str


class CrossAgentTeacher:
    """Coordinates cross-agent lesson dissemination and recipient relevance validation."""

    def __init__(self, bus: KnowledgeExchangeBus, semantic_store: SemanticMemoryStore):
        self.bus = bus
        self.semantic = semantic_store
        self.adoption_history: List[LessonAdoptionDecision] = []

    def assess_recipient_relevance(
        self,
        lesson: SemanticLesson,
        recipient_agent: AgentType,
    ) -> tuple[bool, float, str]:
        """
        Receiving agent evaluates relevance before adopting a shared lesson.
        Does not adopt unconditionally.
        """
        # 1. Check if recipient is a targeted consumer
        default_targets = CROSS_AGENT_ROUTING_MAP.get(lesson.source_agent, [])
        is_targeted = (recipient_agent in lesson.target_agents) or (recipient_agent in default_targets)

        if not is_targeted:
            return False, 0.1, f"Recipient {recipient_agent.value} is outside target routing map of {lesson.source_agent.value}"

        # 2. Check confidence score
        if lesson.confidence_score < 0.4:
            return False, lesson.confidence_score, f"Lesson confidence ({lesson.confidence_score}) below adoption threshold (0.4)"

        # 3. Adopted!
        return True, lesson.confidence_score, "Lesson relevant and meets confidence threshold."

    def share_lesson_across_agents(
        self,
        lesson: SemanticLesson,
        task_key: str = "cross_teach",
    ) -> List[LessonAdoptionDecision]:
        """
        Broadcasts a validated lesson to compatible agents according to the distribution matrix.
        """
        target_recipients = lesson.target_agents or CROSS_AGENT_ROUTING_MAP.get(lesson.source_agent, [])
        decisions: List[LessonAdoptionDecision] = []

        for recipient in target_recipients:
            if recipient == lesson.source_agent:
                continue

            adopted, score, reason = self.assess_recipient_relevance(lesson, recipient)
            decision = LessonAdoptionDecision(
                recipient_agent=recipient,
                lesson_id=lesson.lesson_id,
                adopted=adopted,
                relevance_score=score,
                reason=reason,
            )
            decisions.append(decision)
            self.adoption_history.append(decision)

            if adopted:
                # Transmit structured message through Knowledge Bus
                msg = KnowledgeMessage(
                    source_agent=lesson.source_agent,
                    recipient_agent=recipient,
                    topic=lesson.title,
                    problem_context=lesson.problem_context,
                    proposed_recommendation=lesson.recommendation,
                    supporting_evidence=lesson.supporting_evidence,
                    applicable_conditions=lesson.applicable_conditions,
                    limitations=lesson.limitations,
                    confidence=lesson.confidence_score,
                )
                self.bus.publish_message(msg, task_key=task_key)

        return decisions
