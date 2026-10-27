"""Tests for the deterministic sandbox runner and parsers."""
import tempfile
from pathlib import Path
from backend.runners.sandbox import SandboxExecutionEngine, redact_secrets
from backend.models.contracts import TestStatus


def test_redact_secrets():
    raw_text = "Encountered key sk-1234567890abcdef12345678 and token AIzaSyD9876543210zyxwvutsrqponmlkjihg"
    cleaned = redact_secrets(raw_text)
    assert "sk-" not in cleaned
    assert "AIza" not in cleaned
    assert "[REDACTED_SECRET]" in cleaned


def test_sandbox_run_command_success():
    engine = SandboxExecutionEngine()
    cwd = Path.cwd()
    res = engine.run_command([engine.python_exec, "-c", "print('sandbox_ok')"], cwd=cwd)
    assert res.exit_code == 0
    assert "sandbox_ok" in res.stdout
    assert res.duration_ms > 0
    assert res.executed_in_sandbox is True


def test_sandbox_timeout_handling():
    engine = SandboxExecutionEngine()
    cwd = Path.cwd()
    # Run a script that sleeps longer than timeout
    res = engine.run_command(
        [engine.python_exec, "-c", "import time; time.sleep(5)"],
        cwd=cwd,
        timeout_seconds=0.5,
    )
    assert res.exit_code == 124
    assert "timed out" in res.stderr.lower()


def test_sandbox_pytest_execution_with_junit():
    engine = SandboxExecutionEngine()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        # Create a sample test file
        test_file = tmppath / "test_sample.py"
        test_file.write_text(
            """
def test_addition():
    assert 1 + 1 == 2

def test_failure():
    assert 2 * 2 == 5
""",
            encoding="utf-8",
        )

        evidence, suite_report = engine.run_pytest(
            target_dir=tmppath,
            test_path=str(test_file),
            ephemeral_clone=False,
        )

        assert evidence.exit_code != 0  # 1 test failed
        assert suite_report is not None
        assert suite_report.total_tests == 2
        assert suite_report.passed_tests == 1
        assert suite_report.failed_tests == 1
        assert suite_report.status == TestStatus.FAILED

        # Verify individual test case results
        cases = {c.name: c for c in suite_report.test_cases}
        assert cases["test_addition"].status == TestStatus.PASSED
        assert cases["test_failure"].status == TestStatus.FAILED
        assert "assert 2 * 2 == 5" in (cases["test_failure"].stack_trace or "")
