"""Collect ABS pitch events: validate each one, call it by the rules, and store it.

An event is one pitch from a tracking feed. The collector checks every field, computes the ABS
call and its explanation itself (so the analysis never depends on a feed's own labels), flags an
event whose reported call disagrees with the rules, and refuses duplicates.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from abs_assist.zone import Pitch, ZoneRules, call

COLUMNS = {
    "game_id": int, "inning": int, "half": str, "pitcher": str, "pitcher_team": str, "batter": str,
    "batter_team": str, "batter_height_cm": float, "balls": int, "strikes": int, "pitch_type": str,
    "kmh": float, "x_cm": float, "z_mid_cm": float, "z_end_cm": float, "swing": bool, "result": str,
}
OPTIONAL = {"pa_result": str, "reported_strike": bool, "vaa_deg": float, "intent": str,
            "pitcher_throws": str, "batter_side": str, "hb_cm": float, "ivb_cm": float}
RANGES = {"batter_height_cm": (150, 215), "balls": (0, 3), "strikes": (0, 2), "kmh": (60, 170),
          "x_cm": (-150, 150), "z_mid_cm": (-50, 250), "z_end_cm": (-50, 250), "inning": (1, 15)}
OPTIONAL_RANGES = {"hb_cm": (-90, 90), "ivb_cm": (-90, 90)}
HANDS = {"pitcher_throws": ("R", "L"), "batter_side": ("R", "L")}
DERIVED = {"abs_strike": int, "abs_margin_cm": float, "abs_rule": str, "feed_disagrees": int}


@dataclass
class Report:
    accepted: int = 0
    duplicates: int = 0
    disagreements: int = 0
    rejected: List[Tuple[int, str]] = field(default_factory=list)  # (event index, reason)


RESULTS = {"ball", "called_strike", "whiff", "foul", "out", "single", "double", "home_run"}
PA_RESULTS = {"walk", "strikeout", "out", "single", "double", "home_run"}
CHOICES = {"half": {"top", "bottom"}, "result": RESULTS}


def _type_problem(name: str, kind: type, value: Any) -> Optional[str]:
    if kind in (int, float):
        return None if isinstance(value, (int, float)) and not isinstance(value, bool) else f"{name} must be a number"
    if kind is bool:
        return None if isinstance(value, bool) else f"{name} must be true or false"
    return None if isinstance(value, str) else f"{name} must be text"


def validate(event: Dict[str, Any]) -> Optional[str]:
    """Why this event can't be stored, or None. Every field is type-checked before any comparison,
    so a malformed event is refused rather than crashing the whole batch."""
    for name, kind in COLUMNS.items():
        if name not in event:
            return f"missing {name}"
        problem = _type_problem(name, kind, event[name])
        if problem:
            return problem
    for name, (lo, hi) in RANGES.items():
        if not lo <= event[name] <= hi:
            return f"{name}={event[name]} is outside {lo}..{hi}"
    for name, allowed in CHOICES.items():
        if event[name] not in allowed:
            return f"{name}={event[name]!r} is not one of {sorted(allowed)}"
    return _optional_problem(event)


def _optional_problem(event: Dict[str, Any]) -> Optional[str]:
    """Optional fields may be missing, but when present they must make sense."""
    for name, kind in OPTIONAL.items():
        if event.get(name) is not None:
            problem = _type_problem(name, kind, event[name])
            if problem:
                return problem
    for name, (lo, hi) in OPTIONAL_RANGES.items():
        if event.get(name) is not None and not lo <= event[name] <= hi:
            return f"{name}={event[name]} is outside {lo}..{hi}"
    for name, allowed in HANDS.items():
        if event.get(name) is not None and event[name] not in allowed:
            return f"{name} must be R or L"
    if event.get("pa_result") is not None and event["pa_result"] not in PA_RESULTS:
        return f"pa_result={event['pa_result']!r} is not one of {sorted(PA_RESULTS)}"
    return None


def with_call(event: Dict[str, Any], rules: ZoneRules) -> Dict[str, Any]:
    c = call(Pitch(event["x_cm"], event["z_mid_cm"], event["z_end_cm"], event["batter_height_cm"]), rules)
    reported = event.get("reported_strike")
    return {**event, "abs_strike": int(c.strike), "abs_margin_cm": c.margin_cm, "abs_rule": c.deciding_rule,
            "feed_disagrees": int(reported is not None and bool(reported) != c.strike)}


def open_store(path: Union[str, Path] = ":memory:") -> sqlite3.Connection:
    db = sqlite3.connect(str(path), check_same_thread=False)
    names = {**COLUMNS, **OPTIONAL, **DERIVED}
    sql = {int: "INTEGER", float: "REAL", str: "TEXT", bool: "INTEGER"}
    cols = ", ".join(f"{n} {sql[t]}" for n, t in names.items())
    db.execute(f"CREATE TABLE IF NOT EXISTS pitches (id INTEGER PRIMARY KEY, {cols}, "
               "UNIQUE (game_id, inning, half, pitcher, batter, balls, strikes, x_cm, z_mid_cm))")
    return db


def ingest(db: sqlite3.Connection, events: Iterable[Dict[str, Any]], rules: ZoneRules = ZoneRules()) -> Report:
    report, names = Report(), [*COLUMNS, *OPTIONAL, *DERIVED]
    sql = f"INSERT OR IGNORE INTO pitches ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})"
    for i, event in enumerate(events):
        problem = validate(event)
        if problem:
            report.rejected.append((i, problem))
            continue
        row = with_call(event, rules)
        cur = db.execute(sql, [row.get(n) for n in names])
        report.accepted += cur.rowcount
        report.duplicates += 1 - cur.rowcount
        report.disagreements += row["feed_disagrees"] if cur.rowcount else 0
    db.commit()
    return report
