"""Deterministic Test Runner and Execution Sandbox.
Provides isolated execution environments, secret redaction, resource bounds,
and automated evidence extraction (JUnit XML, coverage JSON).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.models.contracts import (
    ExecutionEvidence,
    TestSuiteReport,
    TestStatus,
)
from backend.runners.coverage_parser import parse_coverage_json
from backend.runners.junit_parser import parse_junit_xml

# Regex patterns for scrubbing secrets from stdout/stderr/environment
SECRET_PATTERNS = [
    re.compile(r"(sk-[a-zA-Z0-9_\-]{20,})", re.IGNORECASE),
    re.compile(r"(AIza[0-9A-Za-z_\-]{20,})", re.IGNORECASE),
    re.compile(r"(ghp_[a-zA-Z0-9]{30,})", re.IGNORECASE),
    re.compile(r"(bearer\s+[a-zA-Z0-9_\-\.]+)", re.IGNORECASE),
    re.compile(r"(password\s*[:=]\s*['\"][^'\"]+['\"])", re.IGNORECASE),
]


def redact_secrets(text: str) -> str:
    """Scrub sensitive credentials, tokens, and keys from logs and output."""
    if not text:
        return ""
    scrubbed = text
    for pattern in SECRET_PATTERNS:
        scrubbed = pattern.sub("[REDACTED_SECRET]", scrubbed)
    return scrubbed


class SandboxExecutionEngine:
    """
    Manages deterministic, isolated execution of test suites and commands.
    Supports in-place or ephemeral isolated workspace clones.
    """

    def __init__(self, python_executable: Optional[str] = None):
        # Prefer the virtualenv python if available
        if python_executable and Path(python_executable).exists():
            self.python_exec = python_executable
        else:
            # Check for current sys.executable or .venv
            venv_python = Path(sys.prefix) / "Scripts" / "python.exe"
            if venv_python.exists():
                self.python_exec = str(venv_python)
            else:
                self.python_exec = sys.executable

    def _prepare_sanitized_env(self, custom_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Creates an environment stripped of sensitive host environment variables."""
        env = os.environ.copy()

        # Scrub known credential variables from child process
        sensitive_keys = [
            "AWS_SECRET_ACCESS_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "GITHUB_TOKEN",
            "SSH_AUTH_SOCK",
        ]
        for key in sensitive_keys:
            env.pop(key, None)

        # Standard test runners configuration
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"

        if custom_env:
            env.update(custom_env)

        return env

    def run_command(
        self,
        command: List[str],
        cwd: Path,
        timeout_seconds: float = 45.0,
        custom_env: Optional[Dict[str, str]] = None,
    ) -> ExecutionEvidence:
        """
        Executes a raw command inside the sandbox directory with timeout and output capture.
        """
        env = self._prepare_sanitized_env(custom_env)
        start_time = time.perf_counter()

        try:
            process = subprocess.run(
                command,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
                env=env,
                shell=False,
            )
            duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
            stdout = redact_secrets(process.stdout)
            stderr = redact_secrets(process.stderr)
            exit_code = process.returncode

        except subprocess.TimeoutExpired as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
            stdout = redact_secrets(exc.stdout or "")
            stderr = f"Execution timed out after {timeout_seconds} seconds.\n" + redact_secrets(exc.stderr or "")
            exit_code = 124

        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
            stdout = ""
            stderr = f"Sandbox execution error: {str(exc)}"
            exit_code = 1

        cmd_str = " ".join(command)
        return ExecutionEvidence(
            command=cmd_str,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            executed_in_sandbox=True,
        )

    def run_pytest(
        self,
        target_dir: Path,
        test_path: Optional[str] = None,
        cov_target: Optional[str] = None,
        timeout_seconds: float = 60.0,
        ephemeral_clone: bool = False,
    ) -> Tuple[ExecutionEvidence, Optional[TestSuiteReport]]:
        """
        Executes pytest with automated generation of JUnit XML and Coverage JSON artifacts.
        If ephemeral_clone=True, creates a disposable copy of target_dir to guarantee isolation.
        """
        work_dir = target_dir
        temp_dir: Optional[str] = None

        if ephemeral_clone:
            temp_dir = tempfile.mkdtemp(prefix="agent_sandbox_")
            work_dir = Path(temp_dir)
            # Copy all files except .git and virtualenvs
            shutil.copytree(
                target_dir,
                work_dir,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.pyc", "node_modules"),
            )

        artifacts_dir = work_dir / ".agent_artifacts"
        artifacts_dir.mkdir(exist_ok=True)
        junit_path = artifacts_dir / "junit_report.xml"
        coverage_json = artifacts_dir / "coverage.json"

        # Build pytest command
        cmd = [
            self.python_exec,
            "-m",
            "pytest",
            f"--junitxml={str(junit_path)}",
            "-o",
            "junit_family=xunit2",
            "-v",
        ]

        if cov_target:
            cmd.extend([
                f"--cov={cov_target}",
                f"--cov-report=json:{str(coverage_json)}",
                "--cov-branch",
            ])

        if test_path:
            if isinstance(test_path, list):
                cmd.extend(test_path)
            elif " " in test_path and not (work_dir / test_path).exists():
                cmd.extend(test_path.split())
            else:
                cmd.append(test_path)

        try:
            evidence = self.run_command(cmd, cwd=work_dir, timeout_seconds=timeout_seconds)

            # Parse JUnit XML
            suite_report = parse_junit_xml(junit_path, suite_name=test_path or "Target Test Suite")

            # Parse Coverage if generated
            line_cov, branch_cov, gaps = parse_coverage_json(coverage_json)
            if line_cov is not None:
                evidence.coverage_percentage = line_cov
            if branch_cov is not None:
                evidence.branch_coverage_percentage = branch_cov

            if suite_report:
                evidence.passed_count = suite_report.passed_tests
                evidence.failed_count = suite_report.failed_tests
                evidence.skipped_count = suite_report.skipped_tests
                evidence.error_count = suite_report.error_tests
                evidence.total_count = suite_report.total_tests
                suite_report.raw_evidence = evidence

            # Save artifact locations
            if junit_path.exists():
                evidence.artifact_paths.append(str(junit_path))
            if coverage_json.exists():
                evidence.artifact_paths.append(str(coverage_json))

            return evidence, suite_report

        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
