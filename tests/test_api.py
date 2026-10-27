"""Tests for FastAPI Control Plane and Human Authorization Gate."""
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient

from backend.api.app import app, PATCH_STORE, RUNS_STORE
from backend.models.contracts import PatchVerificationStatus, ProposedPatch, RiskLevel

client = TestClient(app)


def test_api_health():
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "online"
    assert "Unit Testing Agent" in data["agents"]
    assert "Independent Verifier" in data["agents"]


def test_api_trigger_and_get_run():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        (tmppath / "math_ops.py").write_text("def add(a: int, b: int) -> int: return a + b\n")

        res = client.post(
            "/api/runs",
            json={
                "project_path": str(tmppath),
                "use_mock_adapter": True,
            },
        )
        assert res.status_code == 200
        run_data = res.json()
        assert "run_id" in run_data
        run_id = run_data["run_id"]

        # Fetch run by ID
        res_get = client.get(f"/api/runs/{run_id}")
        assert res_get.status_code == 200
        assert res_get.json()["run_id"] == run_id


def test_human_approval_gate():
    # Setup test patch in store
    patch_id = "patch_test_gate_1"
    PATCH_STORE[patch_id] = ProposedPatch(
        patch_id=patch_id,
        target_files=["sample.py"],
        unified_diff="--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n",
        root_cause_analysis="Test repair",
        fault_location="sample.py",
        risk_level=RiskLevel.LOW,
        verification_status=PatchVerificationStatus.UNVERIFIED,
        original_failure_log="failure",
    )

    # 1. Unverified patch cannot be approved
    res_fail = client.post(f"/api/patches/{patch_id}/approve")
    assert res_fail.status_code == 400
    assert "Cannot apply unverified patch" in res_fail.json()["detail"]

    # 2. Mark as verified by independent verifier
    PATCH_STORE[patch_id].verification_status = PatchVerificationStatus.VERIFIED
    res_approve = client.post(f"/api/patches/{patch_id}/approve")
    assert res_approve.status_code == 200
    assert res_approve.json()["status"] == "applied"
    assert PATCH_STORE[patch_id].verification_status == PatchVerificationStatus.APPROVED

    # 3. Rejection gate
    patch_id_2 = "patch_test_gate_2"
    PATCH_STORE[patch_id_2] = ProposedPatch(
        patch_id=patch_id_2,
        target_files=["sample.py"],
        unified_diff="",
        root_cause_analysis="Rejection test",
        fault_location="sample.py",
        verification_status=PatchVerificationStatus.VERIFIED,
        original_failure_log="failure",
    )
    res_rej = client.post(f"/api/patches/{patch_id_2}/reject")
    assert res_rej.status_code == 200
    assert PATCH_STORE[patch_id_2].verification_status == PatchVerificationStatus.REJECTED
