"""The coach's reply, written by the language model from facts laid out in code, then checked in code.

Code gathers every fact the tools returned for the question (labeled, rates as percentages) and a draft answer
of its own. The model writes the reply from those facts alone and may say plainly that something isn't in the
data. Code then checks that every number, player, team and pitch location in the reply appears in the facts or
the question. A reply that fails gets one rewrite; if that fails too, the coach shows the code's draft.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List

Call = Dict[str, Any]
Chat = Callable[[List[Dict[str, Any]], List[Dict[str, Any]]], Dict[str, Any]]
MAX_FACT_LINES = 260
SYSTEM = (
    "You write the reply a baseball coach reads, using ONLY the facts given. Answer exactly what was asked: lead with "
    "the answer itself (yes or no, the name, the pitch, the number), then one or two facts that support it. Copy every "
    "number exactly as it is written in the facts, with its unit. Name only players and teams that appear in the facts "
    "or the question. Describe pitch locations only with the words the facts use. If the facts don't contain what was "
    "asked, or only part of it, say plainly which part isn't in the data and give what is. Never estimate, never work "
    "out new numbers, never use outside knowledge. Two to four short sentences of plain words; no lists, tables or "
    "markdown. Answer every part of a question with several parts, and when it names several players, give each "
    "one's number and say which is ahead. Draw no conclusion the facts don't state directly, and don't answer yes or "
    "no when the facts don't settle it. If the question adds a condition the facts don't split by (a fastball 'for a "
    "strike', 'with runners on'), say the facts don't separate it. Write in English, and never mention field or tool "
    "names. The draft answer was written by code and is correct, but it may not fit the question: use it when it does."
)
# Plain names for fields, so the model never quotes an internal label.
LABELS = {"batter_value_after_runs": "hitter's run value after this pitch", "batter_value_now_runs": "hitter's run value before it",
          "called_strike_chance_if_taken": "chance it's called a strike if taken", "whiff_chance_if_swung_at": "chance he misses if he swings",
          "swing_chance": "chance he swings", "next_pitch_after": "next pitch after a", "zone_swing_rate": "swing rate at strikes",
          "chase_rate": "chase rate (swings at balls)", "strikes_lost": "strikes lost at the back of the plate",
          "share_of_takes": "share of taken pitches", "by_pitch_type": "by pitch type", "mix_by_situation": "pitch mix",
          "hb_cm": "horizontal break cm", "ivb_cm": "vertical break cm", "avg_kmh": "average km/h", "max_kmh": "top km/h",
          "gain_from_taking_runs": "run value gained by taking", "p_called_strike": "chance it's called a strike",
          "in_zone_rate": "share of pitches in the zone", "others_higher": "qualified others higher",
          "others_lower": "qualified others lower", "others": "qualified others", "each_pitch": "pitch"}
RATE = re.compile(r"rate|usage|chance|share|probabilit|whiff|chase|swing|foul|^p_|^(?:fastball|sinker|slider|changeup|splitter|curveball)$")
THREE_PLACES = ("batting_average", "on_base_percentage", "slugging")


def _value(path: str, v: Any) -> str:
    """Rates as percentages, batting stats as .276, everything else as it is. `path` is the field's full label."""
    name, last = path.replace(" ", "_"), path.split(" > ")[-1].replace(" ", "_")
    if not isinstance(v, float) or re.search(r"_(?:cm|kmh|runs)$", last):
        return str(v)
    if any(t in name for t in THREE_PLACES) and last in (*THREE_PLACES, "value", "league_average"):
        return f"{v:.3f}".lstrip("0")
    if 0 <= v <= 1 and (RATE.search(last) or (last in ("value", "league_average") and RATE.search(name))):
        return f"{round(v * 100 + 1e-9, 1):.1f}%"
    return str(v)


def _label(key: str) -> str:
    return LABELS.get(key, key.replace("_", " "))


