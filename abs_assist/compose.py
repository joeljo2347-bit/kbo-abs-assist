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
from abs_assist.compare import BATTER_METRICS, PITCHER_METRICS

Call = Dict[str, Any]
Result = Dict[str, Any]
NOTHING = "I couldn't find that in the data. Ask about a pitcher, a batter, a team, a count or the ABS rules."
LABELS = {
    "chase_rate": "chase rate", "zone_swing_rate": "swing rate at strikes", "whiff_rate": "whiff rate (swings that miss)",
    "zone_height_cm": "zone height", "batting_average": "batting average", "home_runs": "home runs",
    "strikeout_rate": "strikeout rate", "walk_rate": "walk rate", "on_base_percentage": "on-base percentage",
    "slugging": "slugging", "in_zone_rate": "share of pitches in the zone", "fastball_kmh": "fastball speed",
    "strikes_lost_at_back_rate": "share of taken pitches lost as strikes at the back of the plate",
    "strikeouts": "strikeouts", "walks": "walks",
}
PITCH_QUESTION = (r"main pitch|throws? (?:the )?most|rely|lean on|go-to|start\w* .*off|first.?pitch|open\w* (?:at-bats|hitters)|best pitch"
                  r"|strikeout pitch|put.?away|what pitch|which pitch|each pitch|every pitch|all (?:his|of his) pitches|velo")
# (words in the question, stat, a "yes" means the stat is high) - first match wins, per kind of player.
BATTER_WORDS: List[Tuple[str, str, bool]] = [
    (r"how many strike ?outs", "strikeouts", True), (r"how many walks|walks drawn", "walks", True),
    (r"(?:hard|tough)\w* to strike", "strikeout_rate", False), (r"strike ?outs?|strikes? out", "strikeout_rate", True),
    (r"power|\bpop\b|home runs?|homers?|extra.?base", "home_runs", True), (r"slug|\bpop\b|power", "slugging", True),
    (r"on-base|\bobp\b|get on base", "on_base_percentage", True), (r"contact", "whiff_rate", False),
    (r"contact", "batting_average", True),
    (r"disciplin|patient|lay\w* off|chase|out of the zone|outside the zone|off the plate|bad pitches|expand", "chase_rate", False),
    (r"aggressive|free.?swing", "zone_swing_rate", True),
    (r"walk", "walk_rate", True), (r"miss|whiff", "whiff_rate", True),
    (r"batting average|hit for average|hitter for average", "batting_average", True),
    (r"abs zone|strike zone|his zone|zone (?:size|height)|(?:biggest|smallest|tallest) zone", "zone_height_cm", True),
]
PITCHER_WORDS: List[Tuple[str, str, bool]] = [
    (r"\bhard|velo|speed|\bfast(?:er|est)?\b", "fastball_kmh", True), (r"miss|whiff", "whiff_rate", True),
    (r"strike ?outs?|strikes? out", "strikeout_rate", True), (r"walk", "walk_rate", True), (r"chase", "chase_rate", True),
    (r"back of the plate|lose", "strikes_lost_at_back_rate", True),
    (r"throws? (?:a lot of |more |many )?strikes|in the zone|zone rate|command", "in_zone_rate", True),
]


def pct(x: Optional[float]) -> str:
    return "–" if x is None else f"{round(x * 100 + 1e-9, 1):.1f}%"  # the nudge keeps 0.0355 from showing as 3.5%


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
    found: Dict[str, bool] = {}
    for pattern, m, high in words:  # first match per stat wins, so a stat is never listed twice
        if m not in found and re.search(pattern, question, re.I):
            found[m] = high
    return list(found.items())[:3]


