"""Policy checks as plain Python. Each returns computed FACTS so no LLM ever does arithmetic.

Every check returns the same shape:
    {"check": str, "passed": bool, "reason_code": str | None,
     "findings": [str, ...], "citations": [id, ...], "details": {...}}
"""
import re

from app.tools import data, docs

PRICE_TOLERANCE = 0.02     # policy 3.3: +/- 2%
GST_TOTAL_TOLERANCE = 1.0  # policy 3.5: within Rs 1
DUPLICATE_DAYS = 7         # policy 4.1
BANK_CHANGE_DAYS = 30      # policy 5.1
HIGH_VALUE = 500000.0      # policy 5.1 / 8.2: Rs 5,00,000


def _result(check, passed, reason_code, findings, citations, details=None):
    return {
        "check": check,
        "passed": passed,
        "reason_code": None if passed else reason_code,
        "findings": findings,
        "citations": citations,
        "details": details or {},
    }


def _norm_invoice_no(s: str) -> str:
    """Policy 4.1: ignore spaces, dashes, slashes and capitals. 'ABC-1042' == 'abc1042'."""
    return re.sub(r"[\s\-/]", "", s or "").lower()


# ---------- Documents present (policy 6.1, 6.2) ----------

def check_po_exists(invoice_id: str) -> dict:
    inv = data.get_invoice(invoice_id)
    po = data.get_purchase_order(inv["po_id"])
    if po is None:
        return _result("po_exists", False, "UNKNOWN_PO",
                       [f"PO {inv['po_id']} on invoice {invoice_id} does not exist in our system."],
                       [invoice_id, inv["po_id"], "AP-POL-6.2"])
    return _result("po_exists", True, None, [f"PO {po['po_id']} exists."], [po["po_id"]])


def check_grn_exists(invoice_id: str) -> dict:
    inv = data.get_invoice(invoice_id)
    grns = data.get_goods_receipts(inv["po_id"])
    if not grns:
        return _result("grn_exists", False, "MISSING_GRN",
                       [f"No goods receipt recorded for {inv['po_id']}; delivery cannot be confirmed."],
                       [inv["po_id"], "AP-POL-6.1"])
    ids = [g["grn_id"] for g in grns]
    return _result("grn_exists", True, None, [f"GRN(s) found: {', '.join(ids)}."], ids)


# ---------- Line matching (policy 3.2, 3.3, 3.4, 3.5) ----------

def check_quantity(invoice_id: str) -> dict:
    """Invoice qty must not exceed qty received on the GRN(s) for that PO line. No tolerance."""
    inv = data.get_invoice(invoice_id)
    grns = data.get_goods_receipts(inv["po_id"])
    received: dict[int, int] = {}
    for g in grns:
        for gl in g["lines"]:
            received[gl["po_line_no"]] = received.get(gl["po_line_no"], 0) + gl["qty_received"]
    grn_ids = [g["grn_id"] for g in grns]
    findings, bad_lines, lines = [], [], []
    for l in inv["lines"]:
        got = received.get(l["po_line_no"], 0)
        ok = l["qty"] <= got
        lines.append({"line_id": l["line_id"], "invoiced_qty": l["qty"], "received_qty": got, "ok": ok})
        if not ok:
            bad_lines.append(l["line_id"])
            findings.append(f"{l['line_id']}: invoiced {l['qty']} but received {got} "
                            f"(short by {l['qty'] - got}).")
    passed = not bad_lines
    if passed:
        findings = ["All invoiced quantities are within quantities received."]
    return _result("quantity", passed, "QTY_SHORT", findings,
                   bad_lines + grn_ids + ([] if passed else ["AP-POL-3.2"]), {"lines": lines})


