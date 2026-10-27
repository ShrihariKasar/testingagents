"""Autonomous Project Completion and Repair Engine Module."""
from backend.completion.models import (
    AutonomousCompletionContext,
    ChecklistCategory,
    ChecklistItem,
    ChecklistItemStatus,
    ClosedLoopIterationRecord,
    CompletionWorkflowStatus,
    DefectSeverity,
    DefectStatus,
    HighImpactActionType,
    HighImpactApprovalRequest,
    StructuredDefectFinding,
)
from backend.completion.analyzer import ProjectRequirementAnalyzer
from backend.completion.engine import AutonomousProjectCompletionEngine

__all__ = [
    "AutonomousCompletionContext",
    "ChecklistCategory",
    "ChecklistItem",
    "ChecklistItemStatus",
    "ClosedLoopIterationRecord",
    "CompletionWorkflowStatus",
    "DefectSeverity",
    "DefectStatus",
    "HighImpactActionType",
    "HighImpactApprovalRequest",
    "StructuredDefectFinding",
    "ProjectRequirementAnalyzer",
    "AutonomousProjectCompletionEngine",
]
