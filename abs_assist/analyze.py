"""What the ABS data says: called strikes by location, the two-plane effect, and player profiles.

Locations are measured against each batter's own zone: height 0 is the bottom edge, 1 the top,
so a 170 cm and a 195 cm batter can be compared. Left-right is in cm from the middle of the plate.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from abs_assist.zone import ZONE_WIDTH_CM, ZoneRules

RULES = ZoneRules(2025)
HALF = ZONE_WIDTH_CM / 2
# Standard approximate run values of plate-appearance results (wOBA-style weights).
RESULT_VALUE = {"walk": 0.69, "single": 0.88, "double": 1.25, "home_run": 2.0, "out": 0.0, "strikeout": 0.0}
Row = Dict[str, Any]


def rows(db: sqlite3.Connection, **where: Any) -> List[Row]:
    """Pitches, optionally filtered by pitcher, batter, team columns, pitch type or game."""
    allowed = {"pitcher", "batter", "pitcher_team", "batter_team", "pitch_type", "game_id"}
    filters = {k: v for k, v in where.items() if v is not None and k in allowed}
    sql = "SELECT * FROM pitches" + (" WHERE " + " AND ".join(f"{k} = ?" for k in filters) if filters else "")
    cur = db.execute(sql, list(filters.values()))
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def height_in_zone(r: Row, plane: str = "z_mid_cm") -> float:
    bottom, top = RULES.bounds(r["batter_height_cm"])
    return (r[plane] - bottom) / (top - bottom)


def cell(r: Row) -> Tuple[str, str]:
    """A named location: (height band, side band)."""
    h, x = height_in_zone(r), abs(r["x_cm"])
    height = ("below" if h < 0 else "low" if h < 0.33 else "middle" if h < 0.67 else "high" if h <= 1 else "above")
    side = "heart" if x < 8 else "inner" if x < 16 else "edge" if x <= HALF else "off"
    return height, side


def called_strike_map(data: List[Row]) -> Dict[str, Dict[str, Any]]:
    """For taken pitches: how often each location was called a strike."""
    counts: Dict[Tuple[str, str], List[int]] = defaultdict(lambda: [0, 0])
    for r in data:
        if not r["swing"]:
            c = counts[cell(r)]
            c[0] += r["abs_strike"]
            c[1] += 1
    return {f"{h}/{s}": {"taken": n, "called_strike_rate": round(k / n, 3)} for (h, s), (k, n) in sorted(counts.items())}


def lost_at_back(data: List[Row]) -> Dict[str, Any]:
    """Taken pitches inside the zone at the middle of the plate but called balls at the back edge."""
    taken = [r for r in data if not r["swing"]]
    lost = [r for r in taken if not r["abs_strike"] and r["abs_rule"] == "bottom, back of plate"
            and 0 <= height_in_zone(r) <= 1 and abs(r["x_cm"]) <= HALF]
    by_type: Dict[str, int] = defaultdict(int)
    for r in lost:
        by_type[r["pitch_type"]] += 1
    return {"how_to_read": "Totals over every game in the data (games); not per game or per season.",
            "games": len({r["game_id"] for r in data}), "taken_pitches": len(taken), "strikes_lost": len(lost),
            "share_of_takes": round(len(lost) / max(len(taken), 1), 4),
            "by_pitch_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1]))}


PITCHER_FIELDS = ("Rates are 0-1. in_zone_rate: share of his pitches inside the ABS zone. chase_rate: share of his "
                  "pitches outside the zone that batters swung at. whiff_rate: share of swings against him that missed "
                  "(this is 'missing bats'). pitch_mix: share of his pitches by type. strikes_lost_at_back: taken pitches "
                  "inside the zone at the middle of the plate but called balls at the back edge.")
BATTER_FIELDS = ("Rates are 0-1. The swing, chase and whiff rates are per pitch; batting_average is per at-bat; "
                 "strikeout_rate and walk_rate (in compared_with_league) are per plate appearance. zone_cm: the bottom and top of his ABS zone "
                 "in cm above the ground (the zone's width is always 47.18 cm). zone_swing_rate: share of in-zone pitches "
                 "he swung at. chase_rate: share of pitches outside the zone he swung at. whiff_rate: share of his swings "
                 "that missed. value_per_pa: his average run value per plate appearance.")


def _rate(part: int, whole: int) -> Optional[float]:
    return round(part / whole, 3) if whole else None


def pitcher_profile(db: sqlite3.Connection, pitcher: str) -> Dict[str, Any]:
    data = rows(db, pitcher=pitcher)
    if not data:
        return {"error": f"No pitches for {pitcher}."}
    zone = [r for r in data if r["abs_strike"]]
    swings = [r for r in data if r["swing"]]
    mix: Dict[str, int] = defaultdict(int)
    for r in data:
        mix[r["pitch_type"]] += 1
    return {"how_to_read": PITCHER_FIELDS, "pitcher": pitcher, "team": data[0]["pitcher_team"], "pitches": len(data),
            "in_zone_rate": _rate(len(zone), len(data)),
            "chase_rate": _rate(sum(1 for r in swings if not r["abs_strike"]), len(data) - len(zone)),
            "whiff_rate": _rate(sum(1 for r in swings if r["result"] == "whiff"), len(swings)),
            "pitch_mix": {k: _rate(v, len(data)) for k, v in sorted(mix.items(), key=lambda kv: -kv[1])},
            "strikes_lost_at_back": lost_at_back(data)["strikes_lost"]}


def batter_profile(db: sqlite3.Connection, batter: str) -> Dict[str, Any]:
    data = rows(db, batter=batter)
    if not data:
        return {"error": f"No pitches for {batter}."}
    bottom, top = RULES.bounds(data[0]["batter_height_cm"])
    zone = [r for r in data if r["abs_strike"]]
    out = [r for r in data if not r["abs_strike"]]
    pas = [r["pa_result"] for r in data if r["pa_result"]]
    return {"how_to_read": BATTER_FIELDS, "batter": batter, "team": data[0]["batter_team"],
            "height_cm": data[0]["batter_height_cm"],
            "zone_cm": [round(bottom, 1), round(top, 1)], "zone_bottom_cm": round(bottom, 1), "zone_top_cm": round(top, 1),
            "zone_height_cm": round(top - bottom, 1), "pitches_seen": len(data), "plate_appearances": len(pas),
            "zone_swing_rate": _rate(sum(r["swing"] for r in zone), len(zone)),
            "chase_rate": _rate(sum(r["swing"] for r in out), len(out)),
            "whiff_rate": _rate(sum(r["result"] == "whiff" for r in data), sum(r["swing"] for r in data)),
            "value_per_pa": round(sum(RESULT_VALUE[p] for p in pas) / max(len(pas), 1), 3)}
