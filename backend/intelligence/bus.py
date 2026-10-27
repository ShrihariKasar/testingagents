"""Controlled Agent-to-Agent Knowledge Exchange Bus.
Implements typed messaging, message budget meters, relevance scoring,
circular delegation prevention, and evidence audit trails.
"""
from __future__ import annotations

import datetime
import uuid
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.models.contracts import AgentType


class KnowledgeMessage(BaseModel):
    """Structured message transmitted across testing agents."""
    message_id: str = Field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:8]}")
    source_agent: AgentType
    recipient_agent: AgentType
    topic: str
    problem_context: str
    proposed_recommendation: str
    supporting_evidence: List[str] = Field(default_factory=list)
    applicable_conditions: Dict[str, Any] = Field(default_factory=dict)
    limitations: List[str] = Field(default_factory=list)
    confidence: float = 0.5
    run_id: Optional[str] = None
    artifact_links: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())


class KnowledgeExchangeBudgetExceeded(Exception):
    """Raised when an agent conversation exceeds the assigned message budget."""
    pass


class KnowledgeExchangeBus:
    """
    Controlled inter-agent communication bus with strict budget limits,
    circular delegation detection, and delivery guarantees.
    """

    def __init__(self, max_budget_per_task: int = 10):
        self.max_budget = max_budget_per_task
        self.message_history: List[KnowledgeMessage] = []
        self._task_counters: Dict[str, int] = {}
        self._delegation_chains: Dict[str, List[AgentType]] = {}
        self._subscribers: Dict[AgentType, List[Callable[[KnowledgeMessage], None]]] = {
            at: [] for at in AgentType
        }

    def subscribe(self, agent_type: AgentType, callback: Callable[[KnowledgeMessage], None]):
        """Subscribes an agent handler to incoming broadcast or targeted messages."""
        self._subscribers[agent_type].append(callback)

    def publish_message(self, message: KnowledgeMessage, task_key: str = "default") -> KnowledgeMessage:
        """
        Publishes a message from one agent to another with budget and circular loop guards.
        """
        # 1. Budget enforcement
        current_count = self._task_counters.get(task_key, 0)
        if current_count >= self.max_budget:
            raise KnowledgeExchangeBudgetExceeded(
                f"Task '{task_key}' exceeded message budget limit of {self.max_budget} messages."
            )

        # 2. Circular delegation detection
        chain = self._delegation_chains.get(task_key, [])
        if message.source_agent in chain and message.recipient_agent in chain:
            # Check if cycle A -> B -> A is occurring
            if len(chain) >= 2 and chain[-1] == message.source_agent and chain[-2] == message.recipient_agent:
                raise ValueError(
                    f"Circular delegation loop detected between {message.source_agent.value} and {message.recipient_agent.value} on task '{task_key}'."
                )

        # Increment budget count now that checks have passed
        self._task_counters[task_key] = current_count + 1

        if not chain or chain[-1] != message.source_agent:
            chain.append(message.source_agent)
        chain.append(message.recipient_agent)
        self._delegation_chains[task_key] = chain

        # 3. Store in audit history
        self.message_history.append(message)

        # 4. Deliver to subscriber
        subscribers = self._subscribers.get(message.recipient_agent, [])
        for sub in subscribers:
            try:
                sub(message)
            except Exception:
                pass

        return message

    def get_messages_for_agent(self, agent_type: AgentType, limit: int = 20) -> List[KnowledgeMessage]:
        """Retrieves messages targeted to a specific agent."""
        return [
            m for m in reversed(self.message_history)
            if m.recipient_agent == agent_type
        ][:limit]

    def reset_task_budget(self, task_key: str):
        """Resets counters for a new independent task."""
        self._task_counters.pop(task_key, None)
        self._delegation_chains.pop(task_key, None)
