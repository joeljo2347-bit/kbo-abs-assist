"""Context for any number: league averages, rankings across players or teams, a pitcher's arsenal.

Every player and team is summarized in one pass over the data, cached until the data changes.
Rates are 0-1. Batting stats (AVG, HR, K, BB) come from how each plate appearance ended.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from abs_assist.analyze import HALF, RULES, Row, height_in_zone, lost_at_back, rows

HITS = {"single", "double", "home_run"}
BATTER_METRICS = {
    "chase_rate": "share of pitches outside the zone he swung at",
    "zone_swing_rate": "share of in-zone pitches he swung at",
    "whiff_rate": "share of his swings that missed",
    "zone_height_cm": "height of his ABS zone (top minus bottom), cm",
    "batting_average": "hits per at-bat", "home_runs": "home runs", "strikeout_rate": "strikeouts per plate appearance",
    "walk_rate": "walks per plate appearance", "on_base_percentage": "times on base (hits and walks) per plate appearance",
    "slugging": "total bases per at-bat",
}
PITCHER_METRICS = {
    "in_zone_rate": "share of his pitches inside the ABS zone", "chase_rate": "share of his pitches outside the zone that were swung at",
    "whiff_rate": "share of swings against him that missed", "fastball_kmh": "average fastball speed, km/h",
    "strikes_lost_at_back_rate": "share of his taken pitches that were strikes at the middle of the plate but balls at the back",
    "strikeout_rate": "strikeouts per batter faced", "walk_rate": "walks per batter faced",
}
# What a value above the league average means, in words, so the direction is never guessed.
HIGH_MEANS = {
    ("batter", "chase_rate"): "swings at more pitches outside the zone (worse at laying off)",
    ("batter", "zone_swing_rate"): "swings at more strikes (more aggressive in the zone)",
    ("batter", "whiff_rate"): "misses more often when he swings",
    ("batter", "zone_height_cm"): "a taller ABS zone", ("batter", "batting_average"): "a better hitter for average",
    ("batter", "home_runs"): "more home runs", ("batter", "strikeout_rate"): "strikes out more often",
    ("batter", "walk_rate"): "walks more often", ("batter", "on_base_percentage"): "gets on base more often",
    ("batter", "slugging"): "hits for more power",
    ("pitcher", "in_zone_rate"): "throws more pitches in the zone", ("pitcher", "chase_rate"): "gets more chases",
    ("pitcher", "whiff_rate"): "gets more swings and misses", ("pitcher", "fastball_kmh"): "throws harder",
    ("pitcher", "strikes_lost_at_back_rate"): "loses more strikes at the back of the plate",
    ("pitcher", "strikeout_rate"): "strikes out more batters", ("pitcher", "walk_rate"): "walks more batters",
}
QUALIFY = 0.3  # qualified: at least this share of the median player's pitches (scales with how much data there is)


def _ratio(part: float, whole: float) -> Optional[float]:
    return round(part / whole, 3) if whole else None


def batter_line(data: List[Row]) -> Dict[str, Any]:
    zone, out = [r for r in data if r["abs_strike"]], [r for r in data if not r["abs_strike"]]
    pas = [r["pa_result"] for r in data if r["pa_result"]]
    walks, hits = pas.count("walk"), sum(p in HITS for p in pas)
    bottom, top = RULES.bounds(data[0]["batter_height_cm"])
    return {"team": data[0]["batter_team"], "pitches": len(data), "plate_appearances": len(pas), "zone_height_cm": round(top - bottom, 1),
            "zone_swing_rate": _ratio(sum(r["swing"] for r in zone), len(zone)),
            "chase_rate": _ratio(sum(r["swing"] for r in out), len(out)),
            "whiff_rate": _ratio(sum(r["result"] == "whiff" for r in data), sum(r["swing"] for r in data)),
            "batting_average": _ratio(hits, len(pas) - walks), "hits": hits, "home_runs": pas.count("home_run"),
            "strikeouts": pas.count("strikeout"), "walks": walks,
            "strikeout_rate": _ratio(pas.count("strikeout"), len(pas)), "walk_rate": _ratio(walks, len(pas)),
            "on_base_percentage": _ratio(hits + walks, len(pas)), "slugging": _slugging(pas)}


def _slugging(pas: List[str]) -> Optional[float]:
    """Total bases per at-bat (the simulation has no triples or hit-by-pitches)."""
    bases = pas.count("single") + 2 * pas.count("double") + 4 * pas.count("home_run")
    return _ratio(bases, len(pas) - pas.count("walk"))


def pitcher_line(data: List[Row]) -> Dict[str, Any]:
    zone, swings = [r for r in data if r["abs_strike"]], [r for r in data if r["swing"]]
    fastballs = [r["kmh"] for r in data if r["pitch_type"] == "fastball"]
    lost, pas = lost_at_back(data), [r["pa_result"] for r in data if r["pa_result"]]
    return {"team": data[0]["pitcher_team"], "pitches": len(data), "in_zone_rate": _ratio(len(zone), len(data)),
            "chase_rate": _ratio(sum(not r["abs_strike"] for r in swings), len(data) - len(zone)),
            "whiff_rate": _ratio(sum(r["result"] == "whiff" for r in swings), len(swings)),
            "fastball_kmh": round(sum(fastballs) / len(fastballs), 1) if fastballs else None,
            "strikes_lost_at_back": lost["strikes_lost"], "strikes_lost_at_back_rate": lost["share_of_takes"],
            "batters_faced": len(pas), "strikeout_rate": _ratio(pas.count("strikeout"), len(pas)),
            "walk_rate": _ratio(pas.count("walk"), len(pas))}


class League:
    """Every batter, pitcher and team summarized once; rebuilt when the number of pitches changes."""

    def __init__(self, db: sqlite3.Connection):
        self.db, self.size = db, -1
        self.lines: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def _fresh(self) -> Dict[str, Dict[str, Any]]:
        size = self.db.execute("SELECT COUNT(*) FROM pitches").fetchone()[0]
        if size != self.size:
            groups: Dict[Tuple[str, str], List[Row]] = defaultdict(list)
            for r in rows(self.db):
                for key in (("batter", r["batter"]), ("pitcher", r["pitcher"]),
                            ("team_batting", r["batter_team"]), ("team_pitching", r["pitcher_team"])):
                    groups[key].append(r)
            line = {"batter": batter_line, "pitcher": pitcher_line, "team_batting": batter_line, "team_pitching": pitcher_line}
            self.lines = {kind: {name: line[kind](d) for (k, name), d in groups.items() if k == kind} for kind in line}
            self.size = size
        return self.lines

    def cached(self, key: str, build: Any) -> Any:
        """Any other league-wide result, rebuilt with the rest when the data changes."""
        lines = self._fresh()
        if key not in lines:
            lines[key] = build()
        return lines[key]

    def qualified(self, kind: str) -> Dict[str, Dict[str, Any]]:
        pool = self._fresh()[kind]
        counts = sorted(v["pitches"] for v in pool.values())
        floor = QUALIFY * counts[len(counts) // 2] if counts else 0
        return {n: v for n, v in pool.items() if v["pitches"] >= floor}

    def average(self, kind: str, metric: str) -> Optional[float]:
        values = [v[metric] for v in self.qualified(kind).values() if v.get(metric) is not None]
        return round(sum(values) / len(values), 3) if values else None

    def rank(self, kind: str, name: str, metric: str) -> Optional[Dict[str, int]]:
        """How many qualified others are above and below him."""
        pool = self.qualified(kind)
        if name not in pool or pool[name].get(metric) is None:
            return None
        mine = pool[name][metric]
        others = [v[metric] for n, v in pool.items() if n != name and v.get(metric) is not None]
        return {"others_higher": sum(o > mine for o in others), "others_lower": sum(o < mine for o in others),
                "others": len(others)}


def verdict(league: League, kind: str, name: str, metric: str) -> Dict[str, Any]:
    """His value against the league, with the conclusion written in code: above, below or about average,
    and what that means for this stat."""
    value = league.qualified(kind).get(name, {}).get(metric)
    avg, rank = league.average(kind, metric), league.rank(kind, name, metric)
    if value is None or avg is None or rank is None:
        return {"value": value, "league_average": avg, "verdict": "not enough data to compare"}
    lower, higher, n = rank["others_lower"], rank["others_higher"], rank["others"]
    if lower > 0.6 * n:
        word = f"higher than most ({lower} of {n} others are lower): {HIGH_MEANS[(kind, metric)]}"
    elif higher > 0.6 * n:
        word = f"lower than most ({higher} of {n} others are higher): the opposite of {HIGH_MEANS[(kind, metric)]}"
    else:
        word = f"in the middle of the pack ({lower} of {n} others are lower, {higher} higher)"
    side = "above" if value > avg else "below" if value < avg else "at"
    return {"value": value, "league_average": avg, **rank, "verdict": word,
            "vs_average": f"{side} the league average (a few extreme players can pull the average away from the middle)"}


def context(league: League, kind: str, name: str, metrics: Dict[str, str]) -> Dict[str, Any]:
    """His standing on each metric against qualified players."""
    return {m: verdict(league, kind, name, m) for m in metrics}


def leaderboard(league: League, metric: str, who: str = "batters", team: str = "", top: int = 10,
                order: str = "highest") -> Dict[str, Any]:
    """Players (or teams) ranked by a metric, highest first (or lowest first: 'toughest to strike out')."""
    kind = {"batters": "batter", "pitchers": "pitcher", "team_batting": "team_batting", "team_pitching": "team_pitching"}.get(who)
    metrics = BATTER_METRICS if kind in ("batter", "team_batting") else PITCHER_METRICS
    if kind is None or metric not in metrics:
        return {"error": f"who must be batters, pitchers, team_batting or team_pitching; metric one of {', '.join(metrics)}."}
    pool = league.qualified(kind)
    if team and kind in ("batter", "pitcher"):
        side = "batter_team" if kind == "batter" else "pitcher_team"
        names = {n for (n,) in league.db.execute(f"SELECT DISTINCT {kind} FROM pitches WHERE {side} = ?", (team,))}
        pool = {n: v for n, v in pool.items() if n in names}
    sign = 1 if order == "lowest" else -1
    ranked = sorted((n for n in pool if pool[n].get(metric) is not None), key=lambda n: sign * pool[n][metric])
    return {"how_to_read": f"{metric}: {metrics[metric]}. {order.capitalize()} first; everyone qualified is counted.",
            "metric": metric, "order": f"{order} first", "who": who, "team": team or "all",
            "note": ("These are teams." if kind.startswith("team") else
                     "These are individual players, not teams: for a team ranking use who=team_batting or team_pitching."),
            "qualified": len(ranked), "league_average": league.average(kind, metric),
            "ranking": [{"name": n, "team": pool[n].get("team", n), metric: pool[n][metric], "pitches": pool[n]["pitches"]}
                        for n in ranked[:top]]}


def arsenal(db: sqlite3.Connection, pitcher: str) -> Dict[str, Any]:
    """Each pitch type: usage, speed, movement and whiffs, plus what he throws after each pitch."""
    data = sorted(rows(db, pitcher=pitcher), key=lambda r: r["id"])
    if not data:
        return {"error": f"No pitches for {pitcher}."}
    by_type: Dict[str, List[Row]] = defaultdict(list)
    after: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for prev, r in zip([None, *data], data):
        by_type[r["pitch_type"]].append(r)
        if prev and prev["game_id"] == r["game_id"] and prev["batter"] == r["batter"]:
            after[prev["pitch_type"]][r["pitch_type"]] += 1
    return {"how_to_read": "usage and whiff_rate are 0-1; kmh is speed; hb_cm/ivb_cm are horizontal and vertical break. "
                           "next_pitch_after: within the same at-bat, the share of each pitch he threw next. "
                           "mix_by_situation: his pitch mix against left- and right-handed batters and with two strikes.",
            "mix_by_situation": situational_mix(data),
            "pitcher": pitcher, "throws": data[0]["pitcher_throws"], "pitches": len(data),
            "arsenal": (lines := [{**_pitch_line(k, v, len(data)), "role": "primary (most used)" if i == 0 else "secondary"}
                                  for i, (k, v) in enumerate(sorted(by_type.items(), key=lambda kv: -len(kv[1])))]),
            "secondary_with_most_whiffs": max((a for a in lines[1:] if a["whiff_rate"] is not None),
                                              key=lambda a: a["whiff_rate"], default={}).get("pitch"),
            "next_pitch_after": {k: {n: _ratio(c, sum(v.values())) for n, c in sorted(v.items(), key=lambda kv: -kv[1])}
                                 for k, v in after.items()}}


def _mix(data: List[Row]) -> Dict[str, Any]:
    counts: Dict[str, int] = defaultdict(int)
    for r in data:
        counts[r["pitch_type"]] += 1
    return {"pitches": len(data), **{k: _ratio(v, len(data)) for k, v in sorted(counts.items(), key=lambda kv: -kv[1])}}


def situational_mix(data: List[Row]) -> Dict[str, Any]:
    """Pitch mix against left- and right-handed batters, and with two strikes."""
    return {"vs_left_handed_batters": _mix([r for r in data if r["batter_side"] == "L"]),
            "vs_right_handed_batters": _mix([r for r in data if r["batter_side"] == "R"]),
            "with_two_strikes": _mix([r for r in data if r["strikes"] == 2]),
            "first_pitch_of_at_bat": _mix([r for r in data if r["balls"] == 0 and r["strikes"] == 0])}


def _pitch_line(kind: str, data: List[Row], total: int) -> Dict[str, Any]:
    swings = [r for r in data if r["swing"]]
    def avg(key: str) -> Optional[float]:
        known = [r[key] for r in data if r[key] is not None]
        return round(sum(known) / len(known), 1) if known else None
    return {"pitch": kind, "usage": _ratio(len(data), total), "avg_kmh": avg("kmh"), "max_kmh": max(r["kmh"] for r in data),
            "hb_cm": avg("hb_cm"), "ivb_cm": avg("ivb_cm"),
            "whiff_rate": _ratio(sum(r["result"] == "whiff" for r in swings), len(swings))}


def low_pitch_calls(db: sqlite3.Connection) -> Dict[str, Any]:
    """Of taken pitches in the bottom tenth of the zone at the middle of the plate: share called balls, by type."""
    taken = [r for r in rows(db) if not r["swing"] and 0 <= height_in_zone(r) <= 0.1 and abs(r["x_cm"]) <= HALF]
    by_type: Dict[str, List[Row]] = defaultdict(list)
    for r in taken:
        by_type[r["pitch_type"]].append(r)
    return {k: {"taken": len(v), "called_ball_rate": _ratio(sum(not r["abs_strike"] for r in v), len(v))}
            for k, v in sorted(by_type.items())}
