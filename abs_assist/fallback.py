"""What the coach fills in when the model's tool choices fall short of the question.

A stat or split the data can't give is said plainly. Otherwise the lookups the question needs and the
model skipped are run here: the named pitcher's own plan, the count the question names, the team it
names, his pitch mix, the zone rules for the heights it gives, or the profiles of the players it names.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

Call = Dict[str, Any]
COUNT_TOOLS = ("recommend_pitch", "attack_plan", "take_guide", "predict_next_pitch")


def _result(call: Call) -> Dict[str, Any]:
    try:
        out = json.loads(call["result"])
    except (ValueError, KeyError, TypeError):
        return {}
    return out if isinstance(out, dict) else {}


def named(question: str, names: List[str]) -> List[str]:
    """Names in the question as whole names: 'Kim Ji-ho' is not found inside 'Kim Ji-hoon'."""
    return [n for n in names if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", question, re.I)]


# A question about what a hitter actually does (not what he should do), by count or situation.
BEHAVIOR = re.compile(r"\b(?:does|do|will) (?:he|[\w'-]+(?: [\w'-]+){0,2}) (?:actually |usually |really |tend to )?"
                      r"(?:swing|take|chase|foul|expand|offer|attack|go)\b"
                      r"|how often does he|\bis he (?:aggressive|patient|swinging|chasing) (?:early|on|in|at|with)|early in (?:the )?counts?"
                      r"|foul(?:s|ing)? (?:off|pitches)|spoil|sits? on", re.I)
# "after a slider", "after he throws a slider", "after Park Ha-jun throws a slider"
AFTER = re.compile(r"\bafter (?:[\w'-]+ ){0,4}?(?:throws? |throwing )?an? (fastball|sinker|slider|changeup|splitter|curveball)", re.I)
PREDICT = re.compile(r"likely (?:to )?throw|most likely|throw next|next pitch|what[’']?s coming|what is coming|going to throw"
                     r"|expect (?:him )?to throw|comes next", re.I)
PITCH_NAMES = r"fastball|sinker|slider|changeup|splitter|curveball"
# Stats the data can't give: it records pitches and how plate appearances ended, not runs, bases or game state.
UNAVAILABLE = re.compile(r"\b(?:ERA|WAR|RBIs?|runs batted|scoring position|RISP|saves?|wins?|losses|innings pitched|"
                         r"stolen|with runners|home and away|by month)\b", re.I)
# A hitter's results split by count or pitcher hand: the data has his pitches, but no split of his results.
HITTER_SPLIT = re.compile(r"\b(?:batting average|average|on-base|obp|slugging|home runs?)\b[^?]*\b(?:with two strikes|"
                          r"with \d strikes|against (?:left|right)|vs\.? (?:left|right)|left-handed pitch|right-handed pitch|lefties|righties)",
                          re.I)


def unavailable(question: str) -> str:
    """'ERA: not available in this data.' when the question asks for a stat or split the data can't give."""
    m = UNAVAILABLE.search(question)
    if m and re.match(r"(?i)with runners|scoring position|risp", m.group(0)):
        return "Results with runners on base aren't available in this data."
    if m:
        return f"{m.group(0)}: not available in this data."
    return "That split of a hitter's results isn't available in this data." if HITTER_SPLIT.search(question) else ""


def _run(tools: Any, calls: List[Call], name: str, args: Dict[str, Any]) -> None:
    calls.append({"tool": name, "args": args, "result": json.dumps(tools.call(name, args))})


def _counts(question: str) -> List[tuple]:
    found = [(int(b), int(s)) for b, s in re.findall(r"\b([0-3])-([0-2])\b", question)]
    return found + ([(3, 2)] if "full count" in question.lower() else [])


