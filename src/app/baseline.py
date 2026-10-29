"""Rules-only decision (no LLM). Used as (1) a reference answer while building and
(2) the guardrail the validator compares agent decisions against."""
from app import policy
from app.tools import checks, data


def decide(invoice_id: str) -> dict:
    inv = data.get_invoice(invoice_id)
    if inv is None:
        raise ValueError(f"Invoice {invoice_id} not found")
    results = checks.run_all_checks(invoice_id)
    failed = [r for r in results if not r["passed"]]
    main, others = policy.main_reason([r["reason_code"] for r in failed])
    decision = policy.DECISION_FOR[main]

    citations = [invoice_id]
    if main == "CLEAN_MATCH":
        for r in results:
            citations += r["citations"]
        citations.append("AP-POL-3.1")
    else:
        for r in failed:
            citations += r["citations"]
        # PO and GRN are always part of the evidence when they exist
        for r in results:
            if r["check"] in ("po_exists", "grn_exists") and r["passed"]:
                citations += r["citations"]
    citations.append("AP-POL-7")

    line_details = []
    for r in results:
        for l in r["details"].get("lines", []):
            line_details.append({"check": r["check"], **l})

    return {
        "invoice_id": invoice_id,
        "decision": decision,
        "reason_code": main,
        "other_reasons": others,
        "amount": round(inv["total"], 2),
        "citations": list(dict.fromkeys(citations)),
        "needs_human": policy.needs_human(decision, inv["total"]),
        "findings": [f for r in failed for f in r["findings"]] or ["Clean three-way match."],
        "lines": line_details,
    }
