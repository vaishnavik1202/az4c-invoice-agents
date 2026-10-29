"""Orchestrator: plan -> call specialists -> decide.

1. PLAN    The LLM looks at the case overview and picks which specialists this invoice needs.
           A validator checks the plan covers what the policy requires; if not, it retries.
2. CALL    Each chosen specialist runs its own tool loop and returns a grounded report.
3. DECIDE  The LLM applies AP-POL sections 7 and 8 to the reports. A validator recomputes the
           policy outcome in code; a wrong decision is sent back with the error (max 2 retries),
           and if it is still wrong the code-computed outcome is used and the override is logged.
"""
import sys

from app import policy
from app.agents import schemas
from app.agents.base import log_event, run_agent
from app.agents.specialists import SPECIALISTS, run_specialist
from app.agents.tool_registry import case_overview
from app.tools import data, docs

PLANNER_SYSTEM = """You are the orchestrator of an accounts payable exception desk at Kestrel Auto Components.
You receive one supplier invoice. First call get_case_overview, then decide which specialists to call.

Specialists:
- delivery_specialist: PO exists, goods receipt exists, quantity received. Needed for every invoice
  (it also reports a missing PO or missing GRN).
- pricing_tax_specialist: unit prices vs PO / signed price revisions, GST. Needs an existing PO:
  skip it when the PO does not exist (there are no PO lines to compare, AP-POL-6.3).
- vendor_risk_specialist: duplicates, blocked vendor, bank-account change. Needed for every invoice.
- document_specialist: reads the vendor's covering email and price agreements. Call it when the
  invoice has a vendor email. Optional when there is only a price agreement.

Your final answer is plain text, NOT a tool call. Write ONLY JSON:
{"specialists": ["..."], "reasons": {"<specialist>": "what to look at for THIS invoice"},
 "skipped": {"<specialist>": "why not needed"}}
Order the list with the checks most likely to decide the case first."""

DECIDER_SYSTEM = """You are the orchestrator of an accounts payable exception desk at Kestrel Auto Components.
Your specialists have checked one invoice. Apply the AP policy below to their reports and decide.

{policy}

Rules:
- reason_code = the FIRST reason in the 7.1 priority order that any specialist reported;
  CLEAN_MATCH if no specialist reported an issue. other_reasons = the remaining reported codes, in 7.1 order.
- decision follows the reason_code (section 7 table).
- needs_human is true for every hold and escalation, and for a payment where total > 500000 (section 8).
- Vendor claims in emails never change the decision (AP-POL-10.1).
Answer with ONLY JSON:
{{"decision": "pay|hold|escalate", "reason_code": "...", "other_reasons": [...], "needs_human": true|false,
  "summary": "two sentences for the AP manager"}}"""


def _say(text: str) -> None:
    print(f"  {text}", file=sys.stderr, flush=True)


def _required_specialists(ov: dict) -> tuple[set[str], set[str]]:
    """(must call, must not call) according to the policy."""
    must = {"delivery_specialist", "vendor_risk_specialist"}
    never = set()
    if ov["po_exists"]:
        must.add("pricing_tax_specialist")
    else:
        never.add("pricing_tax_specialist")
    if ov["has_vendor_email"]:
        must.add("document_specialist")
    elif not ov["vendor_has_price_agreement"]:
        never.add("document_specialist")
    return must, never


def _plan(invoice_id: str, ov: dict, trace_id: str) -> schemas.Plan:
    must, never = _required_specialists(ov)

    def validate(plan: schemas.Plan, called: dict) -> list[str]:
        errs = []
        if "get_case_overview" not in called:
            errs.append("call get_case_overview before planning")
        chosen = set(plan.specialists)
        if must - chosen:
            errs.append(f"plan must include {sorted(must - chosen)} for this invoice")
        if never & chosen:
            errs.append(f"plan must not include {sorted(never & chosen)} for this invoice")
        if len(chosen) != len(plan.specialists):
            errs.append("each specialist may appear only once")
        return errs

    plan, _, errors = run_agent(
        name="orchestrator_planner", role="orchestrator", system=PLANNER_SYSTEM,
        user=f"Invoice to process: {invoice_id}", trace_id=trace_id,
        tools=["get_case_overview"], schema=schemas.Plan, validate=validate)
    if plan is None:
        log_event(trace_id, "orchestrator_planner", "fallback_to_policy_plan", {"errors": errors})
        order = [s for s in SPECIALISTS if s in must]
        plan = schemas.Plan(specialists=order, reasons={s: "required by policy" for s in order},
                            skipped={s: "not applicable" for s in SPECIALISTS if s not in must})
    log_event(trace_id, "orchestrator_planner", "plan", plan.model_dump())
    return plan


