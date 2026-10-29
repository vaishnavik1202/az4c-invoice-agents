"""Token and call usage, read back from the trace logs (logs/<trace_id>.jsonl)."""
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from app.config import LOGS_DIR


def _calls(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if "model" in r and "event" not in r:
            out.append(r)
    return out


def summarise(records: list[dict]) -> dict:
    by_model = defaultdict(lambda: {"calls": 0, "failed": 0, "tokens": 0})
    for r in records:
        m = by_model[r["model"]]
        m["calls"] += 1
        if r.get("error"):
            m["failed"] += 1
        m["tokens"] += (r.get("tokens") or {}).get("total") or 0
    return {
        "calls": sum(m["calls"] for m in by_model.values()),
        "failed_calls": sum(m["failed"] for m in by_model.values()),
        "tokens": sum(m["tokens"] for m in by_model.values()),
        "model_seconds": round(sum(r.get("latency_ms") or 0 for r in records) / 1000, 1),
        "by_model": dict(by_model),
    }


def for_trace(trace_id: str) -> dict:
    p = LOGS_DIR / f"{trace_id}.jsonl"
    return summarise(_calls(p)) if p.exists() else summarise([])


def today() -> dict:
    """Usage across all logs today (UTC, which is how Groq counts its daily quota)."""
    day = datetime.now(timezone.utc).date().isoformat()
    recs = [r for p in LOGS_DIR.glob("*.jsonl") for r in _calls(p) if r.get("ts", "").startswith(day)]
    return summarise(recs)


def print_table(s: dict, title: str) -> None:
    print(f"\n{title}: {s['calls']} model calls ({s['failed_calls']} failed/retried), "
          f"{s['tokens']:,} tokens, {s['model_seconds']}s of model time")
    for model, m in sorted(s["by_model"].items(), key=lambda kv: -kv[1]["tokens"]):
        print(f"  {model:45s} {m['calls']:4d} calls  {m['tokens']:>9,} tokens")
