"""Live pitcher tracking: each pitch is compared with the pitcher's own baseline, with alerts.

Alerts:
- velocity: his last 8 fastballs are clearly slower than his first 10 fastballs of this game
  (by more than the normal pitch-to-pitch noise allows, and by at least 1 km/h)
- zone: his last 20 pitches are in the zone 15 points or more below his norm
- count: he reaches 90 and 100 pitches
Each alert fires once per pitcher per game.
"""

from __future__ import annotations

import sqlite3
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

FASTBALLS, EARLY, RECENT = 8, 10, 20
MIN_DROP_KMH, NOISE_Z, ZONE_DROP = 1.0, 2.5, 0.15


@dataclass
class Baseline:
    fastball_kmh: Optional[float]
    zone_rate: float


@dataclass
class GameState:
    pitches: int = 0
    fastballs: Deque[float] = field(default_factory=lambda: deque(maxlen=FASTBALLS))
    early_fastballs: List[float] = field(default_factory=list)
    fastballs_thrown: int = 0
    recent_zone: Deque[int] = field(default_factory=lambda: deque(maxlen=RECENT))
    whiffs: int = 0
    swings: int = 0
    fired: set = field(default_factory=set)


def baseline(db: sqlite3.Connection, pitcher: str, before_game: int) -> Optional[Baseline]:
    """His norms from games before this one."""
    fb, zone, n = db.execute(
        "SELECT AVG(CASE WHEN pitch_type='fastball' THEN kmh END), SUM(abs_strike), COUNT(*) "
        "FROM pitches WHERE pitcher = ? AND game_id < ?", (pitcher, before_game)).fetchone()
    return Baseline(fb, zone / n) if n else None


def _velocity_drop(state: GameState) -> Optional[float]:
    """How far his recent fastballs are below today's early ones, if that's more than noise."""
    early, recent = state.early_fastballs, list(state.fastballs)
    if state.fastballs_thrown < EARLY + FASTBALLS:  # the two windows must not overlap
        return None
    noise = statistics.stdev(early) * (1 / EARLY + 1 / FASTBALLS) ** 0.5
    drop = statistics.mean(early) - statistics.mean(recent)
    return drop if drop >= max(MIN_DROP_KMH, NOISE_Z * noise) else None


def _alerts(state: GameState, base: Baseline) -> List[str]:
    alerts = []
    drop = _velocity_drop(state)
    if drop is not None:
        alerts.append(f"velocity: last {FASTBALLS} fastballs {drop:.1f} km/h below his first {EARLY} today")
    if len(state.recent_zone) == RECENT:
        rate = sum(state.recent_zone) / RECENT
        if base.zone_rate - rate >= ZONE_DROP:
            alerts.append(f"zone: {rate:.0%} in the zone over the last {RECENT}, his norm is {base.zone_rate:.0%}")
    alerts += [f"count: {n} pitches" for n in (90, 100) if state.pitches == n]
    return alerts


def _key(alert: str) -> str:
    """Velocity and zone alerts fire once per game; each pitch-count milestone once."""
    return alert if alert.startswith("count") else alert.split(":")[0]


class LiveTracker:
    def __init__(self, baselines: Dict[str, Baseline]):
        self.baselines = baselines
        self.games: Dict[str, GameState] = {}

    def add(self, pitch: Dict) -> List[str]:
        """Record one pitch; return any new alerts for its pitcher."""
        name = pitch["pitcher"]
        state = self.games.setdefault(name, GameState())
        state.pitches += 1
        state.recent_zone.append(int(pitch["abs_strike"]))
        if pitch["pitch_type"] == "fastball":
            state.fastballs.append(pitch["kmh"])
            state.fastballs_thrown += 1
            if len(state.early_fastballs) < EARLY:
                state.early_fastballs.append(pitch["kmh"])
        state.swings += bool(pitch["swing"])
        state.whiffs += pitch["result"] == "whiff"
        base = self.baselines.get(name)
        new = [a for a in (_alerts(state, base) if base else []) if _key(a) not in state.fired]
        state.fired.update(_key(a) for a in new)
        return new

    def summary(self, pitcher: str) -> Dict:
        state, base = self.games.get(pitcher, GameState()), self.baselines.get(pitcher)
        recent_fb = sum(state.fastballs) / len(state.fastballs) if state.fastballs else None
        return {"pitcher": pitcher, "pitches": state.pitches,
                "fastball_kmh_recent": round(recent_fb, 1) if recent_fb else None,
                "fastball_kmh_norm": round(base.fastball_kmh, 1) if base and base.fastball_kmh else None,
                "zone_rate_recent": round(sum(state.recent_zone) / len(state.recent_zone), 3) if state.recent_zone else None,
                "zone_rate_norm": round(base.zone_rate, 3) if base else None,
                "whiff_rate_today": round(state.whiffs / state.swings, 3) if state.swings else None}
