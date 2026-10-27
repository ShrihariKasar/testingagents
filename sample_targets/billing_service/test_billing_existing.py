"""Existing regression tests for Billing Service."""
import pytest
from billing import calculate_tax_and_discount, compute_invoice_total, InvoiceItem


def test_standard_us_billing():
    res = calculate_tax_and_discount(100.0, "US", "STANDARD")
    assert res["subtotal"] == 100.0
    assert res["discount_amount"] == 0.0
    assert res["tax_amount"] == 8.0
    assert res["final_total"] == 108.0


def test_vip_eu_billing():
    res = calculate_tax_and_discount(100.0, "EU", "VIP")
    # 15% discount => taxable = 85.0, 20% tax => 17.0, final = 102.0
    assert res["discount_amount"] == 15.0
    assert res["tax_amount"] == 17.0
    assert res["final_total"] == 102.0


def test_negative_subtotal_raises():
    with pytest.raises(ValueError, match="Subtotal cannot be negative"):
        calculate_tax_and_discount(-50.0, "US")


def test_compute_invoice_empty():
    assert compute_invoice_total([], "US") == 0.0
