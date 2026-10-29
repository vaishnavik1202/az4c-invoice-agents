"""The agent loop shared by every agent.

    model asks for tools -> we run them -> model answers in JSON
    -> Pydantic schema check + agent-specific checks against the tool facts
    -> if rejected: send the exact errors back and let it fix the answer (max 2 retries)
"""
import json
import re
from typing import Callable

from pydantic import BaseModel, ValidationError

from app import llm
from app.agents import tool_registry

MAX_TURNS = 8        # tool rounds + answers
MAX_TOOL_ROUNDS = 3  # after this many tool rounds the agent must answer
MAX_RETRIES = 2      # rejected answers we allow before giving up


def log_event(trace_id: str, agent: str, event: str, payload: dict) -> None:
    """Non-LLM events in the same trace: plans, validator rejections, fallbacks, final result."""
    from datetime import datetime, timezone
    llm._log(trace_id, {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                        "trace_id": trace_id, "agent": agent, "event": event, **payload})


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found in your answer")
    return json.loads(text[start:end + 1])


def _assistant_dict(msg) -> dict:
    d = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        d["tool_calls"] = [{"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                           for tc in msg.tool_calls]
    return d


def run_agent(*, name: str, role: str, system: str, user: str, trace_id: str,
              tools: list[str], schema: type[BaseModel],
              validate: Callable[[BaseModel, dict], list[str]] | None = None,
              json_mode: bool = False) -> tuple[BaseModel | None, dict, list[str]]:
    """Returns (parsed answer or None, tool results by name, last validation errors)."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    tool_results: dict[str, dict] = {}
    seen_calls: set[tuple[str, str]] = set()
    retries, errors, tool_rounds = 0, [], 0
    for _ in range(MAX_TURNS):
        must_answer = bool(tools) and tool_rounds >= MAX_TOOL_ROUNDS
        if must_answer and messages[-1]["role"] == "tool":
            messages.append({"role": "user", "content":
                             "You have the tool results you need. Do not call more tools: answer now with the JSON."})
        try:
            msg = llm.chat(agent=name, role=role, messages=messages, trace_id=trace_id,
                           tools=tool_registry.schemas(tools) if tools else None,
                           json_mode=json_mode and not tools, force_text=must_answer)
        except RuntimeError as e:
            # No model could answer (quotas/outage). The caller falls back to tool facts / policy.
            log_event(trace_id, name, "llm_unavailable", {"error": str(e)[:500]})
            return None, tool_results, [f"LLM unavailable: {e}"]
        messages.append(_assistant_dict(msg))

        if msg.tool_calls:
            tool_rounds += 1
            for tc in msg.tool_calls:
                key = (tc.function.name, tc.function.arguments or "")
                if key in seen_calls:
                    result = {"note": "You already called this tool with these arguments; use that result."}
                else:
                    seen_calls.add(key)
                    result = tool_registry.call(tc.function.name, tc.function.arguments, tools)
                    if tc.function.name not in tool_results or "error" not in result:
                        tool_results[tc.function.name] = result
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": json.dumps(result, default=str)})
            continue

        # The model gave an answer: validate it
        try:
            answer = schema.model_validate(_extract_json(msg.content))
            errors = validate(answer, tool_results) if validate else []
        except (ValueError, ValidationError) as e:
            answer, errors = None, [f"Answer is not valid JSON for the required schema: {e}"]

        if not errors:
            return answer, tool_results, []

        log_event(trace_id, name, "validator_rejected", {"retry": retries + 1, "errors": errors})
        if retries >= MAX_RETRIES:
            break
        retries += 1
        messages.append({"role": "user", "content":
                         "Your answer was rejected by the validator:\n- " + "\n- ".join(errors) +
                         "\nCall any tools you still need, then return the corrected JSON only."})
    return None, tool_results, errors
