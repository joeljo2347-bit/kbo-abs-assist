"""The AI coach: plain-language questions answered from the analysis tools.

The language model reads the conversation and decides which tools to call, with which players,
counts and filters. The answer the coach shows is then written in code from those results
(abs_assist/compose.py), so every number and claim in it is one the tools returned; the model's
own wording is never shown. Talks to any OpenAI-compatible chat endpoint, configured with
MODEL_URL and MODEL_NAME.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from abs_assist import compose, visuals, writer
from abs_assist.tools import SCHEMA, Toolbox

MAX_ROUNDS, KEEP_MESSAGES = 10, 40
SYSTEM = (
    "You choose the analysis tools that answer a baseball coach's questions; code writes the reply from their "
    "results, so call every tool the question needs, with exact arguments, and then reply 'done'.\n"
    "- Follow-ups ('and with two strikes?', 'what about him?') refer to earlier turns: call the tools again with the "
    "new count, player or filter. Look names up with find_players when unsure. Call several tools at once if needed.\n"
    "- How to pitch a batter in a count: recommend_pitch when a pitcher is named, else attack_plan. Whether a hitter "
    "should take, swing or be patient in a count: take_guide. What a pitcher will throw next in a count: "
    "predict_next_pitch. His pitch types, speeds, best pitch, mix by batter side, with two strikes, on the first "
    "pitch, or after a given pitch: pitcher_arsenal. A player's tendencies and stats, or comparing named players: "
    "pitcher_profile or batter_profile for each. Who or which team is highest or lowest at something: leaderboard "
    "(who=team_batting or team_pitching for teams; order=lowest for least, fewest or toughest). How the ABS zone "
    "works, or the zone for a given height: abs_rules. Strikes lost at the back of the plate for a team: "
    "strikes_lost_at_back.\n"
    "- A full count is 3-2. Use the count the question gives; '0-0' is the first pitch."
)
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


@dataclass
class Conversation:
    """One coach's conversation: the message history and every tool result so far. `lock` keeps two
    requests from writing into the same history at once."""
    messages: List[Dict[str, Any]] = field(default_factory=lambda: [{"role": "system", "content": SYSTEM}])
    evidence: str = ""
    question_at: int = 1
    last_calls: List[Dict[str, Any]] = field(default_factory=list)  # the tools behind the last answer, for follow-ups
    lock: Any = field(default_factory=threading.Lock, repr=False, compare=False)

    def trimmed(self) -> List[Dict[str, Any]]:
        """The system prompt, as much earlier conversation as fits, and the current turn: its question
        always, then its most recent tool exchanges (a call is never separated from its results)."""
        turn = _fit_turn(self.messages[self.question_at:], KEEP_MESSAGES)
        room = KEEP_MESSAGES - len(turn)
        earlier = self.messages[1:self.question_at][-room:] if room > 0 else []
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
    def __init__(self, tools: Toolbox, chat: Chat, lock: Any = None, writer: bool = False):
        """`lock` guards the database during tool calls, not while waiting on the model. `writer`: the model writes the
        reply from facts code lays out, checked in code (abs_assist/writer.py); off, the code's own answer is shown."""
        self.tools, self.chat, self.lock, self.writer = tools, chat, lock or nullcontext(), writer

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
        """Answer one question. If anything fails, the conversation is left as it was before the question,
        so a broken turn (e.g. a tool call with no result) never poisons the next one."""
        convo = convo or Conversation()
        saved = (len(convo.messages), convo.evidence, convo.question_at)
        try:
            return self._ask(question, convo)
        except Exception:
            del convo.messages[saved[0]:]
            convo.evidence, convo.question_at = saved[1], saved[2]
            raise

    def _ask(self, question: str, convo: Conversation) -> Dict[str, Any]:
        convo.messages.append({"role": "user", "content": question})
        convo.question_at = len(convo.messages) - 1
        calls: List[Dict[str, Any]] = []
        for _ in range(MAX_ROUNDS):
            try:
                if self._step(convo, calls) is not None:
                    break  # the model has finished choosing tools
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    raise
                break  # the model server choked on this turn (e.g. a malformed tool call): answer from what code can look up
        final = compose.answer(question, calls, self.tools, convo.last_calls)
        if self.writer:
            final = writer.write(self.chat, question, final, calls or convo.last_calls)
        if convo.messages[-1]["role"] == "assistant" and not convo.messages[-1].get("tool_calls"):
            convo.messages[-1]["content"] = final  # later turns see what the coach was actually told
        else:
            convo.messages.append({"role": "assistant", "content": final})
        convo.last_calls = calls or convo.last_calls
        return {"answer": final, "tools_used": [c["tool"] for c in calls], "calls": calls,
                "visuals": visuals.for_calls(calls), "conversation": convo}