def standing(r: Result, metric: str) -> str:
    v = (r.get("compared_with_league") or {}).get(metric) or {}
    if "others" not in v:
        return ""
    lower, higher, n = v["others_lower"], v["others_higher"], v["others"]
    avg = f"league average {stat(metric, v['league_average'])}"
    if higher == 0 or lower == 0:
        return f"the {'highest' if higher == 0 else 'lowest'} of {n + 1} qualified players ({avg})"
    word = "higher than most" if lower > 0.6 * n else "lower than most" if higher > 0.6 * n else "about in the middle"
    return f"{word} ({lower} of {n} others are lower, {higher} higher; {avg})"


def value(r: Result, metric: str) -> Any:
    v = r.get(metric)
    return v if v is not None else ((r.get("compared_with_league") or {}).get(metric) or {}).get("value")


def yes_no(question: str, r: Result, metric: str, high_is_yes: bool) -> str:
    """'Yes.' / 'No.' / 'About average.' for an is/does question, from where he ranks."""
    if not re.match(r"\s*(?:is|does|do|are|can|has|will)\b", question, re.I) or re.search(r"\bor\b", question, re.I):
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
    if kind == "pitcher" and r.get("each_pitch") and re.search(f"{PITCH_QUESTION}|{fallback.PITCH_NAMES}", question, re.I):
        return pitch_choice(r, question)
    if kind == "batter" and r.get("by_situation") and fallback.BEHAVIOR.search(question):
        extra = [_part(r, m) for m, _ in asked_stats(question, kind) if m not in ("zone_swing_rate", "chase_rate")]
        return behavior(r, question) + (f" Also: {'; '.join(extra)}." if extra else "")
    votes = {m: yes_no(question, r, m, high) for m, high in wanted if m != "zone_height_cm"}
    leads = set(votes.values()) - {""}
    lead = leads.pop() if len(leads) == 1 else _mixed(votes) if leads else ""
    parts = [_part(r, m) for m, _ in wanted]
    return f"{lead}{_which(question, r)}{name}: " + "; ".join(parts) + "."


def _mixed(votes: Dict[str, str]) -> str:
    says = "; ".join(f"{LABELS.get(m, m)} says {v.strip().rstrip('.').lower()}" for m, v in votes.items() if v)
    return f"Mixed: {says}. "


def _part(r: Result, m: str) -> str:
    if m == "zone_height_cm" and r.get("zone_bottom_cm") is not None:
        return f"ABS zone {r['zone_bottom_cm']} to {r['zone_top_cm']} cm above the ground ({r['zone_height_cm']} cm tall)"
    return f"{LABELS.get(m, m)} {stat(m, value(r, m))}" + (f", {standing(r, m)}" if standing(r, m) else "")


def _which(question: str, r: Result) -> str:
    """'Patient hitter.' / 'Free swinger.' for a which-one question, from where his chase rate ranks."""
    if not (re.search(r"patient", question, re.I) and re.search(r"free.?swing|aggressive", question, re.I)):
        return ""
    v = (r.get("compared_with_league") or {}).get("chase_rate") or {}
    if "others" not in v:
        return ""
    if v["others_higher"] > 0.6 * v["others"]:
        return "A patient hitter: he chases less than most. "
    return "A free swinger: he chases more than most. " if v["others_lower"] > 0.6 * v["others"] else "In between. "


def pitch_choice(r: Result, question: str) -> str:
    """His main pitch, first-pitch choice or best swing-and-miss pitch, from the pitches in his profile."""
    pitches, name = r["each_pitch"], r["pitcher"]
    if re.search(r"first.?pitch|start\w* .*off|open\w* (?:at-bats|hitters)|0-0", question, re.I) and r.get("first_pitch_of_at_bat_mix"):
        mix = list(r["first_pitch_of_at_bat_mix"]["usage"].items())[:3]
        return f"{name} on the first pitch of an at-bat: " + ", ".join(f"{k} {pct(v)}" for k, v in mix) + "."
    detail = _pitch_detail(name, pitches, question)
    if detail:
        return detail
    if re.search(r"best|strikeout|put.?away|whiff|miss", question, re.I):
        used = [a for a in pitches if (a["usage"] or 0) >= 0.02 and a["whiff_rate"] is not None]
        top = max(used, key=lambda a: a["whiff_rate"]) if used else pitches[0]
        rates = ", ".join(f"{a['pitch']} {pct(a['whiff_rate'])}" for a in used)
        return (f"{name}'s pitch with the most swings and misses is his {top['pitch']} ({pct(top['whiff_rate'])} of swings, "
                f"thrown {pct(top['usage'])} of the time). Whiffs by pitch: {rates}.")
    main = pitches[0]
    rest = ", ".join(f"{a['pitch']} {pct(a['usage'])}" for a in pitches[1:4])
    return f"{name}'s main pitch is the {main['pitch']}: {pct(main['usage'])} of his pitches, {main['avg_kmh']} km/h on average. Then: {rest}."


