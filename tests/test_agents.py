"""Verification suite for Integration, Regression, Performance, and E2E agents."""
import tempfile
from pathlib import Path

from backend.adapters.mock_adapter import DeterministicMockAdapter
from backend.agents.integration_agent import IntegrationTestingAgent
from backend.agents.regression_agent import RegressionTestingAgent
from backend.agents.performance_agent import PerformanceTestingAgent, PerformanceThresholds
from backend.agents.e2e_agent import EndToEndTestingAgent, E2ETestScenario, UserJourneyStep


def test_integration_agent_endpoint_discovery():
    agent = IntegrationTestingAgent(model_adapter=DeterministicMockAdapter())
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(
            """
from fastapi import APIRouter
router = APIRouter()

@router.get("/api/v1/users")
def list_users(): return []

@router.post("/api/v1/users")
def create_user(): return {}
"""
        )
        tmp_path = Path(f.name)

    try:
        endpoints = agent.discover_endpoints_from_fastapi_app(tmp_path)
        assert len(endpoints) == 2
        paths = {ep.path: ep.method for ep in endpoints}
        assert paths["/api/v1/users"] == "POST"  # or GET
    finally:
        tmp_path.unlink(missing_ok=True)


def test_regression_agent_test_impact_selection():
    agent = RegressionTestingAgent(model_adapter=DeterministicMockAdapter())
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        (tmppath / "test_auth.py").write_text("import auth\ndef test_login(): pass", encoding="utf-8")
        (tmppath / "test_billing.py").write_text("import billing\ndef test_pay(): pass", encoding="utf-8")
        (tmppath / "test_common.py").write_text("import auth\nimport billing\ndef test_all(): pass", encoding="utf-8")

        # Given a diff modifying only auth.py
        diff_text = "--- a/auth.py\n+++ b/auth.py\n@@ -1 +1 @@"
        modified = agent.parse_git_diff(diff_text)
        assert modified == ["auth.py"]

        report = agent.select_regression_tests(modified_files=modified, test_dir=tmppath)

        # test_auth and test_common import auth, test_billing does not!
        assert "test_auth.py" in report.selected_tests
        assert "test_common.py" in report.selected_tests
        assert "test_billing.py" not in report.selected_tests
        assert report.reduction_percentage > 0.0


def test_performance_agent_percentiles_and_slo():
    agent = PerformanceTestingAgent()
    # 100 sample latencies from 10ms to 300ms
    latencies = [float(i * 3) for i in range(1, 101)]  # 3ms to 300ms
    thresholds = PerformanceThresholds(max_p95_latency_ms=250.0, max_p99_latency_ms=400.0)

    dist = agent.calculate_percentiles(latencies, total_time_seconds=2.0, thresholds=thresholds)

    assert dist.sample_count == 100
    assert dist.min_ms == 3.0
    assert dist.max_ms == 300.0
    assert dist.p50_ms == 153.0
    # P95 is ~ 285ms, which exceeds 250ms threshold
    assert dist.slo_met is False
    assert len(dist.violations) == 1
    assert "P95 latency" in dist.violations[0]


def test_e2e_agent_script_generation():
    adapter = DeterministicMockAdapter()
    adapter.set_next_response(
        "```python\n"
        "from playwright.sync_api import Page, expect\n"
        "def test_login(page: Page):\n"
        "    page.goto('http://localhost:3000')\n"
        "    expect(page.get_by_role('button', name='Submit')).to_be_visible()\n"
        "```"
    )
    agent = EndToEndTestingAgent(model_adapter=adapter)
    scenario = E2ETestScenario(
        scenario_name="User Login Flow",
        steps=[
            UserJourneyStep(action="navigate", target="http://localhost:3000"),
            UserJourneyStep(action="click", target="button[name='Submit']"),
        ],
    )
    suite = agent.generate_playwright_test(scenario)
    assert suite.scenario_name == "User Login Flow"
    assert "page.get_by_role" in suite.script_content