def check_price(invoice_id: str) -> dict:
    """Unit price within +/-2% of PO price, or of a SIGNED revision effective on the invoice date."""
    inv = data.get_invoice(invoice_id)
    po = data.get_purchase_order(inv["po_id"])
    po_lines = {pl["line_no"]: pl for pl in po["lines"]}
    agreements = {a["item_code"]: a for a in docs.get_price_agreements(inv["vendor_id"]) if a["signed"]}
    findings, bad_lines, used_agreements, lines = [], [], [], []
    for l in inv["lines"]:
        pl = po_lines.get(l["po_line_no"])
        if pl is None:
            bad_lines.append(l["line_id"])
            findings.append(f"{l['line_id']}: PO line {l['po_line_no']} not found on {po['po_id']}.")
            continue
        ref_price, ref_source = pl["unit_price"], f"{po['po_id']} line {pl['line_no']}"
        agr = agreements.get(l["item_code"])
        if agr and inv["invoice_date"] >= agr["effective_from"]:
            ref_price, ref_source = agr["revised_unit_price"], agr["agreement_id"]
            used_agreements.append(agr["agreement_id"])
        variance = (l["unit_price"] - ref_price) / ref_price
        ok = abs(variance) <= PRICE_TOLERANCE + 1e-9
        lines.append({"line_id": l["line_id"], "invoiced_price": l["unit_price"],
                      "expected_price": ref_price, "price_source": ref_source,
                      "variance_pct": round(variance * 100, 2), "ok": ok})
        if not ok:
            bad_lines.append(l["line_id"])
            findings.append(f"{l['line_id']}: invoiced Rs {l['unit_price']:.2f} vs expected "
                            f"Rs {ref_price:.2f} ({ref_source}), variance {variance*100:+.2f}%.")
    passed = not bad_lines
    if passed:
        findings = ["All unit prices are within the 2% band."]
        if used_agreements:
            findings.append(f"Signed price revision(s) applied: {', '.join(sorted(set(used_agreements)))}.")
    cites = bad_lines + [po["po_id"]] + sorted(set(used_agreements))
    if used_agreements:
        # cite the invoice lines priced against an agreement (the evidence for a clean match)
        cites += [x["line_id"] for x in lines if x["price_source"].startswith("AGR")]
        cites.append("AP-POL-3.4")
    if not passed:
        cites.append("AP-POL-3.3")
    return _result("price", passed, "PRICE_VARIANCE", findings, list(dict.fromkeys(cites)), {"lines": lines})


def check_gst(invoice_id: str) -> dict:
    """Line GST rate must equal item master rate; header GST must equal sum of line GST within Rs 1."""
    inv = data.get_invoice(invoice_id)
    findings, bad_lines, lines = [], [], []
    for l in inv["lines"]:
        item = data.get_item(l["item_code"])
        master = item["gst_rate"] if item else None
        ok = master is not None and float(l["gst_rate"]) == float(master)
        lines.append({"line_id": l["line_id"], "invoiced_rate": l["gst_rate"], "master_rate": master, "ok": ok})
        if not ok:
            bad_lines.append(l["line_id"])
            findings.append(f"{l['line_id']}: GST {l['gst_rate']}% charged but item master rate for "
                            f"{l['item_code']} is {master}%.")
    line_sum = round(sum(l["gst_amount"] for l in inv["lines"]), 2)
    header_ok = abs(line_sum - inv["gst_amount"]) <= GST_TOTAL_TOLERANCE
    if not header_ok:
        findings.append(f"Invoice GST total Rs {inv['gst_amount']:.2f} != sum of line GST Rs {line_sum:.2f}.")
    passed = not bad_lines and header_ok
    if passed:
        findings = ["GST rates match the item master and the GST total adds up."]
    cites = bad_lines + ([] if header_ok else [invoice_id]) + ([] if passed else ["AP-POL-3.5"])
    return _result("gst", passed, "GST_MISMATCH", findings, cites,
                   {"lines": lines, "header_gst": inv["gst_amount"], "line_gst_sum": line_sum})


# ---------- Duplicates (policy 4.1) ----------