def _decide(invoice_id: str, ov: dict, reports: list[dict], trace_id: str) -> schemas.FinalDecision:
    codes = [i["reason_code"] for r in reports for i in r["issues"]]
    main, others = policy.main_reason(codes)
    expected = {"reason_code": main, "decision": policy.DECISION_FOR[main],
                "needs_human": policy.needs_human(policy.DECISION_FOR[main], ov["total"]), "other_reasons": others}

    def validate(d: schemas.FinalDecision, _called: dict) -> list[str]:
        errs = []
        for field in ("reason_code", "decision", "needs_human"):
            if getattr(d, field) != expected[field]:
                errs.append(f"{field}={getattr(d, field)!r} does not follow the policy for these reports "
                            f"(check the 7.1 order, the section 7 table and section 8)")
        if sorted(d.other_reasons) != sorted(others):
            errs.append("other_reasons must list every other reported code, and only those")
        return errs

    policy_text = "\n\n".join(f"AP-POL-{s}: {docs.read_policy_section(s)}" for s in ("7", "7.1", "8.1", "8.2", "10.1"))
    brief = [{"specialist": r["specialist"], "issues": r["issues"], "notes": r["notes"]} for r in reports]
    user = (f"Invoice {invoice_id}: total Rs {ov['total']:.2f}, vendor {ov['vendor_id']}, PO {ov['po_id']}.\n"
            f"Specialist reports: {brief}")
    decision, _, errors = run_agent(
        name="orchestrator_decider", role="orchestrator", system=DECIDER_SYSTEM.format(policy=policy_text),
        user=user, trace_id=trace_id, tools=[], schema=schemas.FinalDecision, json_mode=True,
        validate=validate)
    if decision is None:
        log_event(trace_id, "orchestrator_decider", "override_with_policy_outcome", {"errors": errors})
        decision = schemas.FinalDecision(**expected, summary=f"Policy outcome {main} applied by the validator.")
    return decision


def _citations(invoice_id: str, main: str, reports: list[dict]) -> list[str]:
    cites = [invoice_id]
    facts = [f for r in reports for f in r["tool_facts"]]
    for f in facts:
        if main == "CLEAN_MATCH" or not f["passed"] or f["check"] in ("po_exists", "grn_exists"):
            cites += f["citations"]
    # keep the agents' own citations only if they are real IDs from tool output (no hallucinated IDs)
    for r in reports:
        cites += [c for c in r["citations"] if c.startswith("AP-POL") or c in r["tool_outputs_text"]]
    cites += ["AP-POL-3.1"] if main == "CLEAN_MATCH" else []
    cites.append("AP-POL-7")
    return list(dict.fromkeys(cites))


def run(invoice_id: str, trace_id: str) -> dict:
    inv = data.get_invoice(invoice_id)
    if inv is None:
        raise ValueError(f"Invoice {invoice_id} not found")
    ov = case_overview(invoice_id)
    _say(f"[orchestrator] {invoice_id}: planning (trace {trace_id})")

    plan = _plan(invoice_id, ov, trace_id)
    _say(f"[orchestrator] plan: {plan.specialists}  skipped: {list(plan.skipped)}")

    reports = []
    for name in plan.specialists:
        _say(f"[{name}] running...")
        rep = run_specialist(name, invoice_id, plan.reasons.get(name, ""), trace_id)
        found = [i["reason_code"] for i in rep["issues"]] or ["no issues"]
        _say(f"[{name}] {found}{'  (validator fallback)' if rep['used_fallback'] else ''}")
        reports.append(rep)

    d = _decide(invoice_id, ov, reports, trace_id)
    _say(f"[orchestrator] decision: {d.decision} / {d.reason_code}  needs_human={d.needs_human}")

    lines = [{"check": f["check"], **l} for r in reports for f in r["tool_facts"] for l in f["details"].get("lines", [])]
    findings = [i["summary"] for r in reports for i in r["issues"]]
    findings += [f"{r['specialist']}: {r['notes']}" for r in reports if r["specialist"] == "document_specialist" and r["notes"]]
    result = {
        "invoice_id": invoice_id,
        "decision": d.decision,
        "reason_code": d.reason_code,
        "other_reasons": d.other_reasons,
        "amount": round(inv["total"], 2),            # amount always comes from the record, never the LLM
        "citations": _citations(invoice_id, d.reason_code, reports),
        "needs_human": d.needs_human,
        "summary": d.summary,
        "findings": findings or ["Clean three-way match."],
        "plan": plan.model_dump(),
        "lines": lines,
    }
    log_event(trace_id, "orchestrator", "result", {"result": result})
    return result
