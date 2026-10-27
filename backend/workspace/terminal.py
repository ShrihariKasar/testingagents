"""Real Terminal Execution Service for Autonomous AI Software Development Workspace.
Executes CLI commands in project context via SandboxExecutionEngine with secret scrubbing,
timeout controls, and structured output capture for the workspace bottom panel.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from backend.models.contracts import TerminalExecuteRequest, TerminalExecuteResponse
from backend.runners.sandbox import SandboxExecutionEngine, redact_secrets
from backend.workspace.filesystem import safe_resolve_path


class WorkspaceTerminalService:
    """Executes safe commands in project workspace context."""

    def __init__(self, sandbox_engine: Optional[SandboxExecutionEngine] = None):
        self.sandbox = sandbox_engine or SandboxExecutionEngine()

    @staticmethod
    def redact_secrets(text: str) -> str:
        """Sanitizes text by scrubbing sensitive keys, tokens, and credentials."""
        return redact_secrets(text)

    def execute_command(
        self,
        project_root: Path,
        command: str,
        cwd_rel: Optional[str] = None,
        timeout_seconds: int = 30,
    ) -> TerminalExecuteResponse:
        """
        Executes a real terminal command inside the validated workspace directory.
        Redacts sensitive tokens / keys from output and enforces strict timeout boundaries.
        """
        effective_cwd = safe_resolve_path(project_root, cwd_rel) if cwd_rel else project_root.resolve()
        start_time = time.perf_counter()

        # Guard against fork bombs or unbounded commands
        clamped_timeout = min(max(timeout_seconds, 1), 120)

        evidence = self.sandbox.run_command(
            command=command,
            cwd=effective_cwd,
            timeout_seconds=clamped_timeout,
        )

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        timed_out = evidence.exit_code == 124

        clean_stdout = redact_secrets(evidence.stdout)
        clean_stderr = redact_secrets(evidence.stderr)

        return TerminalExecuteResponse(
            command=command,
            exit_code=evidence.exit_code,
            stdout=clean_stdout,
            stderr=clean_stderr,
            duration_ms=round(duration_ms, 2),
            timed_out=timed_out,
        )
