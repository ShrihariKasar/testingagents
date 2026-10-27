"""Tests for Autonomous Diagnosis, Repair Agent, and Independent Verifier."""
import tempfile
from pathlib import Path

from backend.adapters.mock_adapter import DeterministicMockAdapter
from backend.agents.repair_agent import AutonomousRepairAgent
from backend.agents.verifier import IndependentVerifier, apply_unified_diff
from backend.models.contracts import PatchVerificationStatus


BUGGY_CODE = """def calculate_tier_bonus(score: int) -> int:
    # Seeded defect: should be score >= 100, but has score > 100
    if score > 100:
        return 50
    return 10
"""

FAILING_TEST = """from bonus import calculate_tier_bonus

def test_score_boundary_100():
    # 100 should receive bonus 50, but buggy code gives 10
    assert calculate_tier_bonus(100) == 50
"""

REGRESSION_TEST = """from bonus import calculate_tier_bonus

def test_score_low():
    assert calculate_tier_bonus(50) == 10

def test_score_high():
    assert calculate_tier_bonus(150) == 50
"""

CORRECT_PATCH_DIFF = """--- a/bonus.py
+++ b/bonus.py
@@ -1,4 +1,4 @@
 def calculate_tier_bonus(score: int) -> int:
-    if score > 100:
+    if score >= 100:
         return 50
     return 10
"""

REGRESSION_BREAKING_PATCH_DIFF = """--- a/bonus.py
+++ b/bonus.py
@@ -1,4 +1,4 @@
 def calculate_tier_bonus(score: int) -> int:
-    if score > 100:
+    if score >= 0:
         return 50
     return 10
"""


def test_independent_verifier_verified_success():
    verifier = IndependentVerifier()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        (tmppath / "bonus.py").write_text(BUGGY_CODE, encoding="utf-8")
        (tmppath / "test_failing.py").write_text(FAILING_TEST, encoding="utf-8")
        (tmppath / "test_regression.py").write_text(REGRESSION_TEST, encoding="utf-8")

        adapter = DeterministicMockAdapter()
        adapter.set_next_response(f"Root cause: boundary condition.\n```diff\n{CORRECT_PATCH_DIFF}\n```")

        repair_agent = AutonomousRepairAgent(model_adapter=adapter, verifier=verifier)
        patch = repair_agent.diagnose_and_repair(
            project_dir=tmppath,
            failing_test_path="test_failing.py",
            target_source_path=tmppath / "bonus.py",
            regression_test_paths=["test_regression.py"],
        )

        assert patch.verification_status == PatchVerificationStatus.VERIFIED
        assert patch.isolated_test_evidence is not None
        assert patch.isolated_test_evidence.exit_code == 0
        assert patch.regression_evidence is not None
        assert patch.regression_evidence.exit_code == 0


def test_independent_verifier_catches_regression():
    verifier = IndependentVerifier()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        (tmppath / "bonus.py").write_text(BUGGY_CODE, encoding="utf-8")
        (tmppath / "test_failing.py").write_text(FAILING_TEST, encoding="utf-8")
        (tmppath / "test_regression.py").write_text(REGRESSION_TEST, encoding="utf-8")

        adapter = DeterministicMockAdapter()
        # Propose patch that fixes 100 but breaks score 50
        adapter.set_next_response(f"Flawed patch.\n```diff\n{REGRESSION_BREAKING_PATCH_DIFF}\n```")

        repair_agent = AutonomousRepairAgent(model_adapter=adapter, verifier=verifier)
        patch = repair_agent.diagnose_and_repair(
            project_dir=tmppath,
            failing_test_path="test_failing.py",
            target_source_path=tmppath / "bonus.py",
            regression_test_paths=["test_regression.py"],
        )

        # Must be rejected because regression suite failed!
        assert patch.verification_status == PatchVerificationStatus.FAILED_REGRESSION