def _movement(a: Result) -> str:
    return f", {a['hb_cm']} cm horizontal and {a['ivb_cm']} cm vertical break" if a.get("hb_cm") is not None else ""


def _pitch_detail(name: str, pitches: List[Result], question: str) -> str:
    """Every pitch's speed and whiff rate when asked for each pitch; one pitch's numbers when the question names it."""
    if re.search(r"each pitch|every pitch|all (?:his|of his) pitches|velo(?:city)? (?:on|of|for) (?:each|every|all)|speeds|movement"
                 r"|break(?:s)? down (?:his|\w+'?s) (?:pitches|arsenal)", question, re.I):
        used = [a for a in pitches if a["whiff_rate"] is not None and (a["usage"] or 0) >= 0.02]
        best = max(used, key=lambda a: a["whiff_rate"]) if used else None
        misses = f" The one that misses the most bats is the {best['pitch']} ({pct(best['whiff_rate'])})." if best else ""
        return f"{name}'s pitches: " + "; ".join(f"{a['pitch']} {a['avg_kmh']} km/h ({pct(a['usage'])} of pitches, "
                                                f"{pct(a['whiff_rate'])} whiffs{_movement(a)})" for a in pitches) + "." + misses
    named = [a for a in pitches if re.search(rf"\b{a['pitch']}s?\b", question, re.I)]
    if named and not fallback.AFTER.search(question):
        return " ".join(f"{name}'s {a['pitch']}: {pct(a['usage'])} of his pitches, {a['avg_kmh']} km/h, "
                        f"{pct(a['whiff_rate'])} of swings at it miss." for a in named)
    return ""


def behavior(r: Result, question: str) -> str:
    """What a hitter actually does in the count or situation asked about, against his overall habits."""
    count = re.search(r"\b([0-3])-([0-2])\b", question)
    where = (f"on {count.group(0)}", r["by_count"].get(count.group(0))) if count else _situation(question, r)
    if not where[1] or not where[1].get("pitches"):
        return f"There's no record of {r['batter']} in that count."
    s, o = where[1], r["overall_swings"]
    if re.search(r"spoil|foul", question, re.I):
        more = (s["foul_rate"] or 0) > (o["foul_rate"] or 0) + 0.02
        lead = (f"{'Yes' if more else 'Not especially'}: he fouls off {pct(s['foul_rate'])} of his swings {where[0]} "
                f"({pct(o['foul_rate'])} overall). ")
        return lead + _behavior_line(r, where, s, o)
    gap = (s["swing_rate"] or 0) - (o["swing_rate"] or 0)
    lead = ("" if where[0] == "overall" else "He swings more than usual here. " if gap > 0.03
            else "He swings less than usual here. " if gap < -0.03 else "About his usual swing rate here. ")
    return lead + _behavior_line(r, where, s, o)


def _behavior_line(r: Result, where: Tuple[str, Any], s: Result, o: Result) -> str:
    return (f"{r['batter']} {where[0]}: swings at {pct(s['swing_rate'])} of pitches ({pct(o['swing_rate'])} overall), "
            f"{pct(s['zone_swing_rate'])} of strikes and {pct(s['chase_rate'])} of balls; misses {pct(s['whiff_rate'])} of "
            f"swings and fouls off {pct(s['foul_rate'])} ({s['pitches']} pitches seen).")


