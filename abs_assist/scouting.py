"""Scouting a pitcher against a team: his arsenal, filtered by batter side, speed and movement.

Arsenal rows are sorted from most to least used. Rates:
- usage: share of his pitches (after filters) that were this type
- zone: share inside the ABS zone; chase: share of pitches outside it that batters swung at
- whiff: share of swings that missed; called_strike: share of taken pitches called strikes
- avg_against: hits / at-bats that ended on this pitch type
Movement is in cm: horizontal break (+ = his arm side) and induced vertical break.
"""

from __future__ import annotations

import random
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from abs_assist.analyze import Row, height_in_zone, rows

HITS = {"single", "double", "home_run"}


@dataclass
class Filters:
    side: Optional[str] = None                       # "R" or "L": the batter's side
    kmh: Tuple[float, float] = (0.0, 200.0)
    hb: Tuple[float, float] = (-100.0, 100.0)
    ivb: Tuple[float, float] = (-100.0, 100.0)

    def keep(self, r: Row) -> bool:
        return ((self.side is None or r["batter_side"] == self.side)
                and self.kmh[0] <= r["kmh"] <= self.kmh[1]
                and self.hb[0] <= r["hb_cm"] <= self.hb[1] and self.ivb[0] <= r["ivb_cm"] <= self.ivb[1])


def _rate(part: float, whole: float) -> Optional[float]:
    return round(part / whole, 3) if whole else None


def _avg(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 1) if values else None


def summarize(kind: str, data: List[Row], total: int) -> Dict[str, Any]:
    """One arsenal row."""
    zone = [r for r in data if r["abs_strike"]]
    outside = [r for r in data if not r["abs_strike"]]
    swings = [r for r in data if r["swing"]]
    takes = [r for r in data if not r["swing"]]
    at_bats = [r for r in data if r["pa_result"] and r["pa_result"] != "walk"]
    return {"pitch": kind, "pitches": len(data), "usage": _rate(len(data), total),
            "avg_kmh": _avg([r["kmh"] for r in data]), "max_kmh": max(r["kmh"] for r in data),
            "hb_cm": _avg([r["hb_cm"] for r in data]), "ivb_cm": _avg([r["ivb_cm"] for r in data]),
            "zone": _rate(len(zone), len(data)), "chase": _rate(sum(r["swing"] for r in outside), len(outside)),
            "whiff": _rate(sum(r["result"] == "whiff" for r in swings), len(swings)),
            "called_strike": _rate(sum(r["abs_strike"] for r in takes), len(takes)),
            "avg_against": _rate(sum(r["pa_result"] in HITS for r in at_bats), len(at_bats))}


def _point(r: Row) -> Dict[str, Any]:
    return {"type": r["pitch_type"], "hb": r["hb_cm"], "ivb": r["ivb_cm"], "kmh": r["kmh"], "x": r["x_cm"],
            "h": round(height_in_zone(r), 3), "strike": bool(r["abs_strike"]), "result": r["result"]}


def scout(db: sqlite3.Connection, pitcher: str, opponent: Optional[str], filters: Filters, limit: int = 1200) -> Dict[str, Any]:
    data = [r for r in rows(db, pitcher=pitcher, batter_team=opponent) if filters.keep(r)]
    if not data:
        return {"pitcher": pitcher, "opponent": opponent, "pitches": 0, "arsenal": [], "points": []}
    by_type: Dict[str, List[Row]] = defaultdict(list)
    for r in data:
        by_type[r["pitch_type"]].append(r)
    arsenal = sorted((summarize(k, v, len(data)) for k, v in by_type.items()), key=lambda a: -a["pitches"])
    sample = random.Random(7).sample(data, min(limit, len(data)))
    return {"pitcher": pitcher, "throws": data[0]["pitcher_throws"], "team": data[0]["pitcher_team"],
            "opponent": opponent, "pitches": len(data), "arsenal": arsenal, "points": [_point(r) for r in sample]}
