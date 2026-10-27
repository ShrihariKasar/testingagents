"""Data contracts and shared Pydantic models for the Autonomous Multi-Agent Testing Platform."""
from __future__ import annotations

import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AgentType(str, Enum):
    UNIT = "unit"
    INTEGRATION = "integration"
    REGRESSION = "regression"
    PERFORMANCE = "performance"
    E2E = "e2e"
    REPAIR = "repair"
    ORCHESTRATOR = "orchestrator"
    VERIFIER = "verifier"
    PROJECT_ANALYSIS = "project_analysis"
    SOFTWARE_DEVELOPMENT = "software_development"
    CODE_REVIEW_SECURITY = "code_review_security"
    CONTINUOUS_LEARNING = "continuous_learning"


class TaskStatus(str, Enum):
    QUEUED = "QUEUED"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING_FOR_DEPENDENCY = "WAITING_FOR_DEPENDENCY"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class TestStatus(str, Enum):
    __test__ = False
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"
    TIMEOUT = "timeout"


class PatchVerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"
    FAILED_REGRESSION = "failed_regression"
    FAILED_ORIGINAL = "failed_original"
    INCONCLUSIVE = "inconclusive"
    REJECTED = "rejected"
    APPROVED = "approved"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ExecutionEvidence(BaseModel):
    """Immutable, verifiable evidence from an isolated sandbox test execution."""
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    coverage_percentage: Optional[float] = None
    branch_coverage_percentage: Optional[float] = None
    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    total_count: int = 0
    artifact_paths: List[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    executed_in_sandbox: bool = True


class TestCaseResult(BaseModel):
    """Result of an individual test case."""
    __test__ = False
    test_id: str
    name: str
    file_path: str
    status: TestStatus
    duration_ms: float
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None


class TestSuiteReport(BaseModel):
    """Aggregated test suite report parsed from runner output / JUnit XML."""
    __test__ = False
    suite_name: str
    status: TestStatus
    total_tests: int
    passed_tests: int
    failed_tests: int
    skipped_tests: int
    error_tests: int
    duration_ms: float
    test_cases: List[TestCaseResult] = Field(default_factory=list)
    raw_evidence: Optional[ExecutionEvidence] = None


class CoverageGap(BaseModel):
    """Identifies an uncovered block, function, or branch in target source code."""
    file_path: str
    symbol_name: str
    symbol_type: str  # "function", "class", "method", "branch"
    start_line: int
    end_line: int
    missing_branches: List[int] = Field(default_factory=list)
    cyclomatic_complexity: int = 1


class ProposedPatch(BaseModel):
    """Autonomous repair candidate proposed by the diagnosis engine."""
    patch_id: str
    target_files: List[str]
    unified_diff: str
    root_cause_analysis: str
    fault_location: Optional[str] = None
    risk_level: RiskLevel = RiskLevel.MEDIUM
    verification_status: PatchVerificationStatus = PatchVerificationStatus.UNVERIFIED
    original_failure_log: Optional[str] = None
    reproduction_evidence: Optional[ExecutionEvidence] = None
    isolated_test_evidence: Optional[ExecutionEvidence] = None
    regression_evidence: Optional[ExecutionEvidence] = None
    independent_verification_evidence: Optional[ExecutionEvidence] = None
    created_at: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())


class AgentExecutionLog(BaseModel):
    """Audit log entry of an agent's execution step."""
    step_id: str
    agent_type: AgentType
    action: str
    details: Dict[str, Any] = Field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    timestamp: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())


class RunContext(BaseModel):
    """Complete shared state of a testing session."""
    run_id: str
    project_path: str
    target_files: List[str] = Field(default_factory=list)
    status: str = "initialized"
    time_budget_seconds: int = 300
    token_budget: int = 500000
    used_tokens: int = 0
    estimated_cost_usd: float = 0.0
    start_time: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    completed_time: Optional[str] = None
    active_agent: Optional[AgentType] = None
    suite_reports: List[TestSuiteReport] = Field(default_factory=list)
    proposed_patches: List[ProposedPatch] = Field(default_factory=list)
    execution_logs: List[AgentExecutionLog] = Field(default_factory=list)


class TaskNode(BaseModel):
    """Verifiable unit of engineering work tracked in the central orchestrator task graph."""
    task_id: str
    project_id: str
    title: str
    objective: str
    acceptance_criteria: List[str] = Field(default_factory=list)
    assigned_agent: AgentType
    dependencies: List[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.QUEUED
    attempts: int = 0
    max_retries: int = 3
    execution_evidence: Optional[ExecutionEvidence] = None
    changed_files: List[str] = Field(default_factory=list)
    test_results: Dict[str, Any] = Field(default_factory=dict)
    failure_details: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class FileTreeNode(BaseModel):
    """Node in the recursive directory tree for the Left Panel Project Explorer."""
    name: str
    path: str
    is_directory: bool
    size_bytes: int = 0
    modified_time: float = 0.0
    git_status: Optional[str] = None  # "M" (modified), "A" (added), "D" (deleted), "U" (untracked), None
    children: Optional[List[FileTreeNode]] = None


class FileReadResponse(BaseModel):
    """File content and metadata for Monaco Editor rendering."""
    path: str
    content: str
    size_bytes: int
    modified_time: float
    language: str
    is_readonly: bool = False
    git_status: Optional[str] = None


class FileWriteRequest(BaseModel):
    """Save request with optimistic concurrency conflict detection."""
    project_id: str = "default"
    path: str
    content: str
    base_modified_time: Optional[float] = None
    force: bool = False


class FileWriteResponse(BaseModel):
    status: str
    path: str
    size_bytes: int
    modified_time: float
    conflict_detected: bool = False
    server_content: Optional[str] = None
    success: bool = True


class FileOperationRequest(BaseModel):
    project_id: str = "default"
    path: str
    new_path: Optional[str] = None
    is_directory: bool = False
    operation: Optional[str] = None


class TerminalExecuteRequest(BaseModel):
    project_id: str = "default"
    command: str
    cwd_rel: Optional[str] = None
    timeout_seconds: int = 30


class TerminalExecuteResponse(BaseModel):
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False


class ChangeReviewRecord(BaseModel):
    change_id: str
    project_id: str
    target_file: str
    change_type: str  # "MODIFIED", "ADDED", "DELETED"
    responsible_agent: AgentType
    task_id: Optional[str] = None
    diff: str
    timestamp: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    is_applied: bool = True
    approval_status: str = "APPROVED"