def _situation(question: str, r: Result) -> Tuple[str, Any]:
    q, by = question.lower(), r["by_situation"]
    for words, key, label in ((r"first.?pitch|0-0|early", "first_pitch", "on the first pitch"),
                              (r"two strikes|2 strikes|protect|put.?away", "two_strikes", "with two strikes"),
                              (r"ahead|hitter'?s count", "hitters_counts", "in hitter's counts (more balls than strikes)"),
                              (r"behind|pitcher'?s count", "pitchers_counts", "when behind in the count")):
        if re.search(words, q):
            return label, by[key]
    return "overall", r["overall_swings"]


def comparison(results: List[Result], question: str) -> str:
    """Which of several players is higher (or lower, if the question asks 'less') on the stat asked about."""
    kind = "pitcher" if "pitcher" in results[0] else "batter"
    pitch = re.search(fallback.PITCH_NAMES, question, re.I)
    if kind == "pitcher" and pitch and not fallback.AFTER.search(question):
        return _pitch_comparison(results, pitch.group(0).lower())
    metric = (asked_stats(question, kind) or [("whiff_rate", True)])[0][0]
    rows = sorted(((r[kind], value(r, metric)) for r in results if value(r, metric) is not None), key=lambda nv: -nv[1])
    if len(rows) < 2:
        return ""
    listed = ", ".join(f"{n} {stat(metric, v)}" for n, v in rows)
    if re.search(r"\b(?:better|worse|best|worst)\b|\b(?:most|least|more|less) (?:disciplined|patient|selective|aggressive)", question, re.I):
        high_is_good = dict(asked_stats(question, kind)).get(metric, True)
        good, bad = (rows[0], rows[-1]) if high_is_good else (rows[-1], rows[0])
        best = bad if re.search(r"\bworse|\bworst|\bleast|\bless", question, re.I) else good
        return f"{best[0]} is {'worse' if best is bad else 'better'} by {LABELS.get(metric, metric)} ({listed})."
    low = bool(re.search(r"\b(?:less|fewer|lower|least|tougher to strike)\b", question, re.I))
    lead, word = (rows[-1], "lower") if low else (rows[0], "higher")
    return f"{lead[0]} is {word} on {LABELS.get(metric, metric)} ({listed})."


def _pitch_comparison(results: List[Result], pitch: str) -> str:
    """Several pitchers' same pitch: swings and misses, usage and speed."""
    rows = list({r["pitcher"]: (r["pitcher"], next((a for a in r.get("each_pitch") or r.get("arsenal") or []
                                                    if a["pitch"] == pitch), None)) for r in results}.values())
    have = sorted(((n, a) for n, a in rows if a and a["whiff_rate"] is not None), key=lambda na: -na[1]["whiff_rate"])
    missing = [n for n, a in rows if not a]
    total = {r["pitcher"]: r.get("pitches") or 0 for r in results}
    text = "; ".join(f"{n}'s {pitch} {pct(a['whiff_rate'])} whiffs ({pct(a['usage'])} of pitches, about "
                     f"{round((a['usage'] or 0) * total.get(n, 0))} thrown, {a['avg_kmh']} km/h)" for n, a in have)
    lead = (f"By swings and misses, {have[0][0]}'s {pitch} is the better one "
            f"({pct(have[0][1]['whiff_rate'])} vs {pct(have[1][1]['whiff_rate'])}). " if len(have) > 1 else "")
    return lead + text + "." + (f" {', '.join(missing)} doesn't throw one." if missing else "")


