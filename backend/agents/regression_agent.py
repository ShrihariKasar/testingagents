"""Specialized Regression Testing Agent.
Implements Test Impact Analysis (TIA) using AST call-graphs and git diffs,
prioritizing minimal test suites to detect regressions with minimal execution cost.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple
from pydantic import BaseModel, Field

from backend.adapters.base import BaseModelAdapter
from backend.models.contracts import AgentType, ExecutionEvidence, TestSuiteReport
from backend.runners.sandbox import SandboxExecutionEngine
from backend.learning.agent_interface import BaseContinuousLearningAgent


class TestImpactReport(BaseModel):
    modified_files: List[str] = Field(default_factory=list)
    modified_symbols: List[str] = Field(default_factory=list)
    selected_tests: List[str] = Field(default_factory=list)
    prioritization_order: List[str] = Field(default_factory=list)
    reduction_percentage: float = 0.0


class RegressionTestingAgent(BaseContinuousLearningAgent):
    """Manages change impact analysis, test selection, and regression prevention."""

    def __init__(self, model_adapter: BaseModelAdapter, sandbox_engine: Optional[SandboxExecutionEngine] = None):
        super().__init__(agent_type=AgentType.REGRESSION)
        self.adapter = model_adapter
        self.sandbox = sandbox_engine or SandboxExecutionEngine()

    def parse_git_diff(self, diff_text: str) -> List[str]:
        """Extracts names of modified files from git diff output."""
        modified_files = []
        for line in diff_text.splitlines():
            if line.startswith("+++ b/"):
                modified_files.append(line[6:].strip())
        return modified_files

    def map_test_dependencies(self, test_dir: Path) -> Dict[str, Set[str]]:
        """
        Statically analyzes test files to build a mapping from test file -> imported modules/symbols.
        """
        dep_map: Dict[str, Set[str]] = {}
        for test_file in test_dir.glob("test_*.py"):
            try:
                tree = ast.parse(test_file.read_text(encoding="utf-8"))
                imported_names: Set[str] = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imported_names.add(alias.name)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            imported_names.add(node.module)
                        for alias in node.names:
                            imported_names.add(alias.name)
                dep_map[test_file.name] = imported_names
            except Exception:
                continue
        return dep_map

    def select_regression_tests(
        self,
        modified_files: List[str],
        test_dir: Path,
    ) -> TestImpactReport:
        """
        Selects only the subset of tests affected by modified files.
        """
        dep_map = self.map_test_dependencies(test_dir)
        all_tests = list(dep_map.keys())
        if not all_tests:
            return TestImpactReport()

        modified_stems = {Path(f).stem for f in modified_files}
        selected: Set[str] = set()

        for test_name, deps in dep_map.items():
            # If test directly imports any modified module stem
            if any(stem in deps for stem in modified_stems):
                selected.add(test_name)

        # Fallback: if no direct match, run all tests
        if not selected:
            selected = set(all_tests)

        reduction = round((1.0 - (len(selected) / len(all_tests))) * 100.0, 2)

        return TestImpactReport(
            modified_files=modified_files,
            modified_symbols=list(modified_stems),
            selected_tests=sorted(list(selected)),
            prioritization_order=sorted(list(selected)),
            reduction_percentage=reduction,
        )

    def run_selected_regressions(
        self,
        project_dir: Path,
        selected_tests: List[str],
    ) -> Tuple[ExecutionEvidence, Optional[TestSuiteReport]]:
        """Executes selected regression tests inside the sandbox."""
        existing = [t for t in selected_tests if (project_dir / t).exists()]
        return self.sandbox.run_pytest(
            target_dir=project_dir,
            test_path=existing if existing else (selected_tests if selected_tests else None),
            ephemeral_clone=False,
        )