def check_duplicate(invoice_id: str) -> dict:
    inv = data.get_invoice(invoice_id)
    me = _norm_invoice_no(inv["vendor_invoice_no"])
    matches = []
    for other in data.get_vendor_invoices(inv["vendor_id"]):
        if other["invoice_id"] == invoice_id or abs(other["total"] - inv["total"]) > 0.005:
            continue
        # "already in our books": the other one is paid, or was recorded on/before this one
        already = other["payment_status"] == "paid" or other["invoice_date"] <= inv["invoice_date"]
        if not already:
            continue
        same_no = _norm_invoice_no(other["vendor_invoice_no"]) == me
        gap = abs(docs.days_between(other["invoice_date"], inv["invoice_date"]))
        if same_no or gap <= DUPLICATE_DAYS:
            matches.append({**other, "same_invoice_no": same_no, "days_apart": gap})
    if not matches:
        return _result("duplicate", True, None, ["No earlier invoice with the same vendor and total."], [])
    findings = [
        f"Same vendor and total (Rs {inv['total']:.2f}) as {m['invoice_id']} "
        f"('{m['vendor_invoice_no']}' vs '{inv['vendor_invoice_no']}', {m['days_apart']} days apart"
        f"{', already paid ' + m['payment_id'] if m['payment_id'] else ''})."
        for m in matches
    ]
    cites = [invoice_id] + [m["invoice_id"] for m in matches] + [m["payment_id"] for m in matches if m["payment_id"]]
    return _result("duplicate", False, "DUPLICATE", findings, cites + ["AP-POL-4.1"], {"matches": matches})


# ---------- Vendor / fraud (policy 5.1, 5.2) ----------

def check_vendor(invoice_id: str) -> dict:
    """Blocked vendor (5.2) and recent bank-account change on a high-value invoice (5.1)."""
    inv = data.get_invoice(invoice_id)
    v = data.get_vendor(inv["vendor_id"])
    out = {"blocked": None, "bank_change": None}
    if v is None or (v["status"] or "").lower() == "blocked":
        out["blocked"] = _result("vendor_blocked", False, "VENDOR_BLOCKED",
                                 [f"Vendor {inv['vendor_id']} is {'unknown' if v is None else 'blocked'}."],
                                 [inv["vendor_id"], "AP-POL-5.2"])
    else:
        out["blocked"] = _result("vendor_blocked", True, None, [f"Vendor {v['vendor_id']} is active."], [])
    changed = v.get("bank_account_changed_on") if v else None
    days = docs.days_between(changed, inv["invoice_date"]) if changed else None
    risky = days is not None and 0 <= days <= BANK_CHANGE_DAYS and inv["total"] > HIGH_VALUE
    if risky:
        out["bank_change"] = _result("bank_change", False, "BANK_CHANGE_RISK",
                                     [f"Bank account changed on {changed}, {days} days before the invoice, "
                                      f"and total Rs {inv['total']:.2f} is above Rs 5,00,000."],
                                     [inv["vendor_id"], invoice_id, "AP-POL-5.1"], {"days_since_change": days})
    else:
        out["bank_change"] = _result("bank_change", True, None,
                                     [f"No bank change risk (changed {changed}, {days} days before invoice)."],
                                     [], {"days_since_change": days})
    return out


def run_all_checks(invoice_id: str) -> list[dict]:
    """Every check, in a safe order. Line checks are skipped when PO/GRN are missing (policy 6.3)."""
    v = check_vendor(invoice_id)
    results = [check_duplicate(invoice_id), v["blocked"], v["bank_change"]]
    po = check_po_exists(invoice_id)
    results.append(po)
    if po["passed"]:
        grn = check_grn_exists(invoice_id)
        results.append(grn)
        if grn["passed"]:
            results.append(check_quantity(invoice_id))
        results += [check_price(invoice_id), check_gst(invoice_id)]
    return results
