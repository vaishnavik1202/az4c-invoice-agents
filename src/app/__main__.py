"""CLI entry point.

    python -m app run  --case samples/UC1-S01.json --out results/UC1-S01.json [--mode rules|agents]
    python -m app eval --cases samples/ [--mode rules|agents]
    python -m app check --invoice INV-21834      # debug: print every policy check's facts
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

from app import baseline, evaluate, mock_system, usage
from app.config import LOGS_DIR

RESULT_FIELDS = ["case_id", "decision", "reason_code", "other_reasons", "amount",
                 "citations", "needs_human", "trace_id", "summary", "findings", "plan", "usage", "lines"]


def run_case(case_path: Path, mode: str, write_mock: bool = True) -> dict:
    case = json.loads(case_path.read_text(encoding="utf-8"))
    invoice_id = case["input"]["invoice_id"]
    trace_id = f"run-{uuid.uuid4().hex[:12]}"

    if mode == "rules":
        res = baseline.decide(invoice_id)
    else:
        from app.agents.orchestrator import run as run_agents
        res = run_agents(invoice_id, trace_id=trace_id)

    res["case_id"] = case["case_id"]
    res["trace_id"] = trace_id
    res["usage"] = usage.for_trace(trace_id)
    if write_mock:
        mock_system.save_decision(res)
    return {k: res[k] for k in RESULT_FIELDS if k in res}


def cmd_run(a):
    result = run_case(Path(a.case), a.mode)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"{result['case_id']}: {result['decision']} / {result['reason_code']} "
          f"(amount {result['amount']}, needs_human={result['needs_human']}) -> {out}")
    u = result["usage"]
    print(f"  usage: {u['calls']} model calls, {u['tokens']:,} tokens, {u['model_seconds']}s model time")


def cmd_eval(a):
    folder = Path(a.cases)
    exp_dir = folder / "expected"
    cases = sorted(p for p in folder.glob("*.json"))
    if not cases:
        sys.exit(f"No case files in {folder}")
    out_dir = Path("results") / folder.name
    out_dir.mkdir(parents=True, exist_ok=True)
    passed = 0
    field_hits: dict[str, list[int]] = {}
    rows = []
    all_usage: list[dict] = []
    for c in cases:
        exp_file = exp_dir / c.name
        try:
            result = run_case(c, a.mode)
        except Exception as e:  # one broken case must not stop the eval
            result, err = {}, e
        else:
            err = None
        (out_dir / c.name).write_text(json.dumps(result, indent=2), encoding="utf-8")
        if not exp_file.exists():
            print(f"  {c.stem}: no expected file, skipped scoring")
            continue
        s = evaluate.score(result, json.loads(exp_file.read_text(encoding="utf-8")))
        passed += s["passed"]
        for k, v in s["checks"].items():
            key = k.split(":")[0]
            field_hits.setdefault(key, [0, 0])
            field_hits[key][0] += v
            field_hits[key][1] += 1
        mark = "PASS" if s["passed"] else "FAIL"
        u = result.get("usage") or {}
        all_usage.append(u)
        detail = "" if s["passed"] else "  failed: " + ", ".join(k for k, v in s["checks"].items() if not v)
        if err:
            detail += f"  error: {err}"
        print(f"  {mark}  {c.stem}: {result.get('decision')} / {result.get('reason_code')}"
              f"  [{u.get('calls', 0)} calls, {u.get('tokens', 0):,} tokens]{detail}")
        rows.append(f"| {c.stem} | {mark} | {result.get('decision')} | {result.get('reason_code')} | "
                    f"{', '.join(result.get('plan', {}).get('specialists', [])) or '-'} | "
                    f"{u.get('calls', 0)} | {u.get('tokens', 0):,} | "
                    f"{', '.join(k for k, v in s['checks'].items() if not v) or '-'} |")
    total = sum(1 for c in cases if (exp_dir / c.name).exists())
    print(f"\nScore: {passed}/{total} cases fully correct")
    for k, (h, n) in field_hits.items():
        print(f"  {k:12s} {h}/{n}")
    tot_calls = sum(u.get("calls", 0) for u in all_usage)
    tot_tokens = sum(u.get("tokens", 0) for u in all_usage)
    n_cases = max(len(all_usage), 1)
    print(f"\nUsage: {tot_calls} model calls, {tot_tokens:,} tokens "
          f"(avg {tot_calls / n_cases:.1f} calls, {tot_tokens / n_cases:,.0f} tokens per case)")
    if a.score_file:
        from datetime import date
        md = [f"# Score: {passed}/{total} cases fully correct", "",
              f"Mode: `{a.mode}` · Cases: `{folder}` · Date: {date.today().isoformat()}", "",
              "| Field | Correct |", "|---|---|"]
        md += [f"| {k} | {h}/{n} |" for k, (h, n) in field_hits.items()]
        md += ["", f"Usage: {tot_calls} model calls, {tot_tokens:,} tokens "
                   f"(avg {tot_calls / n_cases:.1f} calls and {tot_tokens / n_cases:,.0f} tokens per case)", "",
               "| Case | Result | Decision | Reason | Specialists called | Model calls | Tokens | Failed fields |",
               "|---|---|---|---|---|---|---|---|"] + rows
        Path(a.score_file).parent.mkdir(parents=True, exist_ok=True)
        Path(a.score_file).write_text("\n".join(md) + "\n", encoding="utf-8")
        print(f"Score table written to {a.score_file}")


def cmd_check(a):
    from app.tools import checks
    for r in checks.run_all_checks(a.invoice):
        print(f"[{'PASS' if r['passed'] else 'FAIL'}] {r['check']}: " + " | ".join(r["findings"]))


def cmd_ping(a):
    """Test the LLM wrapper: one logged call per role."""
    from app import llm
    trace_id = f"ping-{uuid.uuid4().hex[:8]}"
    for role in ("orchestrator", "specialist"):
        msg = llm.chat(agent=f"ping_{role}", role=role, trace_id=trace_id,
                       messages=[{"role": "user", "content": "Reply with exactly: OK"}])
        print(f"{role:12s} -> {(msg.content or '').strip()}")
    print(f"Log written to {LOGS_DIR / (trace_id + '.jsonl')}")


def cmd_models(a):
    """Show which models each role will use, in order, on this account."""
    from app import llm
    for role, chain in llm.available_routes().items():
        print(f"{role}:")
        for provider, model in chain:
            print(f"   {provider}: {model}")


def cmd_usage(a):
    """Token usage today across all runs (Groq free tier: daily quota per model)."""
    usage.print_table(usage.today(), "Today (UTC)")


def main():
    p = argparse.ArgumentParser(prog="python -m app")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one case end to end")
    r.add_argument("--case", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--mode", choices=["agents", "rules"], default="agents")
    r.set_defaults(fn=cmd_run)
    e = sub.add_parser("eval", help="run every case in a folder and score against expected/")
    e.add_argument("--cases", required=True)
    e.add_argument("--mode", choices=["agents", "rules"], default="agents")
    e.add_argument("--score-file", help="also write the score table as Markdown, e.g. tests/SCORE.md")
    e.set_defaults(fn=cmd_eval)
    c = sub.add_parser("check", help="debug: print policy check facts for one invoice")
    c.add_argument("--invoice", required=True)
    c.set_defaults(fn=cmd_check)
    pg = sub.add_parser("ping", help="test both models through the logged LLM wrapper")
    pg.set_defaults(fn=cmd_ping)
    md = sub.add_parser("models", help="show the model chain each role uses on this account")
    md.set_defaults(fn=cmd_models)
    us = sub.add_parser("usage", help="tokens and calls used today, per model")
    us.set_defaults(fn=cmd_usage)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
