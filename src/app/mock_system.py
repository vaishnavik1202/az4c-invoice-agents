"""Writes to the mock ERP (mock_system/). Called only after the decision is final."""
import json
from datetime import datetime, timezone

from app.config import MOCK_SYSTEM_DIR
from app.tools import data

STATUS_FOR = {"pay": "released_for_payment", "hold": "on_hold", "escalate": "escalated"}


def save_decision(result: dict) -> list[str]:
    MOCK_SYSTEM_DIR.mkdir(exist_ok=True)
    written = []
    record = {
        "invoice_id": result["invoice_id"],
        "status": STATUS_FOR[result["decision"]],
        "decision": result["decision"],
        "reason": result["reason_code"],
        "other_reasons": result.get("other_reasons", []),
        "amount": result["amount"],
        "needs_human": result["needs_human"],
        "trace_id": result.get("trace_id"),
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path = MOCK_SYSTEM_DIR / "erp_payment_status.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    written.append(str(path))

    if result["decision"] == "hold":  # policy 9.1: tell the vendor; escalations stay internal
        written.append(_write_vendor_email(result))
    return written


def _write_vendor_email(result: dict) -> str:
    inv = data.get_invoice(result["invoice_id"])
    vendor = data.get_vendor(inv["vendor_id"])
    ask = {
        "QTY_SHORT": "a credit note for the quantity not received, or proof of delivery",
        "PRICE_VARIANCE": "a corrected invoice at the agreed price, or a credit note for the difference",
        "GST_MISMATCH": "a corrected tax invoice with the correct GST rate",
        "MISSING_GRN": "proof of delivery (delivery challan signed by our stores)",
    }.get(result["reason_code"], "a corrected invoice")
    bad = [l for l in result.get("lines", []) if not l.get("ok", True)]
    rows = []
    for l in bad:
        if l["check"] == "quantity":
            rows.append(f"| {l['line_id']} | Quantity | {l['received_qty']} received | {l['invoiced_qty']} invoiced |")
        elif l["check"] == "price":
            rows.append(f"| {l['line_id']} | Unit price | Rs {l['expected_price']:.2f} | Rs {l['invoiced_price']:.2f} |")
        elif l["check"] == "gst":
            rows.append(f"| {l['line_id']} | GST rate | {l['master_rate']}% | {l['invoiced_rate']}% |")
    table = ("| Line | Check | We expected | You invoiced |\n|---|---|---|---|\n" + "\n".join(rows)) if rows else ""
    body = f"""**To:** {vendor['name'] if vendor else inv['vendor_id']}
**Subject:** Invoice {inv['vendor_invoice_no']} ({result['invoice_id']}) on hold

Dear {vendor['name'] if vendor else 'Supplier'},

Your invoice {inv['vendor_invoice_no']} against {inv['po_id']} for Rs {inv['total']:,.2f} is on hold.

Reason: {result['reason_code']}. {' '.join(result.get('findings', []))}

{table}

Please send {ask} so we can release the payment.

Regards,
Accounts Payable, Kestrel Auto Components Pvt Ltd
"""
    out = MOCK_SYSTEM_DIR / "outbox"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{result['invoice_id']}.md"
    path.write_text(body, encoding="utf-8")
    return str(path)
