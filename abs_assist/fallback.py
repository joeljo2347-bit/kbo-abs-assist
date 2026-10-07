"""What the coach fills in when the model's tool choices fall short.

A stat or split the data can't give is said plainly; a pitcher the question names gets his own plan
when the model asked for the any-pitcher one; and the players a question names are looked up when
the model looked nothing up.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

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


def named(question: str, names: List[str]) -> List[str]:
    """Names in the question as whole names: 'Kim Ji-ho' is not found inside 'Kim Ji-hoon'."""
    return [n for n in names if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", question, re.I)]


# Stats the data can't give: it records pitches and how plate appearances ended, not runs, bases or game state.
UNAVAILABLE = re.compile(r"\b(?:ERA|WAR|RBIs?|runs batted|scoring position|RISP|saves?|wins?|losses|innings pitched|"
                         r"stolen|with runners|home and away|by month)\b", re.I)


def unavailable(question: str) -> str:
    """'ERA isn't in this data ...' when the question asks for a stat or split the data can't give."""
    m = UNAVAILABLE.search(question)
    if not m:
        return ""
    return (f"{m.group(0)}: not available. This data records every pitch and how each plate appearance ended, "
            "but not base runners, innings or game scores.")


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