def arsenal(r: Result, question: str, args: Any = None) -> str:
    q = question.lower()
    after = fallback.AFTER.search(q)
    if after and after.group(1).lower() in r.get("next_pitch_after", {}):
        nxt = r["next_pitch_after"][after.group(1)]
        return f"After a {after.group(1)}, {r['pitcher']} throws next: " + ", ".join(f"{k} {pct(v)}" for k, v in list(nxt.items())[:3]) + "."
    situations = [(k, label) for k, label in (("vs_left_handed_batters", "against left-handed batters"),
                                              ("vs_right_handed_batters", "against right-handed batters"),
                                              ("with_two_strikes", "with two strikes"), ("first_pitch_of_at_bat", "on the first pitch"))
                  if re.search({"vs_left_handed_batters": r"left", "vs_right_handed_batters": r"right",
                                "with_two_strikes": r"two strikes|2 strikes|put.?away",
                                "first_pitch_of_at_bat": r"first.?pitch|start|open\w* (?:at-bats|hitters)"}[k], q)]
    if situations:
        return _situational(r, q, situations)
    return _arsenal_overview(r, q)


def _situational(r: Result, q: str, situations: List[Tuple[str, str]]) -> str:
    mix, misses = r["mix_by_situation"], re.search(r"miss|whiff", q)
    lines = [f"{label}: " + ", ".join(f"{k} {pct(v)}" + (f" ({pct(mix[key]['whiff_rate'].get(k))} whiffs)" if misses else "")
                                      for k, v in list(mix[key]["usage"].items())[:4]) for key, label in situations]
    keys = {k for k, _ in situations}
    sides = {"vs_left_handed_batters", "vs_right_handed_batters"}
    both = (" The data has no split that combines these; each is shown on its own."
            if keys & sides and keys - sides else "")
    usage = mix[situations[0][0]]["usage"]
    top = max(usage, key=lambda k: usage[k])
    lead = f"His go-to {situations[0][1]} is the {top} ({pct(usage[top])}). " if len(situations) == 1 else ""
    if misses:
        used = [a for a in r["arsenal"] if a["whiff_rate"] is not None and (a["usage"] or 0) >= 0.02]
        top_all = max(used, key=lambda a: a["whiff_rate"]) if used else None
        lead = (f"Overall, his best swing-and-miss pitch is the {top_all['pitch']} ({pct(top_all['whiff_rate'])}). " if top_all else "") + lead
    if misses and len(situations) == 1:
        whiffs = {k: v for k, v in mix[situations[0][0]]["whiff_rate"].items() if v is not None}
        best = max(whiffs, key=lambda k: whiffs[k]) if whiffs else None
        lead += f"His best swing-and-miss pitch {situations[0][1]} is the {best} ({pct(whiffs[best])}). " if best else ""
    return lead + f"{r['pitcher']}'s pitch mix " + "; ".join(lines) + "." + both


def _arsenal_overview(r: Result, q: str) -> str:
    pitches = r["arsenal"]
    detail = _pitch_detail(r["pitcher"], pitches, q)
    if detail:
        return detail
    main = pitches[0]
    text = f"{r['pitcher']}'s main pitch is the {main['pitch']} ({pct(main['usage'])} of his pitches, {main['avg_kmh']} km/h on average)."
    fastball, asked = next((a for a in pitches if a["pitch"] == "fastball"), None), False
    if fastball and re.search(r"\bhard|velo|speed|\bfast(?:er|est)?\b|how fast", q):
        text, asked = text + f" His fastball averages {fastball['avg_kmh']} km/h and tops out at {fastball['max_kmh']} km/h.", True
    if re.search(r"secondary", q) and r.get("secondary_with_most_whiffs"):
        candidates = [a for a in pitches if a["whiff_rate"] is not None
                      and (a["pitch"] != "fastball" if "fastball" in q else a is not pitches[0])]
        best = max(candidates, key=lambda a: a["whiff_rate"]) if candidates else pitches[0]
        word = "besides the fastball" if "fastball" in q else "after his main pitch"
        text, asked = text + f" His best pitch {word} for swings and misses is the {best['pitch']} ({pct(best['whiff_rate'])}).", True
    elif re.search(r"whiff|miss|strikeout pitch|strike.?out pitch|best pitch|put.?away", q):
        used = [a for a in pitches if (a["usage"] or 0) >= 0.02 and a["whiff_rate"] is not None]
        top = max(used, key=lambda a: a["whiff_rate"]) if used else None
        if top:
            text += f" The pitch that gets the most swings and misses is his {top['pitch']} ({pct(top['whiff_rate'])}, thrown {pct(top['usage'])})."
            asked = True
    others = ", ".join(f"{a['pitch']} {pct(a['usage'])}" for a in pitches[1:4])
    return text + ("" if asked or not others else f" He also throws: {others}.")


