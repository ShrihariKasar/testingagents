"""Three-Tier Memory Models: Episodic, Semantic, and Strategy Memory Contracts."""
from __future__ import annotations

import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.models.contracts import (
    AgentType,
    ExecutionEvidence,
    PatchVerificationStatus,
    RiskLevel,
    TestStatus,
)


class FailureCategory(str, Enum):
    PRODUCT_DEFECT = "product_defect"
    TEST_DESIGN_FLAW = "test_design_flaw"
    FLAKY_TEST = "flaky_test"
    ENVIRONMENT_FAILURE = "environment_failure"
    ENVIRONMENT_OR_CONFIG = "environment_or_config"
    FLAKY_ENVIRONMENT = "flaky_environment"
    REGRESSION_DEFECT = "regression_defect"
    ORCHESTRATION_TIMEOUT = "orchestration_timeout"
    TOOL_EXECUTION_ERROR = "tool_execution_error"
    INCORRECT_EXPECTATION = "incorrect_expectation"


class FailureOrigin(str, Enum):
    PRODUCT_CODE = "product_code"
    TEST_CODE = "test_code"
    TEST_FRAMEWORK = "test_framework"
    ORCHESTRATOR = "orchestrator"
    ENVIRONMENT = "environment"
    TOOL_LAYER = "tool_layer"
    SPECIFICATION = "specification"


class KnowledgeScope(str, Enum):
    GLOBAL_VERIFIED = "global_verified"      # Reusable testing patterns sanitized of private code
    PROJECT_SPECIFIC = "project_specific"    # Private architecture & failure info (tenant isolated)
    CANDIDATE = "candidate"                  # New observations awaiting empirical validation


class LessonValidationStatus(str, Enum):
    UNVALIDATED = "unvalidated"
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    REJECTED = "rejected"
    PROMOTED = "promoted"


class StrategyStatus(str, Enum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    EVALUATING = "evaluating"
    PRODUCTION = "production"
    DEPRECATED = "deprecated"
    ROLLED_BACK = "rolled_back"


# ==============================================================================
# Tier 1: Episodic Memory (Raw Experiences & Interventions)
# ==============================================================================

class ExperienceRecord(BaseModel):
    """
    Immutable historical testing experience.
    Strictly distinguishes confirmed facts from unverified hypotheses.
    """
    experience_id: str
    project_id: str
    run_id: str
    agent_type: AgentType
    task_type: str  # e.g., "unit_test_synthesis", "api_boundary_check", "regression_impact", "repair"
    original_objective: str
    execution_context: Dict[str, Any] = Field(default_factory=dict)
    strategy_id: str = "default_v1"
    strategy_version: str = "1.0.0"
    prompt_version: str = "v1"
    tools_used: List[str] = Field(default_factory=list)
    generated_tests: List[str] = Field(default_factory=list)
    execution_evidence: Optional[ExecutionEvidence] = None
    expected_outcome: str = "pass"
    actual_outcome: str = "pass"
    failure_category: Optional[FailureCategory] = None
    failure_origin: Optional[FailureOrigin] = None
    root_cause_hypothesis: Optional[str] = None
    confirmed_root_cause: Optional[str] = None
    is_root_cause_confirmed: bool = False
    intervention_attempted: Optional[str] = None
    verification_result: Optional[PatchVerificationStatus] = None
    duration_ms: float = 0.0
    token_usage: int = 0
    estimated_cost_usd: float = 0.0
    produced_measurable_improvement: bool = False
    created_at: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())


# ==============================================================================
# Tier 2: Semantic Memory (Reusable Lessons & Patterns)
# ==============================================================================

class SemanticLesson(BaseModel):
    """
    Reusable, validated lesson distilled from verified episodic experiences.
    Includes vector embeddings for semantic similarity retrieval and strict tenant scoping.
    """
    lesson_id: str
    source_agent: AgentType
    target_agents: List[AgentType] = Field(default_factory=list)
    title: str
    problem_context: str
    recommendation: str
    supporting_evidence: List[str] = Field(default_factory=list)
    applicable_conditions: Dict[str, Any] = Field(default_factory=dict)
    limitations: List[str] = Field(default_factory=list)
    confidence_score: float = 0.5  # 0.0 to 1.0
    validation_status: LessonValidationStatus = LessonValidationStatus.UNVALIDATED
    scope: KnowledgeScope = KnowledgeScope.GLOBAL_VERIFIED
    project_id: Optional[str] = None  # Bound if scope is PROJECT_SPECIFIC
    evidence_run_ids: List[str] = Field(default_factory=list)
    times_applied: int = 0
    times_successful: int = 0
    embedding: Optional[List[float]] = None  # Cosine vector
    provenance: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())


# ==============================================================================
# Tier 3: Strategy Memory (Versioned Prompt & Policy Registry)
# ==============================================================================

class StrategyRecord(BaseModel):
    """
    Versioned testing strategy, prompt configuration, and tool-selection policy.
    Subject to strict promotion gates and automated rollback pointers.
    """
    strategy_id: str
    agent_type: AgentType
    version: str
    is_production: bool = False
    status: StrategyStatus = StrategyStatus.DRAFT
    prompt_template: str = "Standard prompt template"
    system_instruction: str = "Standard system instructions"
    tool_selection_policy: Dict[str, Any] = Field(default_factory=dict)
    applicability_rules: Dict[str, Any] = Field(default_factory=dict)
    benchmark_scores: Dict[str, float] = Field(default_factory=dict)
    # Mutation score, defect detection, false positive rate, latency_ms, cost_usd
    rollback_target_version: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    promoted_at: Optional[str] = None
