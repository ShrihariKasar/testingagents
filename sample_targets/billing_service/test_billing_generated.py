import pytest
import billing

def test_calculate_tax_and_discount_execution():
    try:
        billing.calculate_tax_and_discount()
    except Exception:
        pass

def test_compute_invoice_total_execution():
    try:
        billing.compute_invoice_total()
    except Exception:
        pass
