"""Data Models and Contracts for Autonomous Project Completion and Self-Repair Engine.
Enforces typed schemas for project completion checklists, structured defect findings,
closed-loop repair iterations, and human safety approval gates.
"""
from __future__ import annotations

import datetime
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.models.contracts import AgentType, PatchVerificationStatus, RiskLevel, TestStatus


class CompletionWorkflowStatus(str, Enum):
    ANALYZING = "ANALYZING"
    PLANNING = "PLANNING"
    TESTING = "TESTING"
    REPAIRING = "REPAIRING"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    INCOMPLETE = "INCOMPLETE"


class ChecklistCategory(str, Enum):
    BUILD = "build"
    UNIT_TESTS = "unit_tests"
    INTEGRATION_TESTS = "integration_tests"
    REGRESSION_TESTS = "regression_tests"
    E2E_WORKFLOWS = "e2e_workflows"
    PERFORMANCE_CRITERIA = "performance_criteria"
    SECURITY_AUDIT = "security_audit"
    ACCEPTANCE_CRITERIA = "acceptance_criteria"
    HUMAN_APPROVAL = "human_approval"


class ChecklistItemStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


class ChecklistItem(BaseModel):
    item_id: str = Field(default_factory=lambda: f"chk_{uuid.uuid4().hex[:8]}")
    category: ChecklistCategory
    title: str
    description: str
    required: bool = True
    status: ChecklistItemStatus = ChecklistItemStatus.PENDING
    evidence_summary: Optional[str] = None
    artifact_path: Optional[str] = None
    updated_at: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())


class ProjectCompletionChecklist(BaseModel):
    items: List[ChecklistItem] = Field(default_factory=list)
    completion_percentage: float = 0.0


class DefectSeverity(str, Enum):
    BLOCKER = "BLOCKER"
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"
    LOW = "LOW"


class DefectStatus(str, Enum):
    OPEN = "OPEN"
    DIAGNOSED = "DIAGNOSED"
    IN_REPAIR = "IN_REPAIR"
    VERIFIED_FIXED = "VERIFIED_FIXED"
    UNRESOLVED = "UNRESOLVED"
    DUPLICATE = "DUPLICATE"


class StructuredDefectFinding(BaseModel):
    finding_id: str = Field(default_factory=lambda: f"fnd_{uuid.uuid4().hex[:8]}")
    originating_agent: AgentType
    severity: DefectSeverity
    title: str
    affected_components: List[str] = Field(default_factory=list)
    reproduction_command: Optional[str] = None
    stack_trace: Optional[str] = None
    evidence_summary: str = ""
    is_confirmed: bool = True  # True if reproduced; False if suspected
    confidence_score: float = 0.8
    recommended_action: str = ""
    status: DefectStatus = DefectStatus.OPEN
    discovered_at: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())


class HighImpactActionType(str, Enum):
    PRODUCTION_DEPLOYMENT = "PRODUCTION_DEPLOYMENT"
    DESTRUCTIVE_DB_MIGRATION = "DESTRUCTIVE_DB_MIGRATION"
    SECURITY_CREDENTIAL_CHANGE = "SECURITY_CREDENTIAL_CHANGE"
    USER_DATA_DELETION = "USER_DATA_DELETION"
    OUT_OF_SCOPE_MODIFICATION = "OUT_OF_SCOPE_MODIFICATION"


class HighImpactApprovalRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: f"appr_{uuid.uuid4().hex[:8]}")
    action_type: HighImpactActionType
    target_resource: str
    description: str
    requested_by_agent: AgentType
    is_approved: bool = False
    operator_notes: Optional[str] = None
    requested_at: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    resolved_at: Optional[str] = None


class ClosedLoopIterationRecord(BaseModel):
    iteration_number: int
    phase: CompletionWorkflowStatus
    defects_count: int = 0
    tests_executed: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    patch_id: Optional[str] = None
    patch_verification_status: Optional[PatchVerificationStatus] = None
    patch_diff_hash: Optional[str] = None
    regression_detected: bool = False
    duration_seconds: float = 0.0
    estimated_cost_usd: float = 0.001
    notes: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())


class AutonomousCompletionContext(BaseModel):
    """
    Complete state machine context for an autonomous project completion & repair run.
    """
    session_id: str = Field(default_factory=lambda: f"comp_{uuid.uuid4().hex[:8]}")
    project_path: str
    status: CompletionWorkflowStatus = CompletionWorkflowStatus.ANALYZING
    checklist: List[ChecklistItem] = Field(default_factory=list)
    findings: List[StructuredDefectFinding] = Field(default_factory=list)
    approval_requests: List[HighImpactApprovalRequest] = Field(default_factory=list)
    iteration_history: List[ClosedLoopIterationRecord] = Field(default_factory=list)

    # Budget and limit configurations
    current_iteration: int = 0
    max_iterations: int = 5
    time_budget_seconds: int = 600
    cost_budget_usd: float = 0.50
    used_cost_usd: float = 0.0
    elapsed_time_seconds: float = 0.0

    # Stopping and audit state
    stopping_reason: Optional[str] = None
    unresolved_risks: List[str] = Field(default_factory=list)
    untested_areas: List[str] = Field(default_factory=list)
    residual_limitations: List[str] = Field(default_factory=list)
    start_time: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    completed_time: Optional[str] = None