def plan(r: Result, question: str, args: Any = None) -> str:
    b = r["best"][0]
    who = (f" (for {r['pitcher']} against {r['batter']})" if r.get("pitcher")
           else " (against any pitcher in the league; no pitcher was named)")
    text = (f"On {r['count']}, throw a {b['pitch']}{who}: {pct(b['called_strike_chance_if_taken'])} called a strike if he takes it, "
            f"{pct(b['whiff_chance_if_swung_at'])} whiff if he swings, leaving him a run value of {b['batter_value_after_runs']} "
            f"(from {r['batter_value_now_runs']} before the pitch).")
    nxt = "; ".join(x["pitch"] for x in r["best"][1:3])
    avoid = "; ".join(x["pitch"] for x in r.get("worst", []))
    return text + (f" Next best: {nxt}." if nxt else "") + (f" Avoid these spots: {avoid}." if avoid else "")


def take(r: Result, question: str, args: Any = None) -> str:
    verdict, _, detail = str(r.get("summary", "")).removeprefix("Verdict: ").partition(". ")
    pitches = "; ".join(t["pitch"] for t in r.get("take", [])[:4])
    lay_off = f" The pitches he gains most by taking: {pitches}." if pitches else ""
    hunt = "; ".join(x["pitch"] for x in r.get("swing_at", []))
    if re.search(r"sit on|look for|hunt|swing at|what pitch", question, re.I) and hunt:
        lay_off += f" The pitches he gains most by swinging at: {hunt}."
    return f"{_take_lead(verdict, question)}On {r['count']}: {verdict}. {detail}{lay_off}"


def _take_lead(verdict: str, question: str) -> str:
    """A direct answer to 'should he take more / be more aggressive / swing'."""
    if re.search(r"protect", question, re.I):
        return "Protecting the plate here means swinging at strikes and still taking balls. "
    more_take = re.search(r"take (?:more|the)|more patient|be patient|lay off", question, re.I)
    more_swing = re.search(r"swing (?:more|at)|more aggressive|be aggressive", question, re.I)
    if not (more_take or more_swing):
        return ""
    if verdict.startswith("swing at strikes"):
        return "Mostly no: take balls, but swing at strikes. "
    if verdict.startswith("be selective"):
        return "Partly: take everything outside the zone and some pitches in it (counts below); swing at the other strikes. "
    patient = verdict.startswith("be patient")
    return ("Yes. " if patient == bool(more_take) else "No. ") if verdict.startswith(("be patient", "be aggressive")) else ""


def board(r: Result, question: str, args: Any = None) -> str:
    rows, m = r.get("ranking") or [], r["metric"]
    if not rows:
        return "Nobody qualifies for that ranking yet."
    kind = "teams" if str(r.get("who", "")).startswith("team") else (f"{(args or {})['team']} players" if (args or {}).get("team") else "players")
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
    if kinds and re.search(r"which pitch|what pitch|by pitch|on which", question, re.I):
        most = "; by pitch: " + ", ".join(f"{k} {v}" for k, v in kinds)
    return (f"{team} pitchers lost {r['strikes_lost']} strikes at the back of the plate: taken pitches inside the zone at the "
            f"middle of the plate but below it at the back, {pct(r['share_of_takes'])} of their {r['taken_pitches']:,} taken pitches{most}.")


