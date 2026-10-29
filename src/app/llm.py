"""One wrapper for every model call.

- Picks the model for the agent's role (orchestrator vs specialist)
- Fails fast (timeout) and retries itself on rate limits / server errors, with backoff
- Falls back to a backup model if the main one keeps failing
- Logs EVERY attempt to logs/<trace_id>.jsonl: agent, model, prompt, response, tokens, latency
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError

from app.config import LOGS_DIR

PROVIDERS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "key_env": "GEMINI_API_KEY"},
}

# role -> list of (provider, model) to try in order. First = main, rest = fallbacks.
# Groq's free tier has a separate DAILY token quota per model, so a longer chain of models keeps
# the system running when one model's quota is used up. Override with comma-separated env lists.
DEFAULT_ORCHESTRATOR_MODELS = "openai/gpt-oss-120b,llama-3.3-70b-versatile,meta-llama/llama-4-scout-17b-16e-instruct,openai/gpt-oss-20b"
DEFAULT_SPECIALIST_MODELS = "openai/gpt-oss-20b,meta-llama/llama-4-scout-17b-16e-instruct,qwen/qwen3-32b,llama-3.1-8b-instant,openai/gpt-oss-120b"


def _routes() -> dict[str, list[tuple[str, str]]]:
    orch = os.getenv("GROQ_ORCHESTRATOR_MODELS") or os.getenv("GROQ_MODEL") and \
        ",".join(dict.fromkeys([os.environ["GROQ_MODEL"]] + DEFAULT_ORCHESTRATOR_MODELS.split(","))) or DEFAULT_ORCHESTRATOR_MODELS
    spec = os.getenv("GROQ_SPECIALIST_MODELS") or os.getenv("GROQ_SPECIALIST_MODEL") and \
        ",".join(dict.fromkeys([os.environ["GROQ_SPECIALIST_MODEL"]] + DEFAULT_SPECIALIST_MODELS.split(","))) or DEFAULT_SPECIALIST_MODELS
    routes = {
        "orchestrator": [("groq", m.strip()) for m in orch.split(",") if m.strip()],
        "specialist": [("groq", m.strip()) for m in spec.split(",") if m.strip()],
    }
    if os.getenv("USE_GEMINI", "false").lower() == "true":
        gemini = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        for r in routes.values():
            r.append(("gemini", gemini))
    return routes


_available: dict[str, set[str] | None] = {}
_exhausted: set[tuple[str, str]] = set()   # models skipped for the rest of this process


_NON_CHAT = ("whisper", "tts", "guard", "playai", "orpheus", "embed", "compound", "distil", "audio", "vision")


def _extended(route: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Preferred models first, then any other chat model this account offers (each has its own quota)."""
    out = [r for r in route if _is_available(*r)]
    for provider in dict.fromkeys(p for p, _ in route):
        for m in sorted(_available.get(provider) or []):
            if not any(x in m.lower() for x in _NON_CHAT) and (provider, m) not in out:
                out.append((provider, m))
    return out


def available_routes() -> dict[str, list[tuple[str, str]]]:
    return {role: _extended(r) for role, r in _routes().items()}


def _is_available(provider: str, model: str) -> bool:
    """Skip models this account doesn't have (checked once per provider)."""
    if provider not in _available:
        try:
            _available[provider] = {m.id for m in _client(provider).models.list()}
        except Exception:
            _available[provider] = None  # can't tell: try it anyway
    ids = _available[provider]
    return ids is None or model in ids or model.removeprefix("models/") in ids

MAX_ATTEMPTS = 3          # per model for errors, before falling back
MAX_RATE_LIMIT_WAITS = 6  # per model for short per-minute rate-limit waits
MAX_WAIT_S = 65           # longest wait we accept for a per-minute limit
TIMEOUT_S = 60
_clients: dict[str, OpenAI] = {}


def _client(provider: str) -> OpenAI:
    if provider not in _clients:
        cfg = PROVIDERS[provider]
        key = os.getenv(cfg["key_env"])
        if not key:
            raise RuntimeError(f"{cfg['key_env']} is not set in .env")
        # max_retries=0: we do our own retries so every attempt is visible in the log
        _clients[provider] = OpenAI(api_key=key, base_url=cfg["base_url"], timeout=TIMEOUT_S, max_retries=0)
    return _clients[provider]