def _right_count(question: str, tools: Any, calls: List[Call]) -> None:
    """The model looked up a different count than the question's: look up the question's."""
    counts, made = _counts(question), [c for c in calls if c["tool"] in COUNT_TOOLS and isinstance(c.get("args"), dict)]
    if counts and made and not any((c["args"].get("balls"), c["args"].get("strikes")) in counts for c in made):
        b, s = counts[0]
        _run(tools, calls, made[-1]["tool"], {**made[-1]["args"], "balls": b, "strikes": s})


def _team_filter(question: str, tools: Any, calls: List[Call]) -> None:
    """The question names a team but the model ranked the whole league: rank that team's players."""
    teams = named(question, tools.teams())
    board = next((c for c in reversed(calls) if c["tool"] == "leaderboard" and isinstance(c.get("args"), dict)), None)
    if teams and board and not board["args"].get("team") and not str(board["args"].get("who", "")).startswith("team"):
        _run(tools, calls, "leaderboard", {**board["args"], "team": teams[0]})


def _pitcher_in(question: str, tools: Any, calls: List[Call]) -> Optional[str]:
    names = named(question, tools.pitchers())
    return names[0] if names else next((c["args"]["pitcher"] for c in calls if (c.get("args") or {}).get("pitcher")), None)


def _skipped_tool(question: str, tools: Any, calls: List[Call]) -> None:
    """A pitch-mix or zone-rules question the model answered with another tool: run the one it needs."""
    used, q = {c["tool"] for c in calls}, question.lower()
    pitcher = _pitcher_in(question, tools, calls)
    mix = AFTER.search(question) or re.search(r"throw (?:to|against)|left-handed|right-handed|lefties|righties|first.?pitch|start\w*"
                                               r"|main pitch|rely|lean on|secondary|best pitch|throw his|go-to|two strikes|miss bats"
                                               r"|swing.and.miss|tendenc|arsenal|velo", q)
    if mix and pitcher and "pitcher_arsenal" not in used and not _counts(question):
        _run(tools, calls, "pitcher_arsenal", {"pitcher": pitcher})
    heights = [int(h) for h in re.findall(r"\b(1[2-9]\d|2[0-2]\d) ?cm\b", q)]
    if heights:
        made = {(c.get("args") or {}).get("batter_height_cm") for c in calls if c["tool"] == "abs_rules"}
        for h in heights:
            if h not in made and float(h) not in made:
                _run(tools, calls, "abs_rules", {"batter_height_cm": h})
    elif re.search(r"\babs\b|\bzone\b(?! rate)|strike zone", q) and "abs_rules" not in used and not named(question, tools.batters()) \
            and not named(question, tools.pitchers()):
        _run(tools, calls, "abs_rules", {})


def _next_pitch(question: str, tools: Any, calls: List[Call]) -> None:
    """'What will he throw next after a curveball on 1-1?': the next-pitch model for that count and pitch."""
    pitcher, counts = named(question, tools.pitchers()), _counts(question)
    after = AFTER.search(question)
    if pitcher and counts and PREDICT.search(question) \
            and not any(c["tool"] == "predict_next_pitch" for c in calls):
        b, s = counts[0]
        _run(tools, calls, "predict_next_pitch", {"pitcher": pitcher[0], "balls": b, "strikes": s,
                                                  "previous_pitch": after.group(1).lower() if after else ""})


def _named_players(question: str, tools: Any, calls: List[Call]) -> None:
    """Profiles for the players the question names, when the model looked nothing up or didn't look them up."""
    looked = {(c.get("args") or {}).get(k) for c in calls for k in ("pitcher", "batter")}
    pitchers, batters = named(question, tools.pitchers()), named(question, tools.batters())
    several = len(pitchers) + len(batters) >= 2 and not _counts(question)
    if not calls or several:
        for p in (p for p in pitchers if p not in looked or not calls):
            _run(tools, calls, "pitcher_profile", {"pitcher": p})
        for b in (b for b in batters if b not in looked or not calls):
            _run(tools, calls, "batter_profile", {"batter": b})