def rules(r: Result, question: str, args: Any = None) -> str:
    q, z = question.lower(), r.get("zone_for_this_batter_2025")
    if z and re.search(r"\d ?cm", q):
        return (f"For a {z['batter_height_cm']:g} cm batter the ABS zone runs from {z['bottom_cm']} cm to {z['top_cm']} cm above "
                f"the ground: {z['zone_height_cm']} cm tall and {z['width_cm']} cm wide.")
    if re.search(r"2024|season", q):
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
    (r"\b(?:who|which)\b.*\b(?:most|least|fewest|highest|lowest|leads?|hardest|toughest|biggest|largest|smallest)\b|\btop\b|\bleaders?\b",
     ("leaderboard",)),
    (r"^(?!.*\b[0-3]-[0-2]\b).*\b(?:patient|disciplined|aggressive|free.?swing\w*|contact|power) hitter\b", ("batter_profile",)),
    (r"\b(?:who|which)\b.*\b(?:more|less|fewer|harder|higher|lower|better)\b", ("pitcher_profile", "batter_profile")),
    (fallback.BEHAVIOR.pattern, ("batter_profile", "take_guide")),
    (r"\b(?:take|lay\w* off|swing|patient on|aggressive on|sit on|protect)\b", ("take_guide",)),
    (fallback.PREDICT.pattern, ("predict_next_pitch", "pitcher_arsenal")),
    (r"\b[0-3]-[0-2]\b|full count|put.?away|what (?:should|do) (?:we|he) throw|the call|the plan", ("recommend_pitch", "attack_plan")),
    (r"^(?!.*\b[0-3]-[0-2]\b)" + fallback.AFTER.pattern, ("pitcher_arsenal",)),
    (r"after an? \w+|left|right|two strikes|first pitch|start|main pitch|best pitch|secondary|go-to|rely|lean on|arsenal|throw his",
     ("pitcher_arsenal", "pitcher_profile")),
    (r"back of the plate|lose", ("strikes_lost_at_back", "pitcher_profile")),
    (r"zone rate|in the zone|throws? (?:a lot of )?strikes", ("pitcher_profile", "leaderboard")),
    (r"\babs\b|strike zone|\bzone\b(?! rate)|rule|cm\b", ("abs_rules", "batter_profile")),
]


def pick(question: str, good: List[Call]) -> str:
    """The tool whose results answer this question best. Players named in the question are compared directly."""
    used = [c["tool"] for c in good]
    profiled = {(c.get("args") or {}).get(k) for c in good if c["tool"].endswith("_profile") for k in ("pitcher", "batter")}
    no_count = not re.search(r"\b[0-3]-[0-2]\b|full count", question, re.I)
    if no_count and len([n for n in profiled if n and n.lower() in question.lower()]) >= 2:
        return next(c["tool"] for c in good if c["tool"].endswith("_profile"))
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


def _one_board(boards: List[Call], question: str) -> Call:
    """The ranking that fits: of teams for a team question, of one team's players when a team is named."""
    def fit(c: Call) -> int:
        a = c.get("args") or {}
        team_question = bool(re.search(r"\bwhich teams?\b|\bteams?'s?\b", question, re.I))
        return 2 * (str(a.get("who", "")).startswith("team") == team_question) + bool(a.get("team"))
    return max(reversed(boards), key=fit)


def _height_gap(results: List[Result]) -> str:
    """How far the zone's top and bottom move between the heights asked about."""
    zones = sorted((r["zone_for_this_batter_2025"] for r in results if r.get("zone_for_this_batter_2025")),
                   key=lambda z: z["batter_height_cm"])
    if len(zones) < 2:
        return ""
    lo, hi = zones[0], zones[-1]
    return (f"From {lo['batter_height_cm']:g} cm to {hi['batter_height_cm']:g} cm, the top moves up "
            f"{hi['top_cm'] - lo['top_cm']:.1f} cm and the bottom {hi['bottom_cm'] - lo['bottom_cm']:.1f} cm.")


