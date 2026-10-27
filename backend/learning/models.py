"""Contracts and Data Models for Continuous Learning and Project Fingerprinting."""
from __future__ import annotations

import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.models.contracts import AgentType
from backend.memory.models import KnowledgeScope


class LearningEventType(str, Enum):
    # Lifecycle & project events
    PROJECT_REGISTERED = "project_registered"
    PROJECT_UPDATED = "project_updated"
    TEST_PLAN_CREATED = "test_plan_created"

    # Agent execution events
    AGENT_TASK_STARTED = "agent_task_started"
    AGENT_TASK_COMPLETED = "agent_task_completed"
    TEST_GENERATED = "test_generated"
    TEST_PASSED = "test_passed"
    TEST_FAILED = "test_failed"
    TEST_TASK_COMPLETED = "test_task_completed"
    FAILURE_DETECTED = "failure_detected"

    # Defect & assertion quality events
    DEFECT_DETECTED = "defect_detected"
    DEFECT_MISSED = "defect_missed"
    ASSERTION_INEFFECTIVE = "assertion_ineffective"
    FLAKY_DETECTED = "flaky_detected"

    # Agent-specific domain events
    REGRESSION_INTRODUCED = "regression_introduced"
    INTEGRATION_CONTRACT_VIOLATED = "integration_contract_violated"
    PERFORMANCE_THRESHOLD_EXCEEDED = "performance_threshold_exceeded"
    E2E_WORKFLOW_FAILED = "e2e_workflow_failed"

    # Root cause & repair events
    ROOT_CAUSE_CONFIRMED = "root_cause_confirmed"
    REPAIR_PROPOSED = "repair_proposed"
    REPAIR_APPLIED = "repair_applied"
    REPAIR_VERIFIED = "repair_verified"
    REPAIR_FAILED = "repair_failed"

    # Strategy & lesson lifecycle events
    STRATEGY_EVALUATION_COMPLETED = "strategy_evaluation_completed"
    LESSON_PROMOTED = "lesson_promoted"
    LESSON_REJECTED = "lesson_rejected"
    KNOWLEDGE_REQUESTED = "knowledge_requested"
    RUN_INTERRUPTED = "run_interrupted"


class ProjectProfile(BaseModel):
    """
    Fingerprint of a software project architecture, environment, and dependencies.
    Used by applicability gates to prevent blind cross-project strategy application.
    """
    project_id: str
    project_name: str
    root_path: str
    primary_language: str = "python"
    framework: str = "generic"            # e.g., "fastapi", "flask", "django", "express", "react"
    test_runner: str = "pytest"          # e.g., "pytest", "unittest", "playwright", "jest"
    database_layer: Optional[str] = None # e.g., "sqlalchemy", "sqlite", "postgresql"
    tags: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())


class LearningEvent(BaseModel):
    """
    Durable, idempotent event emitted by test execution for background learning processing.
    """
    event_id: str
    idempotency_key: str  # Unique hash to guarantee events are never processed twice
    project_id: str
    run_id: str
    agent_type: AgentType
    event_type: LearningEventType
    payload: Dict[str, Any] = Field(default_factory=dict)
    priority: int = 5     # 1 (low) to 10 (high)
    retry_count: int = 0
    status: str = "PENDING"  # PENDING, PROCESSING, PROCESSED, FAILED
    error_message: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    processed_at: Optional[str] = None
