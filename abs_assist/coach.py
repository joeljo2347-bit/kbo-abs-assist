"""The AI coach: plain-language questions answered from the analysis tools.

The model chooses which tools to call and explains their results. It never computes numbers
itself: a check in code requires every number in the answer to appear in a tool result in this conversation,
and sends an answer that invents one back once. Talks to any OpenAI-compatible chat endpoint,
configured with MODEL_URL and MODEL_NAME.
"""

from __future__ import annotations

import json
import os
import re
import threading
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
    "- Pick the tool for the side asked about. How to pitch a batter: recommend_pitch when a pitcher is named, "
    "attack_plan when none is. What a hitter should take or lay off: take_guide. Pitchers' stats: pitcher_profile.\n"
    "- Don't stall. If something isn't specified, make a sensible assumption, say it in "
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


_PITCH = r"(?:fastball|sinker|slider|changeup|splitter|curveball|curve)s?"
# A bare "high"/"low" next to a pitch type: the tools only say knee-high, belt-high or letter-high.
_VAGUE_HEIGHT = re.compile(rf"\b{_PITCH}\W+(?:[a-z]+\W+){{0,2}}?(?<!-)(?:high|low)\b|(?<!-)\b(?:high|low)\W+(?:[a-z]+\W+)?{_PITCH}\b", re.I)


def problems(answer: str, evidence: str) -> List[str]:
    """What the code check sends back: numbers no tool supports, and pitch heights not in the tools' words."""
    bad = [f"the number {v:g}" for v in unsourced(answer, evidence)]
    return bad + [f'"{m.group(0)}" (say knee-high, belt-high or letter-high, exactly as the tool does)'
                  for m in _VAGUE_HEIGHT.finditer(answer)]


CHECK = ("[check] Your last answer has things no tool result supports: {bad}. Rewrite your answer to the "
         "coach's last question using only numbers and locations from tool results (call a tool if you need one). "
         "Reply with the rewritten answer only, addressed to the coach; don't mention this check.")


@dataclass
class Conversation:
    """One coach's conversation: the message history and every tool result so far. `lock` keeps two
    requests from writing into the same history at once."""
    messages: List[Dict[str, Any]] = field(default_factory=lambda: [{"role": "system", "content": SYSTEM}])
    evidence: str = ""
    question_at: int = 1
    lock: Any = field(default_factory=threading.Lock, repr=False, compare=False)

    def trimmed(self) -> List[Dict[str, Any]]:
        """The system prompt, as much earlier conversation as fits, and the current turn: its question
        always, then its most recent tool exchanges (a call is never separated from its results)."""
        turn = _fit_turn(self.messages[self.question_at:], KEEP_MESSAGES)
        earlier = self.messages[1:self.question_at][-max(KEEP_MESSAGES - len(turn), 0):]
        while earlier and earlier[0]["role"] != "user":
            earlier = earlier[1:]
        return [self.messages[0], *earlier, *turn]


def _fit_turn(turn: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """The question plus whole assistant steps (an assistant message and its tool results), newest kept."""
    steps: List[List[Dict[str, Any]]] = []
    for m in turn[1:]:
        if m["role"] == "assistant" or not steps:
            steps.append([])
        steps[-1].append(m)
    while len(steps) > 1 and 1 + sum(map(len, steps)) > limit:
        steps.pop(0)
    return [turn[0], *[m for step in steps for m in step]]


def _arguments(raw: Any) -> Tuple[Dict[str, Any], Optional[str]]:
    """A tool call's arguments as a dict, or an error the model can read and recover from."""
    if isinstance(raw, dict):
        return raw, None
    try:
        args = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}, "The tool arguments were not valid JSON; call the tool again with a JSON object."
    return (args, None) if isinstance(args, dict) else ({}, "Tool arguments must be a JSON object.")


class Coach:
    def __init__(self, tools: Toolbox, chat: Chat, lock: Any = None):
        """`lock` guards the database during tool calls, not while waiting on the model."""
        self.tools, self.chat, self.lock = tools, chat, lock or nullcontext()

    def _run_tools(self, tool_calls: List[Dict[str, Any]], convo: Conversation, calls: List[Dict[str, Any]]) -> None:
        """Call each requested tool and record the call and its result in the conversation."""
        for call in tool_calls:
            fn = call["function"]
            args, problem = _arguments(fn.get("arguments"))
            if problem:
                result = json.dumps({"error": problem})
            else:
                with self.lock:
                    result = json.dumps(self.tools.call(fn["name"], args))
            convo.messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            calls.append({"tool": fn["name"], "args": args, "result": result})
            convo.evidence += result

    def _step(self, convo: Conversation, calls: List[Dict[str, Any]]) -> Optional[str]:
        """One model turn: run its tool calls (None) or return its answer."""
        msg = self.chat(convo.trimmed(), _openai_tools())
        tool_calls = msg.get("tool_calls") or []
        for i, call in enumerate(tool_calls):
            call.setdefault("id", f"call_{len(convo.messages)}_{i}")
        turn = {"role": "assistant", "content": msg.get("content") or ""}
        convo.messages.append({**turn, "tool_calls": tool_calls} if tool_calls else turn)
        if tool_calls:
            self._run_tools(tool_calls, convo, calls)
            return None
        return turn["content"]

    def ask(self, question: str, convo: Optional[Conversation] = None) -> Dict[str, Any]:
        convo = convo or Conversation()
        convo.messages.append({"role": "user", "content": question})
        convo.question_at = len(convo.messages) - 1
        calls: List[Dict[str, Any]] = []
        answer, check = "", None
        for _ in range(MAX_ROUNDS):
            answer = self._step(convo, calls) or ""
            if not answer:
                continue
            bad = problems(answer, convo.evidence + " " + RULE_FACTS)
            if not bad or check is not None:
                break
            check = {"role": "user", "content": CHECK.format(bad="; ".join(bad))}
            convo.messages.append(check)
        _drop_rejected(convo, check)
        return {"answer": answer or "I couldn't finish that within the step limit.", "corrected": check is not None,
                "tools_used": [c["tool"] for c in calls], "calls": calls, "visuals": visuals.for_calls(calls),
                "conversation": convo}


def _drop_rejected(convo: Conversation, check: Optional[Dict[str, Any]]) -> None:
    """Remove a rejected answer and the check that sent it back, so later turns never build on it.
    The check is found by identity, so a coach's own question that happens to start with "[check]"
    is never touched."""
    for i, m in enumerate(convo.messages):
        if m is check:
            del convo.messages[i - 1:i + 1]
            return
