"""Strategy assist: which pitch and location gives the batter the least, and which pitches a
batter should take.

Everything is measured from the collected data:
- what each count is worth to the batter (the average result of plate appearances that passed
  through it, in standard run-value weights);
- for each pitch type and location: how often batters swing, whiff, foul it off, and what they do
  when they put it in play; and how often ABS calls it a strike when taken. That last rate already
  includes the two-plane rule: low breaking balls lose strikes at the back of the plate.
A batter's own swing and whiff tendencies scale the league rates.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from abs_assist.analyze import RESULT_VALUE, cell, rows

Count = Tuple[int, int]
Key = Tuple[str, Tuple[str, str], bool]  # (pitch type, location cell, two strikes)
PRIOR = 20.0                       # league pitches a thin estimate leans on


@dataclass
class Rates:
    n: float = 0
    swings: float = 0
    whiffs: float = 0
    fouls: float = 0
    takes: float = 0
    called_strikes: float = 0
    in_play: float = 0
    in_play_value: float = 0.0


def count_values(data: List[Dict]) -> Dict[Count, float]:
    """Average plate-appearance value for the batter from each count."""
    totals: Dict[Count, List[float]] = defaultdict(lambda: [0.0, 0])
    visited: List[Count] = []
    for r in sorted(data, key=lambda r: r["id"]):
        visited.append((r["balls"], r["strikes"]))
        if r["pa_result"]:
            for c in set(visited):
                totals[c][0] += RESULT_VALUE[r["pa_result"]]
                totals[c][1] += 1
            visited = []
    values = {c: v / n for c, (v, n) in totals.items()}
    values.update({(4, s): RESULT_VALUE["walk"] for s in range(3)})
    values.update({(b, 3): RESULT_VALUE["strikeout"] for b in range(4)})
    return values


def _add(rate: Rates, r: Dict) -> None:
    rate.n += 1
    if not r["swing"]:
        rate.takes += 1
        rate.called_strikes += r["abs_strike"]
        return
    rate.swings += 1
    rate.whiffs += r["result"] == "whiff"
    rate.fouls += r["result"] == "foul"
    if r["result"] in RESULT_VALUE:
        rate.in_play += 1
        rate.in_play_value += RESULT_VALUE[r["result"]]


def pitch_rates(data: List[Dict]) -> Dict[Key, Rates]:
    table: Dict[Key, Rates] = defaultdict(Rates)
    for r in data:
        _add(table[(r["pitch_type"], cell(r), r["strikes"] == 2)], r)
    return table


def batter_tilt(db: sqlite3.Connection, batter: str, league: Dict[Key, Rates]) -> Tuple[float, float]:
    """How much more (or less) this batter swings and whiffs than the league, shrunk toward 1."""
    mine = pitch_rates(rows(db, batter=batter))
    swing_exp = sum(league[k].swings / max(league[k].n, 1) * v.n for k, v in mine.items() if k in league)
    whiff_exp = sum(league[k].whiffs / max(league[k].swings, 1) * v.swings for k, v in mine.items() if k in league)
    swings = sum(v.swings for v in mine.values())
    whiffs = sum(v.whiffs for v in mine.values())
    return (swings + PRIOR) / (swing_exp + PRIOR), (whiffs + PRIOR) / (whiff_exp + PRIOR)


MIN_PITCHES = 30
# Distinct names, so a shortened one ("high" for "belt-high") can't be read as a different height.
PLACES = {"below": "below the zone", "low": "knee-high", "middle": "belt-high", "high": "letter-high", "above": "above the zone"}
SIDES = {"heart": "over the middle", "inner": "toward a corner", "edge": "on the edge", "off": "off the plate"}


def _next(values: Dict[Count, float], b: int, s: int) -> Tuple[float, float, float]:
    """Batter's value after a strike, a ball, and a foul from count (b, s)."""
    return values[(b, s + 1)], values[(b + 1, s)], values[(b, s + 1)] if s < 2 else values[(b, s)]


def evaluate(rate: Rates, b: int, s: int, values: Dict[Count, float], tilt: Tuple[float, float]) -> Dict[str, float]:
    """Expected value for the batter if he swings, if he takes, and overall."""
    strike, ball, foul = _next(values, b, s)
    p_cs = rate.called_strikes / rate.takes if rate.takes else 0.0
    take = p_cs * strike + (1 - p_cs) * ball
    whiff = min(rate.whiffs / max(rate.swings, 1) * tilt[1], 0.95)
    fouls = min(rate.fouls / max(rate.swings, 1), 1 - whiff)  # chances of one swing add up to at most 1
    in_play = max(1 - whiff - fouls, 0.0)
    swing = whiff * strike + fouls * foul + in_play * (rate.in_play_value / max(rate.in_play, 1))
    p_swing = min(rate.swings / max(rate.n, 1) * tilt[0], 0.97)
    return {"overall": p_swing * swing + (1 - p_swing) * take, "swing": swing, "take": take,
            "p_swing": p_swing, "p_whiff": whiff, "p_called_strike": p_cs}


