"""Verification suite for AST symbol extraction, security validation, and Unit Testing Agent."""
import tempfile
from pathlib import Path
from backend.adapters.mock_adapter import DeterministicMockAdapter
from backend.agents.ast_extractor import extract_symbols_from_file
from backend.agents.unit_agent import UnitTestingAgent, validate_test_ast
from backend.models.contracts import TestStatus


SAMPLE_MODULE_CODE = '''"""Sample billing engine with conditional discount branches."""

def calculate_discount(price: float, customer_tier: str, is_promotional: bool = False) -> float:
    if price < 0:
        raise ValueError("Price cannot be negative")
    
    discount = 0.0
    if customer_tier == "VIP":
        discount = 0.20
    elif customer_tier == "REGULAR":
        discount = 0.05
    else:
        discount = 0.0
        
    if is_promotional and price > 100.0:
        discount += 0.05
        
    return round(price * (1.0 - discount), 2)


class SubscriptionManager:
    def __init__(self, plan_name: str):
        self.plan_name = plan_name
        self.active = True

    def cancel(self) -> bool:
        if not self.active:
            return False
        self.active = False
        return True
'''

SAMPLE_VALID_TESTS = '''
import pytest
from billing import calculate_discount, SubscriptionManager

def test_negative_price():
    with pytest.raises(ValueError, match="Price cannot be negative"):
        calculate_discount(-10.0, "VIP")

def test_vip_discount():
    res = calculate_discount(100.0, "VIP")
    assert res == 80.0

def test_promotional_regular():
    res = calculate_discount(200.0, "REGULAR", is_promotional=True)
    # 0.05 + 0.05 = 0.10 discount => 200 * 0.9 = 180.0
    assert res == 180.0

def test_subscription_cancel():
    sub = SubscriptionManager("pro")
    assert sub.cancel() is True
    assert sub.cancel() is False
'''


def test_ast_symbol_extraction():
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(SAMPLE_MODULE_CODE)
        temp_path = Path(f.name)

    try:
        symbols = extract_symbols_from_file(temp_path)

        # Verify functions
        fn_map = {fn.name: fn for fn in symbols.functions}
        assert "calculate_discount" in fn_map
        calc_fn = fn_map["calculate_discount"]
        assert "price" in calc_fn.args
        assert "customer_tier" in calc_fn.args
        assert calc_fn.cyclomatic_complexity >= 4
        assert len(calc_fn.branch_lines) > 0

        # Verify classes
        assert len(symbols.classes) == 1
        cls = symbols.classes[0]
        assert cls.name == "SubscriptionManager"
        method_names = [m.name for m in cls.methods]
        assert "__init__" in method_names
        assert "cancel" in method_names
    finally:
        temp_path.unlink(missing_ok=True)


def test_validate_test_ast_security():
    safe_code = "def test_ok(): assert 1 == 1"
    valid, err = validate_test_ast(safe_code)
    assert valid is True
    assert err is None

    unsafe_code = "import os\ndef test_bad(): os.system('rm -rf /')"
    valid, err = validate_test_ast(unsafe_code)
    assert valid is False
    assert "Security violation" in err


def test_unit_agent_end_to_end_synthesis_and_sandbox_execution():
    adapter = DeterministicMockAdapter()
    adapter.set_next_response(f"```python\n{SAMPLE_VALID_TESTS}\n```")
    agent = UnitTestingAgent(model_adapter=adapter)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        module_file = tmppath / "billing.py"
        module_file.write_text(SAMPLE_MODULE_CODE, encoding="utf-8")

        # 1. Analyze AST
        symbols = agent.analyze_module(module_file)
        assert len(symbols.functions) == 1

        # 2. Synthesize Unit Tests
        suite = agent.synthesize_unit_tests(module_file, symbols)
        assert suite.is_valid_ast is True
        assert "calculate_discount" in suite.functions_covered

        # 3. Execute inside Deterministic Sandbox
        evidence, report, success = agent.execute_and_verify(
            project_dir=tmppath,
            target_file=module_file,
            synthesized_suite=suite,
        )

        assert success is True
        assert evidence.exit_code == 0
        assert report is not None
        assert report.status == TestStatus.PASSED
        assert report.total_tests == 4
        assert report.passed_tests == 4
        assert evidence.coverage_percentage is not None
        assert evidence.coverage_percentage > 90.0
