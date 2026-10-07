"""What the coach says when the model's answer still fails the checks after a rewrite, or never comes.

Everything here is computed from tool results, never from the model's text: the code-written answer
of the tools it called, a comparison when it looked up several players, the plan for a pitcher the
model ignored, or a lookup of the players the question names.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

Call = Dict[str, Any]
# Words in a question that name the stat a "who is higher" comparison is about.
STAT_WORDS: List[Tuple[str, str]] = [
    (r"miss|whiff", "whiff_rate"), (r"chase", "chase_rate"), (r"hard|velo|fast|speed", "fastball_kmh"),
    (r"strikes? (?:thrown|in the zone)|in the zone|zone rate", "in_zone_rate"), (r"strike ?out", "strikeout_rate"),
    (r"walk", "walk_rate"), (r"average", "batting_average"), (r"home run|homer", "home_runs"),
]


def _result(call: Call) -> Dict[str, Any]:
    try:
        out = json.loads(call["result"])
    except (ValueError, KeyError, TypeError):
        return {}
    return out if isinstance(out, dict) else {}


def comparison(question: str, calls: List[Call]) -> str:
    """'X is higher on whiff rate (11% vs 10%).' when several profiles were looked up for one stat."""
    metric = next((m for words, m in STAT_WORDS if re.search(words, question, re.I)), None)
    rows = [(r.get("pitcher") or r.get("batter"), (r.get("compared_with_league") or {}).get(metric, {}).get("value"))
            for r in map(_result, calls) if metric and r.get("compared_with_league")]
    rows = [(n, v) for n, v in dict(rows).items() if n and v is not None]
    if len(rows) < 2:
        return ""
    rows.sort(key=lambda nv: -nv[1])
    fmt = (lambda v: f"{round(v * 100)}%") if str(metric).endswith("_rate") else (lambda v: f"{v:g}")
    return f"{rows[0][0]} is higher on {str(metric).replace('_', ' ')} ({' vs '.join(f'{n} {fmt(v)}' for n, v in rows)})."


def code_answer(calls: List[Call], question: str = "") -> str:
    """The code-written answer of the latest tool that has one (of every call to it, when it was called for
    several players), with the comparison the question asks for."""
    written = [(c["tool"], str(_result(c).get("answer"))) for c in calls if _result(c).get("answer")]
    if not written:
        return ""
    last = written[-1][0]
    text = " ".join(dict.fromkeys(t for tool, t in written if tool == last))
    return f"{comparison(question, calls)} {text}".strip()


def named(question: str, names: List[str]) -> List[str]:
    return [n for n in names if n.lower() in question.lower()]


def lookup(question: str, tools: Any, calls: List[Call]) -> None:
    """Fill in what the model skipped: the named pitcher's own plan when it used the any-pitcher plan,
    or the profiles of the players the question names when it looked nothing up."""
    pitchers, plan = named(question, tools.pitchers()), next((c for c in calls if c["tool"] == "attack_plan"), None)
    if pitchers and plan and isinstance(plan.get("args"), dict):
        _run(tools, calls, "recommend_pitch", {**plan["args"], "pitcher": pitchers[0]})
    elif not calls:
        for p in pitchers:
            _run(tools, calls, "pitcher_profile", {"pitcher": p})
        for b in named(question, tools.batters()):
            _run(tools, calls, "batter_profile", {"batter": b})


def _run(tools: Any, calls: List[Call], name: str, args: Dict[str, Any]) -> None:
    calls.append({"tool": name, "args": args, "result": json.dumps(tools.call(name, args))})


def best(question: str, calls: List[Call], tools: Any) -> Optional[str]:
    """The code's answer after the model failed: look up what it skipped, then answer from the results."""
    lookup(question, tools, calls)
    return code_answer(calls, question) or None
