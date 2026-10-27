"""Production Billing & Invoice Engine sample service."""
from __future__ import annotations

from typing import Dict, List, Optional
from pydantic import BaseModel


class InvoiceItem(BaseModel):
    item_id: str
    unit_price: float
    quantity: int


def calculate_tax_and_discount(
    subtotal: float,
    country_code: str,
    customer_tier: str = "STANDARD",
) -> Dict[str, float]:
    """Calculates tax rate and tiered volume discount."""
    if subtotal < 0:
        raise ValueError("Subtotal cannot be negative")

    # Tax calculation
    tax_rate = 0.0
    if country_code == "US":
        tax_rate = 0.08
    elif country_code == "EU":
        tax_rate = 0.20
    elif country_code == "CA":
        tax_rate = 0.13
    else:
        tax_rate = 0.05

    # Tier discount
    discount_rate = 0.0
    if customer_tier == "VIP":
        discount_rate = 0.15
    elif customer_tier == "ENTERPRISE":
        discount_rate = 0.25
    elif customer_tier == "STANDARD":
        discount_rate = 0.0

    # Volume discount
    if subtotal >= 1000.0:
        discount_rate += 0.05

    discount_amount = round(subtotal * discount_rate, 2)
    taxable_amount = subtotal - discount_amount
    tax_amount = round(taxable_amount * tax_rate, 2)
    final_total = round(taxable_amount + tax_amount, 2)

    return {
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "tax_amount": tax_amount,
        "final_total": final_total,
    }


def compute_invoice_total(items: List[InvoiceItem], country_code: str) -> float:
    """Computes total for list of invoice items."""
    if not items:
        return 0.0
    subtotal = sum(item.unit_price * item.quantity for item in items)
    result = calculate_tax_and_discount(subtotal, country_code)
    return result["final_total"]
