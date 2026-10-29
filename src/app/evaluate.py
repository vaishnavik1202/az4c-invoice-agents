"""Scores result JSONs against expected/ files, using the same fields the evaluators score."""


def _numbers(obj) -> list[float]:
    if isinstance(obj, bool):
        return []
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, dict):
        return [n for v in obj.values() for n in _numbers(v)]
    if isinstance(obj, list):
        return [n for v in obj for n in _numbers(v)]
    return []


def score(result: dict, expected: dict) -> dict:
    checks = {}
    checks["decision"] = result.get("decision") == expected["decision"]
    codes = expected["reason_code"]
    checks["reason_code"] = result.get("reason_code") in (codes if isinstance(codes, list) else [codes])
    if "amount" in expected:
        a = expected["amount"]
        if a is None or (isinstance(a, dict) and a.get("value") is None):
            checks["amount"] = result.get("amount") is None
        else:
            val, tol = (a["value"], a.get("tolerance", 1.0)) if isinstance(a, dict) else (a, 1.0)
            checks["amount"] = result.get("amount") is not None and abs(result["amount"] - val) <= tol
    if expected.get("key_numbers"):
        nums = _numbers(result)
        for k in expected["key_numbers"]:
            kv, kt = (k["value"], k.get("tolerance", 1.0)) if isinstance(k, dict) else (k, 1.0)
            checks[f"key_number:{kv}"] = any(abs(n - kv) <= kt for n in nums)
    cites = set(result.get("citations", []))
    for c in expected.get("must_cite", []):
        checks[f"cite:{c}"] = c in cites
    if "needs_human" in expected:
        checks["needs_human"] = result.get("needs_human") == expected["needs_human"]
    return {"passed": all(checks.values()), "checks": checks}
