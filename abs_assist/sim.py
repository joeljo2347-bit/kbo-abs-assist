"""A pitch-by-pitch simulated season, producing the kind of log an ABS feed provides.

Each pitch: the pitcher picks a pitch type and a target (in the zone, on an edge, or a chase pitch,
depending on the count), misses by his command, and the pitch drops across the plate by its own
angle. The batter swings or takes; a taken pitch is called by the ABS rules in `zone.py`.
The model is simple on purpose: it exists to produce realistic-looking data, not to predict games.
"""

from __future__ import annotations

import math
from typing import Dict, Iterator, List, Tuple

import numpy as np

from abs_assist.players import PITCH_TYPES, Batter, Pitcher, league
from abs_assist.zone import ZONE_WIDTH_CM, Pitch, ZoneRules, call

HALF_PLATE_DEPTH_CM = 21.59  # from the middle of the plate to its back edge
WHIFF = {"fastball": 0.17, "sinker": 0.13, "slider": 0.32, "changeup": 0.29, "splitter": 0.35, "curveball": 0.30}
RULES = ZoneRules(2025)


def _intent(balls: int, strikes: int, rng: np.random.Generator) -> str:
    """Where the pitcher aims: more chase pitches when ahead, more strikes when behind."""
    lead = strikes - balls
    p_chase = float(np.clip(0.18 + 0.10 * lead, 0.03, 0.45))
    p_edge = 0.42
    roll = rng.random()
    return "chase" if roll < p_chase else "edge" if roll < p_chase + p_edge else "zone"


def _target(intent: str, kind: str, height: float, rng: np.random.Generator) -> Tuple[float, float]:
    """Aim point (x, height at the middle of the plate). Breaking and sinking pitches aim lower."""
    bottom, top = RULES.bounds(height)
    span, side = top - bottom, float(rng.choice([-1.0, 1.0]))
    low = kind in ("curveball", "splitter", "changeup", "sinker")
    if intent == "zone":
        lo, hi = (0.15, 0.55) if low else (0.25, 0.80)
        return side * rng.uniform(0, 14), bottom + span * rng.uniform(lo, hi)
    if intent == "edge":
        if rng.random() < 0.6:  # top or bottom edge
            return side * rng.uniform(0, 18), (bottom + 2) if (low or rng.random() < 0.5) else (top - 2)
        return side * rng.uniform(20, 23), bottom + span * rng.uniform(0.3, 0.7)
    if rng.random() < (0.75 if low else 0.45):  # chase below the zone, else off the side
        return side * rng.uniform(0, 20), bottom - rng.uniform(4, 16)
    return side * rng.uniform(27, 38), bottom + span * rng.uniform(0.2, 0.9)


def choose_type(pitcher: Pitcher, balls: int, strikes: int, prev: str, rng: np.random.Generator) -> str:
    """His mix, shifted by the count (fastballs when behind, more breaking balls when ahead)
    and by his habit of what follows the previous pitch."""
    lead = strikes - balls
    weights = []
    for kind, share in pitcher.arsenal.items():
        w = share * (1 + 0.6 * max(-lead, 0) if kind == "fastball" else 1 + pitcher.ahead_breaking * max(lead, 0))
        weights.append(w * pitcher.follow.get(prev, {}).get(kind, 1.0))
    p = np.array(weights) / sum(weights)
    return str(rng.choice(list(pitcher.arsenal), p=p))


