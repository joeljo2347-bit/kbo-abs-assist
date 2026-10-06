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
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from abs_assist import visuals
from abs_assist.tools import SCHEMA, Toolbox

MAX_ROUNDS, KEEP_MESSAGES = 8, 40
SYSTEM = (
    "You are the club's baseball strategy assistant, in an ongoing conversation with coaches and analysts.\n"
    "- Use the conversation: follow-ups like 'and with two strikes?', 'why?' or 'what about him?' refer to "
    "earlier turns. Reuse earlier tool results when they still answer the question; call tools for anything new. "
    "Look players up with find_players when you don't have an exact name. When you need several players or "
    "several facts, call all the tools you need at once in a single step.\n"
    "- Facts and numbers come only from tool results in this conversation or the ABS rules; never estimate. "
    "Read numbers with each result's how_to_read, and describe locations with the tools' exact words.\n"
    "- Don't stall. If something isn't specified (say, no pitcher named), make a sensible assumption, say it in "
    "a few words, and answer. Ask a clarifying question only when no useful answer is possible.\n"
    "- If the tools don't have something (ERA, handedness, spin rate), say so plainly and offer what they do have.\n"
    "- When comparing players, give each one's number and say clearly which is higher.\n"
    "- When asked why, explain the reasoning: swing, whiff and called-strike chances, and the ABS rule that "
    "the bottom and top are checked at both the middle and the back of the plate.\n"
    "- The app shows the key numbers next to your answer, so never write tables or long lists. Answer in two "
    "to four short sentences of plain baseball language: the answer first, then the one or two "
    "numbers that matter most, written as percentages where they are chances. Don't suggest follow-up questions."
)
# Published ABS rule numbers the coach may quote without a tool call (zone shares, widths, plate depth).
RULE_FACTS = "55.75% 27.04% 56.35% 27.64% 47.18 43.18 2 21.59"
_NUMBER = re.compile(r"(?<![\w.,])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[ \u00a0\u202f]?%)?")
Chat = Callable[[List[Dict[str, Any]], List[Dict[str, Any]]], Dict[str, Any]]


def http_chat(url: str = "", model: str = "") -> Chat:
    """A chat function for an OpenAI-compatible endpoint."""
    url = url or os.environ.get("MODEL_URL", "")
    model = model or os.environ.get("MODEL_NAME", "")
    effort = os.environ.get("MODEL_REASONING", "low")  # less hidden reasoning, faster replies

    def chat(messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not (url and model):
            raise OSError("set MODEL_URL and MODEL_NAME to the chat model the coach should use")
        body = json.dumps({"model": model, "messages": messages, "tools": tools, "temperature": 0,
                           "reasoning_effort": effort}).encode()
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
        value = float(n.rstrip("%").rstrip(" \u00a0\u202f").replace(",", ""))
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


CHECK = ("[check] Your last answer used numbers that no tool result supports: {bad}. Rewrite your answer to the "
         "coach's last question using only numbers from tool results (call a tool if you need one). Reply with the "
         "rewritten answer only, addressed to the coach; don't mention this check.")


@dataclass
class Conversation:
    """One coach's conversation: the message history and every tool result so far."""
    messages: List[Dict[str, Any]] = field(default_factory=lambda: [{"role": "system", "content": SYSTEM}])
    evidence: str = ""

    def trimmed(self) -> List[Dict[str, Any]]:
        """The system prompt plus recent turns, starting at a user message so tool results keep their calls."""
        recent = self.messages[1:][-KEEP_MESSAGES:]
        while recent and recent[0]["role"] != "user":
            recent = recent[1:]
        return [self.messages[0], *recent]


class Coach:
    def __init__(self, tools: Toolbox, chat: Chat, lock: Any = None):
        """`lock` guards the database during tool calls, not while waiting on the model."""
        self.tools, self.chat, self.lock = tools, chat, lock or nullcontext()

    def _run_tools(self, msg: Dict[str, Any], convo: Conversation, calls: List[Dict[str, Any]]) -> None:
        """Call each requested tool and record the call and its result in the conversation."""
        for call in msg["tool_calls"]:
            fn = call["function"]
            args = json.loads(fn.get("arguments") or "{}")
            with self.lock:
                result = json.dumps(self.tools.call(fn["name"], args))
            convo.messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            calls.append({"tool": fn["name"], "args": args, "result": result})
            convo.evidence += result

    def _step(self, convo: Conversation, calls: List[Dict[str, Any]]) -> Optional[str]:
        """One model turn: run its tool calls (None) or return its answer."""
        msg = self.chat(convo.trimmed(), _openai_tools())
        turn = {"role": "assistant", "content": msg.get("content") or ""}
        convo.messages.append({**turn, "tool_calls": msg["tool_calls"]} if msg.get("tool_calls") else turn)
        if msg.get("tool_calls"):
            self._run_tools(msg, convo, calls)
            return None
        return turn["content"]

    def ask(self, question: str, convo: Optional[Conversation] = None) -> Dict[str, Any]:
        convo = convo or Conversation()
        convo.messages.append({"role": "user", "content": question})
        calls: List[Dict[str, Any]] = []
        answer, corrected = "", False
        for _ in range(MAX_ROUNDS):
            answer = self._step(convo, calls) or ""
            if not answer:
                continue
            bad = unsourced(answer, convo.evidence + " " + RULE_FACTS)
            if not bad or corrected:
                break
            convo.messages.append({"role": "user", "content": CHECK.format(bad=bad)})
            corrected = True
        _drop_rejected(convo)
        return {"answer": answer or "I couldn't finish that within the step limit.", "corrected": corrected,
                "tools_used": [c["tool"] for c in calls], "calls": calls, "visuals": visuals.for_calls(calls),
                "conversation": convo}


def _drop_rejected(convo: Conversation) -> None:
    """Remove a rejected answer and its [check] message, so later turns never build on it."""
    for i, m in enumerate(convo.messages):
        if m["role"] == "user" and m["content"].startswith("[check]"):
            del convo.messages[i - 1:i + 1]
            return
