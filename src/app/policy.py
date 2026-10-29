"""AP policy section 7 and 8 as code: reason priority, decision mapping, approval rule."""

# Policy 7.1: main reason = first that applies in this order
REASON_PRIORITY = [
    "DUPLICATE", "VENDOR_BLOCKED", "BANK_CHANGE_RISK", "UNKNOWN_PO",
    "MISSING_GRN", "QTY_SHORT", "PRICE_VARIANCE", "GST_MISMATCH",
]

# Policy 7 table
DECISION_FOR = {
    "CLEAN_MATCH": "pay",
    "QTY_SHORT": "hold", "PRICE_VARIANCE": "hold", "GST_MISMATCH": "hold", "MISSING_GRN": "hold",
    "DUPLICATE": "escalate", "VENDOR_BLOCKED": "escalate",
    "BANK_CHANGE_RISK": "escalate", "UNKNOWN_PO": "escalate",
}

HIGH_VALUE = 500000.0


def main_reason(reason_codes: list[str]) -> tuple[str, list[str]]:
    """Returns (main_reason, other_reasons) following policy 7.1."""
    found = [r for r in REASON_PRIORITY if r in set(reason_codes)]
    if not found:
        return "CLEAN_MATCH", []
    return found[0], found[1:]


def needs_human(decision: str, amount: float | None) -> bool:
    """Policy 8.1 + 8.2: every hold/escalate, and any payment above Rs 5,00,000."""
    return decision != "pay" or (amount or 0) > HIGH_VALUE