def _with_what_we_have(gap: str, question: str, calls: List[Call], tools: Any) -> str:
    """'ERA: not available' plus the named player's profile, so the coach still gets something useful."""
    have = [c for c in calls if c["tool"].endswith("_profile") and "error" not in result(c)]
    if not have:
        fallback.lookup(question, tools, have)
    profiles = [profile(result(c), "", c.get("args")) for c in have if "error" not in result(c)]
    return f"{gap} What it does have: {' '.join(dict.fromkeys(profiles))}" if profiles else gap


def _board_metric(question: str, calls: List[Call], tools: Any) -> None:
    """A ranking of a different stat than the question asks about ('out of the zone' is chasing): rank the right one."""
    board = next((c for c in reversed(calls) if c["tool"] == "leaderboard" and isinstance(c.get("args"), dict)), None)
    if not board:
        return
    batting = str(board["args"].get("who", "batters")) in ("batters", "team_batting")
    asked = [m for m, _ in asked_stats(question, "batter" if batting else "pitcher")
             if m in (BATTER_METRICS if batting else PITCHER_METRICS)]
    if asked and asked[0] != board["args"].get("metric"):
        args = {**board["args"], "metric": asked[0]}
        calls.append({"tool": "leaderboard", "args": args, "result": json.dumps(tools.call("leaderboard", args))})


def _several_counts(group: List[Call], good: List[Call], question: str) -> str:
    """Take-or-swing verdicts across several counts (e.g. every two-strike count), with what he actually does there."""
    verdicts = " ".join(f"On {result(c)['count']}: " + str(result(c)["summary"]).removeprefix("Verdict: ").split(". ")[0] + "."
                        for c in group)
    chase = next((behavior(result(c), "two strikes") if result(c).get("by_situation") else profile(result(c), "chase")
                  for c in good if c["tool"] == "batter_profile"), "")
    lead = _take_lead(str(result(group[0])["summary"]).removeprefix("Verdict: ").split(". ")[0], question)
    return f"{lead}{verdicts} {chase}".strip()


def answer(question: str, calls: List[Call], tools: Any, previous: Optional[List[Call]] = None) -> str:
    """The reply the coach shows: written from the results of the tools called for this question (or, for a
    follow-up that called none, the previous question's)."""
    gap = fallback.unavailable(question)
    if gap:
        return _with_what_we_have(gap, question, calls, tools)
    fallback.lookup(question, tools, calls)
    _board_metric(question, calls, tools)
    use = calls or list(previous or [])
    good = [c for c in use if c["tool"] in WRITERS and result(c) and "error" not in result(c)]
    if not good:
        # Only errors a coach can act on (a name not found, the wrong kind of player) are worth showing.
        errors = [str(result(c).get("error")) for c in use if re.match(r"No |\w.* is a ", str(result(c).get("error", "")))]
        return errors[-1] if errors else NOTHING
    tool = pick(question, good)
    group = list({json.dumps([c["tool"], c.get("args")], sort_keys=True): c for c in good if c["tool"] == tool}.values())
    if tool == "leaderboard":
        group = [_one_board(group, question)]
    group = _for_count(group, question)
    if tool == "take_guide" and len(group) > 1:
        return _several_counts(group, good, question)
    texts = [_write(c, question) for c in group]
    if tool in ("pitcher_profile", "batter_profile", "pitcher_arsenal") and len(group) > 1:
        lead = comparison([result(c) for c in group], question)
        texts = [lead] if lead.startswith("By swings and misses") else [lead, *texts]  # a pitch comparison says it all
    if tool == "abs_rules":
        texts.append(_height_gap([result(c) for c in group]))
    return " ".join(dict.fromkeys(t for t in texts if t)) or NOTHING
