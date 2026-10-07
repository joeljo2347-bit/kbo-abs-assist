"""The coach's answer, written in code from the tool results the model chose.

The model's job is to pick tools and arguments; its own text is never shown. This module reads the
question for what it asks (a yes or no, a comparison, a split, a count) and answers it from the
results, so every number and claim in an answer is one the tools returned.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from abs_assist import fallback

Call = Dict[str, Any]
Result = Dict[str, Any]
NOTHING = "I couldn't find that in the data. Ask about a pitcher, a batter, a team, a count or the ABS rules."
LABELS = {
    "chase_rate": "chase rate", "zone_swing_rate": "swing rate at strikes", "whiff_rate": "whiff rate (swings that miss)",
    "zone_height_cm": "zone height", "batting_average": "batting average", "home_runs": "home runs",
    "strikeout_rate": "strikeout rate", "walk_rate": "walk rate", "on_base_percentage": "on-base percentage",
    "slugging": "slugging", "in_zone_rate": "share of pitches in the zone", "fastball_kmh": "fastball speed",
    "strikes_lost_at_back_rate": "share of taken pitches lost as strikes at the back of the plate",
}
# (words in the question, stat, a "yes" means the stat is high) - first match wins, per kind of player.
BATTER_WORDS: List[Tuple[str, str, bool]] = [
    (r"(?:hard|tough)\w* to strike", "strikeout_rate", False), (r"strike ?outs?|strikes? out", "strikeout_rate", True),
    (r"power|home runs?|homers?", "home_runs", True), (r"slug", "slugging", True),
    (r"on-base|\bobp\b|get on base", "on_base_percentage", True), (r"contact", "whiff_rate", False),
    (r"disciplin|patient|lay\w* off|chase", "chase_rate", False), (r"aggressive|free.?swing", "zone_swing_rate", True),
    (r"walk", "walk_rate", True), (r"miss|whiff", "whiff_rate", True), (r"average|hitter for", "batting_average", True),
    (r"zone", "zone_height_cm", True),
]
PITCHER_WORDS: List[Tuple[str, str, bool]] = [
    (r"hard|velo|fast|speed", "fastball_kmh", True), (r"miss|whiff", "whiff_rate", True),
    (r"strike ?outs?|strikes? out", "strikeout_rate", True), (r"walk", "walk_rate", True), (r"chase", "chase_rate", True),
    (r"back of the plate|lose", "strikes_lost_at_back_rate", True),
    (r"strikes|in the zone|zone rate|command", "in_zone_rate", True),
]


def pct(x: Optional[float]) -> str:
    return "–" if x is None else f"{x * 100:.1f}%"


def stat(metric: str, v: Any) -> str:
    if v is None:
        return "–"
    if metric in ("batting_average", "on_base_percentage", "slugging"):
        return f"{v:.3f}".lstrip("0")
    if metric.endswith("_rate"):
        return pct(v)
    unit = {"fastball_kmh": " km/h", "zone_height_cm": " cm"}.get(metric, "")
    return f"{round(v, 1):g}{unit}"


def result(call: Call) -> Result:
    try:
        out = json.loads(call["result"])
    except (ValueError, KeyError, TypeError):
        return {}
    return out if isinstance(out, dict) else {}


def asked_stats(question: str, kind: str) -> List[Tuple[str, bool]]:
    """The stats the question is about, with whether a 'yes' means high."""
    words = BATTER_WORDS if kind == "batter" else PITCHER_WORDS
    found = [(m, high) for pattern, m, high in words if re.search(pattern, question, re.I)]
    return list(dict.fromkeys(found))[:2]


def standing(r: Result, metric: str) -> str:
    v = (r.get("compared_with_league") or {}).get(metric) or {}
    if "others" not in v:
        return ""
    lower, higher, n = v["others_lower"], v["others_higher"], v["others"]
    word = "higher than most" if lower > 0.6 * n else "lower than most" if higher > 0.6 * n else "about in the middle"
    return f"{word} ({lower} of {n} others are lower, {higher} higher; league average {stat(metric, v['league_average'])})"


def value(r: Result, metric: str) -> Any:
    v = r.get(metric)
    return v if v is not None else ((r.get("compared_with_league") or {}).get(metric) or {}).get("value")


def yes_no(question: str, r: Result, metric: str, high_is_yes: bool) -> str:
    """'Yes.' / 'No.' / 'About average.' for an is/does question, from where he ranks."""
    if not re.match(r"\s*(?:is|does|do|are|can|has|will)\b", question, re.I):
        return ""
    v = (r.get("compared_with_league") or {}).get(metric) or {}
    if "others" not in v:
        return ""
    high, low = v["others_lower"] > 0.6 * v["others"], v["others_higher"] > 0.6 * v["others"]
    if not (high or low):
        return "About average. "
    return "Yes. " if high == high_is_yes else "No. "


def profile(r: Result, question: str, args: Any = None) -> str:
    kind = "pitcher" if "pitcher" in r else "batter"
    name = r[kind]
    wanted = asked_stats(question, kind) or [(m, True) for m in (
        ("in_zone_rate", "whiff_rate", "fastball_kmh") if kind == "pitcher" else ("chase_rate", "whiff_rate", "batting_average"))]
    lead = yes_no(question, r, *wanted[0]) if len(wanted) == 1 else ""
    parts = [f"{LABELS.get(m, m)} {stat(m, value(r, m))}" + (f", {standing(r, m)}" if standing(r, m) else "")
             for m, _ in wanted]
    note = f" ({r['note']})" if r.get("note") else ""
    return f"{lead}{name}{note}: " + "; ".join(parts) + "."


def comparison(results: List[Result], question: str) -> str:
    """Which of several players is higher (or lower, if the question asks 'less') on the stat asked about."""
    kind = "pitcher" if "pitcher" in results[0] else "batter"
    metric = (asked_stats(question, kind) or [("whiff_rate", True)])[0][0]
    rows = sorted(((r[kind], value(r, metric)) for r in results if value(r, metric) is not None), key=lambda nv: -nv[1])
    if len(rows) < 2:
        return ""
    low = bool(re.search(r"\b(?:less|fewer|lower|least|tougher to strike)\b", question, re.I))
    lead, word = (rows[-1], "lower") if low else (rows[0], "higher")
    listed = ", ".join(f"{n} {stat(metric, v)}" for n, v in rows)
    return f"{lead[0]} is {word} on {LABELS.get(metric, metric)} ({listed})."


def arsenal(r: Result, question: str, args: Any = None) -> str:
    q, mix = question.lower(), r.get("mix_by_situation") or {}
    after = re.search(r"after an? (\w+)", q)
    if after and after.group(1) in r.get("next_pitch_after", {}):
        nxt = r["next_pitch_after"][after.group(1)]
        return f"After a {after.group(1)}, {r['pitcher']} throws next: " + ", ".join(f"{k} {pct(v)}" for k, v in list(nxt.items())[:3]) + "."
    situations = [(k, label) for k, label in (("vs_left_handed_batters", "against left-handed batters"),
                                              ("vs_right_handed_batters", "against right-handed batters"),
                                              ("with_two_strikes", "with two strikes"), ("first_pitch_of_at_bat", "on the first pitch"))
                  if re.search({"vs_left_handed_batters": r"left", "vs_right_handed_batters": r"right",
                                "with_two_strikes": r"two strikes|2 strikes|put.?away", "first_pitch_of_at_bat": r"first pitch|start"}[k], q)]
    if situations:
        lines = [f"{label}: " + ", ".join(f"{k} {pct(v)}" for k, v in list(mix[key].items())[1:4]) for key, label in situations]
        both = " The data has no split that combines these; each is shown on its own." if len(situations) > 1 else ""
        return f"{r['pitcher']}'s pitch mix " + "; ".join(lines) + "." + both
    return _arsenal_overview(r, q)


def _arsenal_overview(r: Result, q: str) -> str:
    pitches = r["arsenal"]
    main = pitches[0]
    text = f"{r['pitcher']}'s main pitch is the {main['pitch']} ({pct(main['usage'])} of his pitches, {main['avg_kmh']} km/h on average)."
    fastball = next((a for a in pitches if a["pitch"] == "fastball"), None)
    if fastball and re.search(r"hard|velo|speed|fast", q):
        text += f" His fastball averages {fastball['avg_kmh']} km/h and tops out at {fastball['max_kmh']} km/h."
    if re.search(r"whiff|miss|strikeout pitch|strike.?out pitch|best pitch|put.?away", q):
        used = [a for a in pitches if (a["usage"] or 0) >= 0.05 and a["whiff_rate"] is not None]
        top = max(used, key=lambda a: a["whiff_rate"]) if used else None
        return text + (f" The pitch that gets the most swings and misses is his {top['pitch']} ({pct(top['whiff_rate'])} of swings)." if top else "")
    others = ", ".join(f"{a['pitch']} {pct(a['usage'])}" for a in pitches[1:4])
    return text + (f" He also throws: {others}." if others else "")


def plan(r: Result, question: str, args: Any = None) -> str:
    b = r["best"][0]
    who = "" if (args or {}).get("pitcher") else " (against any pitcher in the league; no pitcher was named)"
    text = (f"On {r['count']}, throw a {b['pitch']}{who}: {pct(b['called_strike_chance_if_taken'])} called a strike if he takes it, "
            f"{pct(b['whiff_chance_if_swung_at'])} whiff if he swings, leaving him {b['batter_value_after_runs']} expected runs "
            f"(from {r['batter_value_now_runs']} before the pitch).")
    nxt = ", ".join(x["pitch"] for x in r["best"][1:3])
    avoid = "; ".join(x["pitch"] for x in r.get("worst", []))
    return text + (f" Next best: {nxt}." if nxt else "") + (f" Avoid exactly these: {avoid}." if avoid else "")


def take(r: Result, question: str, args: Any = None) -> str:
    verdict, _, detail = str(r.get("summary", "")).removeprefix("Verdict: ").partition(". ")
    pitches = "; ".join(t["pitch"] for t in r.get("take", [])[:4])
    lay_off = f" The pitches he gains most by taking: {pitches}." if pitches else ""
    return f"On {r['count']}: {verdict}. {detail}{lay_off}"


def board(r: Result, question: str, args: Any = None) -> str:
    rows, m = r.get("ranking") or [], r["metric"]
    if not rows:
        return "Nobody qualifies for that ranking yet."
    kind = "teams" if str(r.get("who", "")).startswith("team") else "players"
    word = "Lowest" if str(r.get("order", "")).startswith("lowest") else "Highest"
    top = ", then ".join(x["name"] + ("" if x["name"] == x.get("team") else f" ({x['team']})") + f" {stat(m, x[m])}" for x in rows[:3])
    tied = [x["name"] for x in rows[1:] if x[m] == rows[0][m]]
    tie = f" {rows[0]['name']} is tied with {', '.join(tied)}." if tied else ""
    return f"{word} {LABELS.get(m, m)} among {kind}: {top}.{tie} League average: {stat(m, r.get('league_average'))}."


def predict(r: Result, question: str, args: Any = None) -> str:
    probs = list((r.get("probabilities") or {}).items())
    a = args or {}
    where = f" on {a.get('balls')}-{a.get('strikes')}" + (f" after a {a['previous_pitch']}" if a.get("previous_pitch") else "")
    return f"{a.get('pitcher', 'He')}{where}, most likely next: " + ", then ".join(f"{k} ({pct(v)})" for k, v in probs[:3]) + "."


def lost(r: Result, question: str, args: Any = None) -> str:
    team = (args or {}).get("team") or "All teams'"
    kinds = list(r.get("by_pitch_type", {}).items())
    most = f"; the most on the {kinds[0][0]} ({kinds[0][1]} of {r['strikes_lost']})" if kinds else ""
    return (f"{team} pitchers lost {r['strikes_lost']} strikes at the back of the plate: taken pitches inside the zone at the "
            f"middle of the plate but below it at the back, {pct(r['share_of_takes'])} of their {r['taken_pitches']:,} taken pitches{most}.")


def rules(r: Result, question: str, args: Any = None) -> str:
    q, z = question.lower(), r.get("zone_for_this_batter_2025")
    if z:
        return (f"For a {z['batter_height_cm']:g} cm batter the ABS zone runs from {z['bottom_cm']} cm to {z['top_cm']} cm above "
                f"the ground: {z['zone_height_cm']} cm tall and {z['width_cm']} cm wide.")
    if re.search(r"2024|2025|change|differ", q):
        c = r["change_2024_to_2025"]
        moved = abs(c["top_moved_share_of_height"]) * 100
        return f"From 2024 to 2025 the zone kept {c['summary']}: top and bottom both moved by {moved:.1f}% of height."
    if re.search(r"low|curve|ball|back|drop", q):
        low = r["low_pitches"]
        rates = ", ".join(f"{k} {pct(v['called_ball_rate'])}" for k, v in sorted(low.items(), key=lambda kv: -(kv[1]["called_ball_rate"] or 0)))
        return ("ABS checks the bottom of the zone at the middle and again at the back of the plate, and a breaking ball is still "
                f"dropping in between. Taken pitches in the bottom tenth of the zone at the middle, called balls: {rates}.")
    top, bottom = r["zone_by_season"]["2025"]["top_share_of_height"], r["zone_by_season"]["2025"]["bottom_share_of_height"]
    return (f"The 2025 ABS zone runs from {pct(bottom)} to {pct(top)} of the batter's height, so taller batters get a higher, taller "
            f"zone; it is {r['width_cm']} cm wide for everyone, checked at the middle and (top and bottom) at the back of the plate.")


def players(r: Result, question: str, args: Any = None) -> str:
    found = [f"{k}: {', '.join(v)}" for k, v in r.items() if k in ("pitchers", "batters") and v]
    return ("Found " + "; ".join(found) + ".") if found else NOTHING


WRITERS: Dict[str, Callable[[Result, str, Any], str]] = {
    "pitcher_profile": profile, "batter_profile": profile, "pitcher_arsenal": arsenal, "recommend_pitch": plan,
    "attack_plan": plan, "take_guide": take, "leaderboard": board, "predict_next_pitch": predict,
    "strikes_lost_at_back": lost, "abs_rules": rules, "find_players": players,
}


# What the question is about -> the tool results that answer it, in order of preference.
INTENTS: List[Tuple[str, Tuple[str, ...]]] = [
    (r"\bwhich teams?\b|\bteams?'s?\b", ("leaderboard", "strikes_lost_at_back")),
    (r"\b(?:who|which)\b.*\b(?:most|least|fewest|highest|lowest|leads?|hardest|toughest)\b|\btop\b|\bleaders?\b", ("leaderboard",)),
    (r"\b(?:who|which)\b.*\b(?:more|less|fewer|harder|higher|lower|better)\b", ("pitcher_profile", "batter_profile")),
    (r"\b(?:take|lay\w* off|swing|patient on|aggressive on|sit on|protect)\b", ("take_guide",)),
    (r"\b[0-3]-[0-2]\b|full count|put.?away|what (?:should|do) (?:we|he) throw|the call|the plan", ("recommend_pitch", "attack_plan")),
    (r"likely to throw|throw next|next pitch", ("predict_next_pitch", "pitcher_arsenal")),
    (r"after an? \w+|left|right|two strikes|first pitch|start|main pitch|best pitch|go-to|rely|lean on|arsenal|throw his",
     ("pitcher_arsenal", "pitcher_profile")),
    (r"back of the plate|lose", ("strikes_lost_at_back", "pitcher_profile")),
    (r"\bzone\b|\babs\b|rule|cm\b", ("abs_rules", "batter_profile")),
]


def pick(question: str, good: List[Call]) -> str:
    """The tool whose results answer this question best."""
    used = [c["tool"] for c in good]
    for pattern, preferred in INTENTS:
        if re.search(pattern, question, re.I):
            for tool in preferred:
                if tool in used:
                    return tool
    rest = [t for t in used if t != "find_players"]
    return (rest or used)[-1]


def _for_count(calls: List[Call], question: str) -> List[Call]:
    """Calls for the count the question names (a call for another count is a mistake), else all of them."""
    counts = {(int(b), int(s)) for b, s in re.findall(r"\b([0-3])-([0-2])\b", question)} | (
        {(3, 2)} if "full count" in question.lower() else set())
    keep = [c for c in calls if not counts or (c.get("args", {}).get("balls"), c.get("args", {}).get("strikes")) in counts]
    return keep or calls


def _write(call: Call, question: str) -> str:
    try:
        return WRITERS[call["tool"]](result(call), question, call.get("args"))
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return ""


def answer(question: str, calls: List[Call], tools: Any, previous: Optional[List[Call]] = None) -> str:
    """The reply the coach shows: written from the results of the tools called for this question (or, for a
    follow-up that called none, the previous question's)."""
    gap = fallback.unavailable(question)
    if gap:
        return gap
    fallback.lookup(question, tools, calls)
    use = calls or list(previous or [])
    good = [c for c in use if c["tool"] in WRITERS and result(c) and "error" not in result(c)]
    if not good:
        # Only errors a coach can act on (a name not found, the wrong kind of player) are worth showing.
        errors = [str(result(c).get("error")) for c in use if re.match(r"No |\w.* is a ", str(result(c).get("error", "")))]
        return errors[-1] if errors else NOTHING
    tool = pick(question, good)
    group = list({json.dumps([c["tool"], c.get("args")], sort_keys=True): c for c in good if c["tool"] == tool}.values())
    texts = [_write(c, question) for c in _for_count(group, question)]
    if tool in ("pitcher_profile", "batter_profile") and len(group) > 1:
        texts.insert(0, comparison([result(c) for c in group], question))
    return " ".join(dict.fromkeys(t for t in texts if t)) or NOTHING
