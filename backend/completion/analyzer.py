"""Project Requirement Analyzer and Completion Checklist Generator.
Inspects software repositories, detects frontend/backend architectures, discovers existing tests,
identifies missing implementations/stubs, and generates a structured completion checklist.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.completion.models import (
    ChecklistCategory,
    ChecklistItem,
    ChecklistItemStatus,
    ProjectCompletionChecklist,
    StructuredDefectFinding,
    DefectSeverity,
    DefectStatus,
)
from backend.learning.models import ProjectProfile
from backend.learning.profiler import ProjectProfiler
from backend.models.contracts import AgentType
from backend.runners.sandbox import SandboxExecutionEngine


class IncompleteFunctionStub(ast.NodeVisitor):
    """AST visitor that detects placeholder functions with pass or NotImplementedError."""

    def __init__(self):
        self.stubs: List[Tuple[str, int, str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._check_body(node.name, node.lineno, node.body)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._check_body(node.name, node.lineno, node.body)
        self.generic_visit(node)

    def _check_body(self, name: str, lineno: int, body: List[ast.stmt]):
        if not body:
            self.stubs.append((name, lineno, "Empty body"))
            return

        # Check for single 'pass' or docstring + 'pass'
        stmt_to_check = body[-1]
        if len(body) == 1 and isinstance(stmt_to_check, ast.Pass):
            self.stubs.append((name, lineno, "Contains only 'pass'"))
        elif len(body) == 2 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(stmt_to_check, ast.Pass):
            self.stubs.append((name, lineno, "Docstring followed only by 'pass'"))
        elif isinstance(stmt_to_check, ast.Raise):
            if isinstance(stmt_to_check.exc, ast.Call) and getattr(stmt_to_check.exc.func, "id", "") == "NotImplementedError":
                self.stubs.append((name, lineno, "Raises NotImplementedError"))
            elif isinstance(stmt_to_check.exc, ast.Name) and stmt_to_check.exc.id == "NotImplementedError":
                self.stubs.append((name, lineno, "Raises NotImplementedError"))


class ProjectRequirementAnalyzer:
    """Comprehensive repository inspector and completion planner."""

    def __init__(self, sandbox: Optional[SandboxExecutionEngine] = None):
        self.sandbox = sandbox or SandboxExecutionEngine()

    def analyze_project(
        self,
        project_dir: Path,
        project_id: Optional[str] = None,
    ) -> Tuple[ProjectProfile, List[ChecklistItem], List[StructuredDefectFinding]]:
        """
        Conducts deep architectural and requirement inspection.
        Returns:
            (profile, initial_checklist, initial_findings)
        """
        profile = ProjectProfiler.profile_directory(project_dir, project_id=project_id)
        checklist: List[ChecklistItem] = []
        findings: List[StructuredDefectFinding] = []

        # 1. Inspect source files vs test files
        source_files = [
            f for f in project_dir.glob("*.py")
            if not f.name.startswith("test_") and not f.name.startswith("conftest")
        ]
        test_files = list(project_dir.glob("test_*.py"))

        # 2. Build Category Item
        checklist.append(
            ChecklistItem(
                category=ChecklistCategory.BUILD,
                title="Syntax and Static Verification",
                description="Verify that all project source files are syntactically valid and importable.",
                status=ChecklistItemStatus.PENDING,
            )
        )

        # 3. Unit Tests Category Item
        checklist.append(
            ChecklistItem(
                category=ChecklistCategory.UNIT_TESTS,
                title="Unit Test Suite Execution",
                description=f"Execute unit test suite against {len(source_files)} discovered modules.",
                status=ChecklistItemStatus.PENDING,
            )
        )

        # 4. Integration Tests Category Item
        has_api_routes = profile.framework in ("fastapi", "flask", "django")
        checklist.append(
            ChecklistItem(
                category=ChecklistCategory.INTEGRATION_TESTS,
                title="API Service Contract Verification",
                description="Verify endpoint contracts, HTTP status code schemas, and database rollback invariants.",
                required=has_api_routes,
                status=ChecklistItemStatus.PENDING if has_api_routes else ChecklistItemStatus.SKIPPED,
            )
        )

        # 5. Regression Tests Category Item
        checklist.append(
            ChecklistItem(
                category=ChecklistCategory.REGRESSION_TESTS,
                title="Test Impact Regression Guard",
                description="Verify that newly applied repairs or changes introduce zero regressions to callers.",
                status=ChecklistItemStatus.PENDING,
            )
        )

        # 6. E2E Workflows Category Item
        checklist.append(
            ChecklistItem(
                category=ChecklistCategory.E2E_WORKFLOWS,
                title="End-to-End User Journey Verification",
                description="Verify critical user workflows with web-first assertions.",
                required=False,
                status=ChecklistItemStatus.PENDING,
            )
        )

        # 7. Performance Criteria Category Item
        checklist.append(
            ChecklistItem(
                category=ChecklistCategory.PERFORMANCE_CRITERIA,
                title="Latency and SLO Profiling",
                description="Verify P95/P99 latency thresholds and error rates under concurrent load.",
                required=False,
                status=ChecklistItemStatus.PENDING,
            )
        )

        # 8. Security Audit Category Item
        checklist.append(
            ChecklistItem(
                category=ChecklistCategory.SECURITY_AUDIT,
                title="Secret Scrubbing and AST Security Audit",
                description="Inspect code for hardcoded API keys, forbidden system calls, or command injection risks.",
                status=ChecklistItemStatus.PENDING,
            )
        )

        # 9. Acceptance Criteria Category Item
        checklist.append(
            ChecklistItem(
                category=ChecklistCategory.ACCEPTANCE_CRITERIA,
                title="Specification Acceptance Verification",
                description="Verify all functional acceptance criteria outlined in specifications.",
                status=ChecklistItemStatus.PENDING,
            )
        )

        # 10. Scan for Incomplete Implementation Stubs (pass, NotImplementedError)
        for sf in source_files:
            try:
                tree = ast.parse(sf.read_text(encoding="utf-8"), filename=sf.name)
                stub_visitor = IncompleteFunctionStub()
                stub_visitor.visit(tree)

                for func_name, lineno, reason in stub_visitor.stubs:
                    findings.append(
                        StructuredDefectFinding(
                            originating_agent=AgentType.UNIT,
                            severity=DefectSeverity.MAJOR,
                            title=f"Incomplete implementation in {sf.name}::{func_name}",
                            affected_components=[f"{sf.name}:{lineno}"],
                            evidence_summary=f"Function stub detected: {reason} at line {lineno}",
                            is_confirmed=True,
                            confidence_score=0.95,
                            recommended_action=f"Synthesize verified functional implementation for {func_name}",
                            status=DefectStatus.OPEN,
                        )
                    )
            except Exception:
                continue

        # 11. Run baseline build/syntax check
        build_item = next((item for item in checklist if item.category == ChecklistCategory.BUILD), None)
        syntax_errors = []
        for sf in source_files:
            try:
                ast.parse(sf.read_text(encoding="utf-8"))
            except SyntaxError as e:
                syntax_errors.append(f"{sf.name}:{e.lineno} - {e.msg}")

        if build_item:
            if syntax_errors:
                build_item.status = ChecklistItemStatus.FAILED
                build_item.evidence_summary = f"Syntax errors: {'; '.join(syntax_errors)}"
                findings.append(
                    StructuredDefectFinding(
                        originating_agent=AgentType.UNIT,
                        severity=DefectSeverity.BLOCKER,
                        title="Syntax error in repository source code",
                        affected_components=syntax_errors,
                        evidence_summary=str(syntax_errors),
                        is_confirmed=True,
                        confidence_score=1.0,
                        recommended_action="Resolve syntax error before proceeding with execution",
                    )
                )
            else:
                build_item.status = ChecklistItemStatus.PASSED
                build_item.evidence_summary = f"All {len(source_files)} source files successfully parsed."

        return profile, checklist, findings