def throw(pitcher: Pitcher, batter: Batter, balls: int, strikes: int, rng: np.random.Generator,
          prev: str = "", pitch_no: int = 1) -> Dict:
    """One pitch's flight: type, speed, and where it crosses the middle and back of the plate.
    Past his stamina, a pitcher loses speed and command."""
    kind = choose_type(pitcher, balls, strikes, prev, rng)
    tired = max(0.0, (pitch_no - pitcher.stamina) / 30)
    tx, tz = _target(_intent(balls, strikes, rng), kind, batter.height_cm, rng)
    spread = pitcher.command_cm + 4 * tired
    x, z_mid = tx + rng.normal(0, spread), tz + rng.normal(0, spread)
    vaa = PITCH_TYPES[kind]["vaa"] + rng.normal(0, 0.6)
    z_end = z_mid - HALF_PLATE_DEPTH_CM * math.tan(math.radians(vaa))
    kmh = PITCH_TYPES[kind]["kmh"] - 2.2 * tired + rng.normal(0, 2.0)
    intent = "tired" if tired else ""
    return {"pitch_type": kind, "intent": intent, "kmh": round(kmh, 1), "vaa_deg": round(vaa, 2),
            "x_cm": round(x, 2), "z_mid_cm": round(z_mid, 2), "z_end_cm": round(z_end, 2), "pitch_no": pitch_no}


def _swing_prob(in_zone: bool, batter: Batter, balls: int, strikes: int) -> float:
    base = 0.62 if in_zone else 0.30 * (1.25 - batter.discipline)
    if strikes == 2:
        base += 0.15
    if balls == 3 and strikes < 2:
        base -= 0.25
    return float(np.clip(base, 0.02, 0.97))


def centrality(x_cm: float, z_mid_cm: float, height_cm: float) -> float:
    """1 at the heart of the zone, 0 at its edge, below 0 outside it."""
    bottom, top = RULES.bounds(height_cm)
    across = abs(x_cm) / (ZONE_WIDTH_CM / 2)
    up = abs(z_mid_cm - (bottom + top) / 2) / ((top - bottom) / 2)
    return float(np.clip(1 - max(across, up), -1.0, 1.0))


BREAKING = ("slider", "curveball", "splitter", "changeup")


def _whiff_factor(kind: str, centre: float, height: float) -> float:
    """Where a pitch ends up changes how often it's missed: breaking and off-speed pitches get their
    whiffs below the zone and almost none when they hang; fastballs get more up in the zone."""
    if kind in BREAKING:
        if height < 0:
            return 1.9                                   # dives below the zone
        if height > 0.6 and centre >= 0:
            return 0.45                                  # hangs
        return 0.9 if centre >= 0 else 1.3
    if height > 0.67:
        return 1.35                                      # riding fastball up
    return 0.85 if centre >= 0 else 1.2


def _contact_result(kind: str, centre: float, height: float, batter: Batter, rng: np.random.Generator) -> str:
    """whiff, foul, out, single, double or home_run. The heart of the plate and hanging breaking
    balls are hit hardest; chase pitches are hit weakly."""
    if rng.random() < min(WHIFF[kind] * (1.45 - batter.contact) * _whiff_factor(kind, centre, height), 0.8):
        return "whiff"
    if rng.random() < 0.55:
        return "foul"
    hangs = kind in BREAKING and height > 0.6 and centre >= 0
    damage = max(centre, 0) + (0.5 if hangs else 0)
    p_hit = float(np.clip(0.27 + 0.12 * centre + 0.06 * hangs, 0.12, 0.42)) * (0.8 + 0.4 * batter.contact)
    if rng.random() >= p_hit:
        return "out"
    hr = (0.04 + 0.12 * batter.power) * (1 + 1.2 * damage)
    roll = rng.random()
    return "home_run" if roll < hr else "double" if roll < hr + 0.16 + 0.15 * batter.power else "single"


def pitch_outcome(p: Dict, batter: Batter, balls: int, strikes: int, rng: np.random.Generator) -> Dict:
    """Swing or take; a take is called by the ABS rules."""
    abs_call = call(Pitch(p["x_cm"], p["z_mid_cm"], p["z_end_cm"], batter.height_cm), RULES)
    swing = rng.random() < _swing_prob(abs_call.strike, batter, balls, strikes)
    if not swing:
        result = "called_strike" if abs_call.strike else "ball"
    else:
        bottom, top = RULES.bounds(batter.height_cm)
        height = (p["z_mid_cm"] - bottom) / (top - bottom)
        result = _contact_result(p["pitch_type"], centrality(p["x_cm"], p["z_mid_cm"], batter.height_cm), height, batter, rng)
    return {**p, "swing": swing, "result": result, "abs_strike": abs_call.strike,
            "abs_margin_cm": abs_call.margin_cm, "abs_rule": abs_call.deciding_rule}