def _plans(question: str, tools: Any, calls: List[Call]) -> None:
    """A count with a batter (and maybe a pitcher) named, but no pitch plan or take guide looked up: look one up."""
    batters, pitchers = named(question, tools.batters()), named(question, tools.pitchers())
    trait = r"protect|patient|disciplin|aggressive|free.?swing|contact hitter|power hitter"
    asks_trait = re.search(trait, question, re.I) or BEHAVIOR.search(question)
    if batters and asks_trait and not any(c["tool"] == "batter_profile" for c in calls):
        _run(tools, calls, "batter_profile", {"batter": batters[0]})
    if not batters or any(c["tool"] in COUNT_TOOLS and "error" not in _result(c) for c in calls):
        return
    stance = re.search(r"\b(?:take|swing|protect|patient|aggressive|lay off|sit on)\b", question, re.I)
    counts = _counts(question) or ([(0, 2), (1, 2), (2, 2), (3, 2)] if re.search(r"two strikes", question, re.I) else [])
    for b, s in counts:
        if stance:
            _run(tools, calls, "take_guide", {"batter": batters[0], "balls": b, "strikes": s})
        elif pitchers:
            _run(tools, calls, "recommend_pitch", {"pitcher": pitchers[0], "batter": batters[0], "balls": b, "strikes": s})
        else:
            _run(tools, calls, "attack_plan", {"batter": batters[0], "balls": b, "strikes": s})


def _board_fit(tools: Any, calls: List[Call], question: str) -> None:
    """A team question ranked players, or a 'most' question ranked lowest first (or the reverse): rank it right."""
    board = next((c for c in reversed(calls) if c["tool"] == "leaderboard" and isinstance(c.get("args"), dict)), None)
    if not board:
        return
    a = dict(board["args"])
    if re.search(r"\bwhich teams?\b|\bteams?'s?\b", question, re.I) and not str(a.get("who", "")).startswith("team"):
        a["who"], a["team"] = ("team_batting" if re.search(r"hitter|batter|offen", question, re.I) else "team_pitching"), ""
    low = re.search(r"\b(?:least|fewest|lowest|smallest|toughest to strike|hardest to strike)\b", question, re.I)
    high = re.search(r"\b(?:most|highest|biggest|largest|hardest throwers?|best)\b", question, re.I)
    a["order"] = "lowest" if low else "highest" if high else a.get("order", "highest")
    if a != board["args"]:
        _run(tools, calls, "leaderboard", a)


def _count_metric(tools: Any, calls: List[Call], question: str) -> None:
    """'Most walks' or 'walks drawn' asks for a count; the model ranked the rate."""
    board = next((c for c in reversed(calls) if c["tool"] == "leaderboard" and isinstance(c.get("args"), dict)), None)
    want = re.search(r"\b(?:most|fewest) (walks|strikeouts)\b|\b(walks) drawn\b", question, re.I)
    if board and want and board["args"].get("metric", "").endswith("_rate") and not str(board["args"].get("who", "")).startswith("team"):
        _run(tools, calls, "leaderboard", {**board["args"], "metric": (want.group(1) or want.group(2)).lower()})


def lookup(question: str, tools: Any, calls: List[Call]) -> None:
    """Run the lookups the question needs that the model skipped."""
    pitchers, plan = named(question, tools.pitchers()), next((c for c in calls if c["tool"] == "attack_plan"), None)
    if pitchers and plan and isinstance(plan.get("args"), dict):
        _run(tools, calls, "recommend_pitch", {**plan["args"], "pitcher": pitchers[0]})
    _plans(question, tools, calls)
    _right_count(question, tools, calls)
    _count_metric(tools, calls, question)
    _board_fit(tools, calls, question)
    _team_filter(question, tools, calls)
    _next_pitch(question, tools, calls)
    _skipped_tool(question, tools, calls)
    _named_players(question, tools, calls)
