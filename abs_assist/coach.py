"""The AI coach: plain-language questions answered from the analysis tools.

The model chooses which tools to call and explains their results. It never computes numbers
itself: a check in code requires every number in the answer to appear in a tool result this turn,
and sends an answer that invents one back once. Talks to any OpenAI-compatible chat endpoint,
configured with MODEL_URL and MODEL_NAME.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from contextlib import nullcontext
from typing import Any, Callable, Dict, List, Tuple

from abs_assist.tools import SCHEMA, Toolbox

MAX_ROUNDS = 6
SYSTEM = (
    "You are a baseball strategy assistant for a KBO team's coaches and analysts. Answer from the tools: "
    "look players up with find_players when you don't have an exact name, then use the other tools. "
    "Every number you state must come from a tool result; never estimate. Read numbers using each result's "
    "how_to_read, and describe locations with the tool's exact words. If a batter is named but no pitcher, "
    "answer from the batter's profile and take guide instead of asking. Be concise and practical: lead with "
    "the recommendation, then the two or three numbers that support it."
)
_NUMBER = re.compile(r"(?<![\w.,])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")
Chat = Callable[[List[Dict[str, Any]], List[Dict[str, Any]]], Dict[str, Any]]


def http_chat(url: str = "", model: str = "") -> Chat:
    """A chat function for an OpenAI-compatible endpoint."""
    url = url or os.environ.get("MODEL_URL", "")
    model = model or os.environ.get("MODEL_NAME", "")

    def chat(messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not (url and model):
            raise OSError("set MODEL_URL and MODEL_NAME to the chat model the coach should use")
        body = json.dumps({"model": model, "messages": messages, "tools": tools, "temperature": 0}).encode()
        req = urllib.request.Request(f"{url}/chat/completions", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.load(resp)["choices"][0]["message"]
    return chat


def _openai_tools() -> List[Dict[str, Any]]:
    return [{"type": "function", "function": {
        "name": t["name"], "description": t["description"],
        "parameters": {"type": "object", "properties": t["parameters"], "required": list(t["parameters"])}}}
        for t in SCHEMA]


def numbers(text: str) -> List[Tuple[float, bool]]:
    """Each number as (value, is_percentage); percentages become rates (55% -> 0.55)."""
    out = []
    for n in _NUMBER.findall(text):
        pct = n.endswith("%")
        value = float(n.rstrip("%").replace(",", ""))
        out.append((value / 100 if pct else value, pct))
    return out


def _supported(value: float, known: List[float]) -> bool:
    """Allows only rounding: rates to the half percentage point, whole numbers to the nearest one,
    other numbers to one decimal."""
    tolerance = 0.0051 if value <= 1 else 0.5 if value == int(value) else 0.051
    return any(abs(value - k) <= tolerance for k in known)


def unsourced(answer: str, evidence: str) -> List[float]:
    """Numbers in the answer that no tool result supports. Small whole numbers (counts like 1-2,
    "two pitches") are allowed; every percentage and every other number must match a tool value."""
    known = [v for v, _ in numbers(evidence)]
    return [v for v, pct in numbers(answer)
            if (pct or v > 3 or v != int(v)) and not _supported(v, known)]


class Coach:
    def __init__(self, tools: Toolbox, chat: Chat, lock: Any = None):
        """`lock` guards the database during tool calls, not while waiting on the model."""
        self.tools, self.chat, self.lock = tools, chat, lock or nullcontext()

    def _run_tools(self, msg: Dict[str, Any], messages: List[Dict[str, Any]], calls: List[Dict[str, Any]]) -> str:
        """Call each requested tool; record it; return the results as text for the number check."""
        evidence = ""
        for call in msg["tool_calls"]:
            fn = call["function"]
            args = json.loads(fn.get("arguments") or "{}")
            with self.lock:
                result = json.dumps(self.tools.call(fn["name"], args))
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            calls.append({"tool": fn["name"], "args": args, "result": result})
            evidence += result
        return evidence

    def ask(self, question: str) -> Dict[str, Any]:
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
        evidence, used, corrected, answer = "", [], False, ""
        calls: List[Dict[str, Any]] = []
        for _ in range(MAX_ROUNDS):
            msg = self.chat(messages, _openai_tools())
            turn = {"role": "assistant", "content": msg.get("content") or ""}
            messages.append({**turn, "tool_calls": msg["tool_calls"]} if msg.get("tool_calls") else turn)
            if msg.get("tool_calls"):
                evidence += self._run_tools(msg, messages, calls)
                used += [c["function"]["name"] for c in msg["tool_calls"]]
                continue
            answer = msg.get("content") or ""
            bad = unsourced(answer, evidence)
            if not bad or corrected:
                break
            messages.append({"role": "user", "content": f"[check] These numbers aren't in any tool result: {bad}. "
                                                        "Use only numbers from the tools, or call a tool."})
            corrected = True
        return {"answer": answer or "I couldn't finish that within the step limit.", "tools_used": used,
                "corrected": corrected, "evidence": evidence, "calls": calls}