def _log(trace_id: str, record: dict) -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    with (LOGS_DIR / f"{trace_id}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _retryable(e: Exception) -> bool:
    if isinstance(e, (RateLimitError, APITimeoutError, APIConnectionError)):
        return True
    return isinstance(e, APIStatusError) and e.status_code >= 500


def _wait_seconds(e: Exception, attempt: int) -> float:
    """Honour the provider's retry-after header on 429s; otherwise back off 2s, 4s, 8s."""
    try:
        ra = float(e.response.headers.get("retry-after"))  # type: ignore[attr-defined]
        return max(ra, 1.0)
    except Exception:
        return min(2 ** attempt, 10)


def chat(*, agent: str, role: str, messages: list[dict], trace_id: str,
         tools: list[dict] | None = None, json_mode: bool = False, force_text: bool = False):
    """Call a model and return the assistant message (may contain tool_calls).

    agent: name for the log, e.g. "orchestrator", "price_specialist"
    role:  "orchestrator" or "specialist" (decides which model is used)
    """
    last_error = None
    for provider, model in _extended(_routes()[role]):
        if (provider, model) in _exhausted:
            continue
        no_tools, errors_here, waits_here, attempt = force_text, 0, 0, 0
        while errors_here < MAX_ATTEMPTS and waits_here <= MAX_RATE_LIMIT_WAITS:
            attempt += 1
            started = time.perf_counter()
            kwargs = {"model": model, "messages": messages, "temperature": 0}
            if tools:
                kwargs["tools"] = tools
                if no_tools:
                    kwargs["tool_choice"] = "none"   # model wants to answer: let it answer in text
            if json_mode and not tools:
                kwargs["response_format"] = {"type": "json_object"}
            record = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "trace_id": trace_id, "agent": agent, "provider": provider,
                "model": model, "attempt": attempt, "prompt": messages,
                "tools_offered": [t["function"]["name"] for t in tools or []],
            }
            try:
                resp = _client(provider).chat.completions.create(**kwargs)
            except Exception as e:
                record.update(latency_ms=round((time.perf_counter() - started) * 1000),
                              error=f"{type(e).__name__}: {e}")
                _log(trace_id, record)
                last_error = e
                if isinstance(e, APIStatusError) and e.status_code == 400 and "tool call validation failed" in str(e):
                    # gpt-oss sometimes "calls" a non-existent tool (e.g. 'JSON') to give its answer.
                    # Retry the same model with tools disabled for this turn so it answers in plain text.
                    no_tools = True
                    errors_here += 1
                    continue
                if isinstance(e, APIStatusError) and e.status_code == 404:
                    _exhausted.add((provider, model))   # model not found on this account
                    break
                if not _retryable(e):
                    break                      # bad request etc: retrying won't help, try next model
                wait = _wait_seconds(e, attempt)
                if isinstance(e, RateLimitError):
                    if "per day" in str(e) or wait > MAX_WAIT_S:
                        # daily quota: this model is done for today, move on now
                        _exhausted.add((provider, model))
                        _log(trace_id, {**record, "event": "model_skipped", "why": "daily quota or long wait"})
                        break
                    waits_here += 1                  # per-minute limit: worth waiting for
                    _log(trace_id, {**record, "event": "rate_limit_wait", "wait_s": round(wait, 1)})
                    print(f"    (rate limit on {model}, waiting {wait:.0f}s)", file=sys.stderr, flush=True)
                else:
                    errors_here += 1
                time.sleep(wait)
                continue
            msg = resp.choices[0].message
            if msg.content and "<think>" in msg.content:   # reasoning models (e.g. qwen3) inline their thinking
                import re as _re
                msg.content = _re.sub(r"<think>.*?</think>", "", msg.content, flags=_re.S).strip()
            usage = resp.usage
            record.update(
                latency_ms=round((time.perf_counter() - started) * 1000),
                response={"content": msg.content,
                          "tool_calls": [{"name": tc.function.name, "arguments": tc.function.arguments}
                                         for tc in msg.tool_calls or []]},
                tokens={"prompt": getattr(usage, "prompt_tokens", None),
                        "completion": getattr(usage, "completion_tokens", None),
                        "total": getattr(usage, "total_tokens", None)},
            )
            _log(trace_id, record)
            return msg
    raise RuntimeError(f"All models failed for {agent}: {last_error}")