PA_END = {"out", "single", "double", "home_run"}


def _advance(result: str, balls: int, strikes: int) -> Tuple[int, int, str]:
    """The count after a pitch, and the plate appearance's result if it ended."""
    if result in PA_END:
        return balls, strikes, result
    if result == "ball":
        return (balls + 1, strikes, "walk") if balls == 3 else (balls + 1, strikes, "")
    if result == "foul" and strikes == 2:
        return balls, strikes, ""
    return (balls, strikes + 1, "strikeout") if strikes == 2 else (balls, strikes + 1, "")


def plate_appearance(pitcher: Pitcher, batter: Batter, rng: np.random.Generator,
                     state: Dict[str, Dict]) -> Tuple[List[Dict], str]:
    """`state` carries each pitcher's pitch count and previous pitch through the game."""
    rows, balls, strikes, ended = [], 0, 0, ""
    while not ended:
        n = state["count"][pitcher.name] = state["count"].get(pitcher.name, 0) + 1
        thrown = throw(pitcher, batter, balls, strikes, rng, state["prev"].get(pitcher.name, ""), n)
        state["prev"][pitcher.name] = thrown["pitch_type"]
        p = pitch_outcome(thrown, batter, balls, strikes, rng)
        rows.append({**p, "balls": balls, "strikes": strikes})
        balls, strikes, ended = _advance(p["result"], balls, strikes)
    rows[-1]["pa_result"] = ended
    return rows, ended


def half_inning(pitchers: List[Pitcher], inning: int, lineup: List[Batter], start: int,
                rng: np.random.Generator, state: Dict[str, Dict]) -> Tuple[List[Dict], int]:
    """Three outs. The starter pitches up to six innings, and comes out once he's 15 pitches past
    his stamina; then the reliever."""
    rows, outs, idx = [], 0, start
    while outs < 3:
        starter = pitchers[0]
        tired_out = state["count"].get(starter.name, 0) > starter.stamina + 15
        pitcher = starter if inning <= 6 and not tired_out else pitchers[1]
        batter = lineup[idx % len(lineup)]
        pa, result = plate_appearance(pitcher, batter, rng, state)
        rows += [{**r, "pitcher": pitcher.name, "pitcher_team": pitcher.team, "batter": batter.name,
                  "batter_team": batter.team, "batter_height_cm": round(batter.height_cm, 1)} for r in pa]
        outs += result in ("out", "strikeout")
        idx += 1
    return rows, idx


def game(game_id: int, home: str, away: str, rosters: Dict[str, tuple], rng: np.random.Generator) -> List[Dict]:
    starters = {t: [rosters[t][0][game_id % 5], rosters[t][0][5]] for t in (home, away)}
    rows: List[Dict] = []
    next_up = {home: 0, away: 0}
    state: Dict[str, Dict] = {"count": {}, "prev": {}}
    for inning in range(1, 10):
        for batting, fielding, half in ((away, home, "top"), (home, away, "bottom")):
            pitched, next_up[batting] = half_inning(starters[fielding], inning, rosters[batting][1], next_up[batting], rng, state)
            rows += [{**r, "game_id": game_id, "inning": inning, "half": half} for r in pitched]
    return rows


def season(games: int = 360, seed: int = 2025) -> Iterator[Dict]:
    """A round-robin schedule; every pitch, in order, with its game, inning and count."""
    rosters, rng = league(seed), np.random.default_rng(seed + 1)
    teams = list(rosters)
    for g in range(games):
        home, away = teams[g % len(teams)], teams[(g * 3 + 1 + g // len(teams)) % len(teams)]
        if home == away:
            away = teams[(teams.index(home) + 1) % len(teams)]
        yield from game(g, home, away, rosters, rng)
