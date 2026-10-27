"""Independent Verification Engine.
Runs zero-shot validation in an isolated, untainted environment with clean checkouts.
Does not utilize LLMs or shared state to evaluate candidate patches.
"""
from __future__ import annotations

import difflib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional, Tuple

from backend.models.contracts import (
    ExecutionEvidence,
    PatchVerificationStatus,
    ProposedPatch,
    TestSuiteReport,
)
from backend.runners.sandbox import SandboxExecutionEngine


def apply_unified_diff(project_dir: Any, diff_text: Any) -> bool:
    """
    Applies a standard unified diff to target files inside project_dir.
    Robustly handles both (project_dir, diff_text) and (diff_text, project_dir) parameter order.
    Returns True if successfully patched, False otherwise.
    """
    try:
        # Normalize arguments
        if isinstance(project_dir, str) and not isinstance(diff_text, str):
            project_dir, diff_text = diff_text, project_dir
        elif isinstance(project_dir, str) and isinstance(diff_text, str):
            if "--- " in project_dir and "--- " not in diff_text:
                project_dir, diff_text = Path(diff_text), project_dir
            else:
                project_dir = Path(project_dir)
        else:
            project_dir = Path(project_dir)

        diff_str = str(diff_text or "").strip()
        if not diff_str:
            return True

        lines = diff_str.splitlines()
        current_file: Optional[Path] = None
        i = 0
        n = len(lines)

        while i < n:
            line = lines[i]
            if line.startswith("--- "):
                i += 1
                if i < n and lines[i].startswith("+++ "):
                    target_path_str = lines[i][4:].strip()
                    if target_path_str.startswith("b/"):
                        target_path_str = target_path_str[2:]
                    elif target_path_str.startswith("a/"):
                        target_path_str = target_path_str[2:]
                    current_file = project_dir / target_path_str
                    i += 1
                    continue
            elif line.startswith("@@"):
                hunk_header = line
                m = re.search(r"@@\s*-(\d+)(?:,(\d+))?\s*\+(\d+)(?:,(\d+))?\s*@@", hunk_header)
                orig_start = int(m.group(1)) if m else 1
                i += 1

                old_lines: list[str] = []
                new_lines: list[str] = []
                while i < n and not lines[i].startswith("@@") and not lines[i].startswith("--- "):
                    hunk_line = lines[i]
                    if hunk_line.startswith("-"):
                        old_lines.append(hunk_line[1:])
                    elif hunk_line.startswith("+"):
                        new_lines.append(hunk_line[1:])
                    elif hunk_line.startswith(" "):
                        old_lines.append(hunk_line[1:])
                        new_lines.append(hunk_line[1:])
                    else:
                        old_lines.append(hunk_line)
                        new_lines.append(hunk_line)
                    i += 1

                if current_file and current_file.exists():
                    file_text = current_file.read_text(encoding="utf-8")
                    old_block = "\n".join(old_lines)
                    new_block = "\n".join(new_lines)
                    if old_block and old_block in file_text:
                        updated_text = file_text.replace(old_block, new_block, 1)
                        current_file.write_text(updated_text, encoding="utf-8")
                    else:
                        # Fallback to line index slicing
                        file_lines = file_text.splitlines()
                        start_idx = max(0, orig_start - 1)
                        count = max(1, len(old_lines))
                        file_lines[start_idx:start_idx + count] = new_lines
                        suffix = "\n" if file_text.endswith("\n") else ""
                        current_file.write_text("\n".join(file_lines) + suffix, encoding="utf-8")
                elif current_file:
                    current_file.parent.mkdir(parents=True, exist_ok=True)
                    current_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                continue
            else:
                i += 1

        return True
    except Exception:
        return False


class IndependentVerifier:
    """
    Zero-shot verification agent. Operates strictly as a judge with no generative capabilities.
    """

    def __init__(self, sandbox_engine: Optional[SandboxExecutionEngine] = None):
        self.sandbox = sandbox_engine or SandboxExecutionEngine()

    def verify_patch(
        self,
        base_repo_dir: Path,
        failing_test_path: str,
        regression_test_paths: list[str],
        patch: ProposedPatch,
    ) -> Tuple[PatchVerificationStatus, Optional[ExecutionEvidence], Optional[ExecutionEvidence]]:
        """
        Executes a rigorous, isolated verification:
        1. Clones base_repo_dir into a clean, ephemeral sandbox.
        2. Applies patch.unified_diff.
        3. Runs original failing test -> must PASS.
        4. Runs regression suite -> all must PASS.
        Returns:
            (status, target_test_evidence, regression_evidence)
        """
        temp_dir = tempfile.mkdtemp(prefix="agent_verifier_")
        work_dir = Path(temp_dir)

        try:
            # Clean copy of untainted repository
            shutil.copytree(
                base_repo_dir,
                work_dir,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.pyc", ".agent_artifacts"),
            )

            # Apply candidate patch
            patch_success = apply_unified_diff(work_dir, patch.unified_diff)
            if not patch_success:
                return (
                    PatchVerificationStatus.INCONCLUSIVE,
                    ExecutionEvidence(
                        command="apply_diff",
                        exit_code=1,
                        stdout="",
                        stderr="Failed to parse and apply candidate unified diff.",
                        duration_ms=0.0,
                        executed_in_sandbox=True,
                    ),
                    None,
                )

            # 1. Run the previously failing test
            target_evidence, target_report = self.sandbox.run_pytest(
                target_dir=work_dir,
                test_path=failing_test_path,
                ephemeral_clone=False,
            )

            if target_evidence.exit_code != 0 or (target_report and target_report.failed_tests > 0):
                return PatchVerificationStatus.FAILED_ORIGINAL, target_evidence, None

            # 2. Run regression tests if provided
            regression_evidence: Optional[ExecutionEvidence] = None
            if regression_test_paths:
                for reg_path in regression_test_paths:
                    reg_ev, reg_rep = self.sandbox.run_pytest(
                        target_dir=work_dir,
                        test_path=reg_path,
                        ephemeral_clone=False,
                    )
                    if reg_ev.exit_code != 0 or (reg_rep and reg_rep.failed_tests > 0):
                        return PatchVerificationStatus.FAILED_REGRESSION, target_evidence, reg_ev
                    regression_evidence = reg_ev

            # If both pass cleanly, the fix is independently verified!
            return PatchVerificationStatus.VERIFIED, target_evidence, regression_evidence

        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