def _flatten(prefix: str, v: Any, out: List[str]) -> None:
    if isinstance(v, dict) and v and all(not isinstance(x, (dict, list)) for x in v.values()) and len(v) <= 8 and prefix:
        out.append(f"{prefix}: " + ", ".join(f"{_label(k)} {_value(prefix + ' > ' + k, x)}" for k, x in v.items()))
    elif isinstance(v, dict):
        for k, x in v.items():
            if k not in ("how_to_read", "data_covers", "answer"):
                _flatten(f"{prefix} > {_label(k)}" if prefix else _label(k), x, out)
    elif isinstance(v, list) and v and isinstance(v[0], dict):
        for item in v:
            out.append(f"{prefix}: " + "; ".join(f"{_label(k)} {_value(k, x)}" for k, x in item.items()))
    elif isinstance(v, list):
        out.append(f"{prefix}: {', '.join(map(str, v))}")
    else:
        out.append(f"{prefix}: {_value(prefix, v)}")


def facts(calls: List[Call]) -> str:
    """Every result as labeled lines, one block per tool call, with what the fields mean."""
    lines: List[str] = []
    for c in calls:
        try:
            r = json.loads(c["result"])
        except (ValueError, KeyError, TypeError):
            continue
        if not isinstance(r, dict) or "error" in r:
            continue
        lines.append(f"[{c['tool']} {json.dumps(c.get('args') or {})}]" + (f" Meaning: {r['how_to_read']}" if r.get("how_to_read") else ""))
        _flatten("", r, lines)
    return "\n".join(lines[:MAX_FACT_LINES])


_NUM = re.compile(r"(?<![\w.])\.?\d[\d,]*(?:\.\d+)?")
_NAME = re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+-[a-z]+\b")
_PLACE = re.compile(r"(?:knee|belt|letter)-high|above the zone|below the zone|over the middle|on the edge|toward a corner|off the plate")


def _numbers(text: str) -> List[float]:
    return [float(n.replace(",", "")) for n in _NUM.findall(text) if n.replace(",", "").strip(".")]


def style_problems(reply: str) -> List[str]:
    """A reply a coach shouldn't see: markdown, another language, internal field names, or too long."""
    bad = []
    if re.search(r"^\s*(?:[|#]|[-*] |\d+\. )|\*\*|\|", reply, re.M):
        bad.append("markdown (tables, lists or bold): write plain sentences")
    if re.search(r"[^\x00-\x7F\u2010-\u2027\u00b0\u00d7\u00b7]", reply):
        bad.append("text that isn't English")
    if re.search(r"[a-z]_[a-z]| > ", reply):
        bad.append("internal field names")
    if len(reply) > 700:
        bad.append("a reply that is too long: two to four sentences")
    return bad


def problems(reply: str, fact_text: str, question: str) -> List[str]:
    """What in the reply isn't in the facts or the question: numbers, player names, pitch-location words; and
    players the question names that the reply leaves out."""
    names = set(_NAME.findall(question))
    left_out = [n for n in names if len(names) > 1 and n in fact_text and n not in reply.replace("\u2011", "-")]
    known = _numbers(fact_text) + _numbers(question)
    bad = [f"the number {n:g}" for n in _numbers(reply)
           if n > 3 and not any(abs(n - k) <= 0.051 or abs(n - k * 100) <= 0.051 for k in known)]
    seen = fact_text + " " + question
    bad += [f"the name {n}" for n in set(_NAME.findall(reply)) if n not in seen]
    bad += [f'the location "{w}"' for w in set(_PLACE.findall(reply.replace("\u2011", "-"))) if w not in seen]
    return bad + [f"nothing about {n}, whom the question names" for n in left_out] + style_problems(reply)


def _ask(chat: Chat, question: str, draft: str, fact_text: str, fix: str = "") -> str:
    user = (f"Question: {question}\n\nDraft answer written by code: {draft}\n\nFacts:\n{fact_text or '(none)'}"
            + (f"\n\nYour last reply had problems: {fix}. Rewrite it: plain English sentences, only the facts." if fix else ""))
    reply = chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], [])
    return str(reply.get("content") or "").strip()


def write(chat: Chat, question: str, draft: str, calls: List[Call]) -> str:
    """The model's reply when every number, name and location in it checks out; otherwise the code's draft."""
    fact_text = facts(calls)
    try:
        reply = _ask(chat, question, draft, fact_text)
        bad = problems(reply, fact_text + " " + draft, question)
        if reply and bad:
            reply = _ask(chat, question, draft, fact_text, "; ".join(bad))
            bad = problems(reply, fact_text + " " + draft, question)
    except Exception:  # the model server failing here never costs the coach its answer
        return draft
    return reply if reply and not bad else draft