def describe(key: Key) -> str:
    kind, (height, side), _ = key
    return f"{kind}, {PLACES[height]}, {SIDES[side]}"


READ_ME = ("batter_value_after_runs: the batter's expected runs after this pitch, in runs, not a percentage (lower is "
           "better for the pitcher); batter_value_now_runs is the same for the count before the pitch. The fields "
           "ending in _chance are probabilities from 0 to 1.")


def _readable(key: Key, ev: Dict[str, float]) -> Dict[str, Any]:
    return {"pitch": describe(key), "batter_value_after_runs": round(ev["overall"], 3),
            "swing_chance": round(ev["p_swing"], 3), "whiff_chance_if_swung_at": round(ev["p_whiff"], 3),
            "called_strike_chance_if_taken": round(ev["p_called_strike"], 3)}


class Strategy:
    """League tables are built once from the collected data; each question reuses them."""

    def __init__(self, db: sqlite3.Connection):
        data = rows(db)
        self.db, self.values, self.league = db, count_values(data), pitch_rates(data)

    def _arsenal(self, pitcher: str) -> List[str]:
        return [k for (k,) in self.db.execute("SELECT DISTINCT pitch_type FROM pitches WHERE pitcher = ?", (pitcher,))]

    def options(self, pitcher: str, batter: str, b: int, s: int) -> List[Tuple[Key, Dict[str, float]]]:
        tilt = batter_tilt(self.db, batter, self.league)
        arsenal = set(self._arsenal(pitcher))
        return [(k, evaluate(r, b, s, self.values, tilt)) for k, r in self.league.items()
                if k[0] in arsenal and k[2] == (s == 2) and r.n >= MIN_PITCHES]

    def attack_plan(self, batter: str, b: int, s: int, top: int = 3) -> Dict:
        """How to pitch this batter in this count when no pitcher is named: every pitch type and
        location in the league, scaled by his own tendencies."""
        tilt = batter_tilt(self.db, batter, self.league)
        rated = [(k, evaluate(r, b, s, self.values, tilt)) for k, r in self.league.items()
                 if k[2] == (s == 2) and r.n >= MIN_PITCHES]
        return self._ranked(rated, b, s, top)

    def recommend(self, pitcher: str, batter: str, b: int, s: int, top: int = 3) -> Dict:
        """The pitches that give this batter the least from this count."""
        return self._ranked(self.options(pitcher, batter, b, s), b, s, top)

    def _ranked(self, rated: List[Tuple[Key, Dict[str, float]]], b: int, s: int, top: int) -> Dict:
        ranked = sorted(rated, key=lambda kv: kv[1]["overall"])
        worst = ranked[max(top, len(ranked) - 2):]  # never list a pitch as both best and worst
        return {"how_to_read": READ_ME, "count": f"{b}-{s}",
                "batter_value_now_runs": round(self.values[(b, s)], 3),
                "best": [_readable(k, ev) for k, ev in ranked[:top]],
                "worst": [_readable(k, ev) for k, ev in worst]}

    def take_guide(self, batter: str, b: int, s: int, top: int = 4) -> Dict:
        """Pitches this batter does better to take than to swing at, from this count, any pitcher."""
        tilt = batter_tilt(self.db, batter, self.league)
        rated = [(k, evaluate(r, b, s, self.values, tilt)) for k, r in self.league.items()
                 if k[2] == (s == 2) and r.n >= MIN_PITCHES and r.takes >= 10]
        gains = sorted(((ev["take"] - ev["swing"], k, ev) for k, ev in rated), key=lambda t: -t[0])
        return {"how_to_read": "gain_from_taking_runs: how many more runs (not a percentage) the batter is expected to get "
                               "by taking than by swinging. p_called_strike: probability 0-1 that it's called a strike.",
                "count": f"{b}-{s}", "take": [{"pitch": describe(k), "gain_from_taking_runs": round(g, 3),
                                               "p_called_strike": round(ev["p_called_strike"], 3)}
                                              for g, k, ev in gains[:top] if g > 0]}
