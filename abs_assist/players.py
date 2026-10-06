"""Simulated KBO rosters: real team names, fictional players.

Players are made up so that no real player's numbers are misrepresented. Their traits are drawn
from ranges that look like a professional league: heights around 181 cm, fastballs around
145 km/h, command and plate discipline that vary from player to player.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np

TEAMS = ["LG Twins", "KIA Tigers", "Samsung Lions", "Doosan Bears", "KT Wiz",
         "SSG Landers", "Lotte Giants", "Hanwha Eagles", "NC Dinos", "Kiwoom Heroes"]

_SURNAMES = ["Kim", "Lee", "Park", "Choi", "Jung", "Kang", "Cho", "Yoon", "Jang", "Lim", "Han", "Oh"]
_GIVEN = ["Min-jun", "Seo-jun", "Do-yun", "Ha-jun", "Ji-ho", "Jun-seo", "Hyun-woo", "Ji-hoon",
          "Woo-jin", "Seung-min", "Tae-yang", "Dong-hyun", "Jae-won", "Sung-ho", "Yeon-woo"]

# Pitch types: mean speed (km/h) and vertical approach angle (degrees below horizontal).
# Pitch types: mean speed (km/h), vertical approach angle (degrees below horizontal), and movement
# for a right-hander in cm: horizontal break (+ = toward his arm side) and induced vertical break
# (+ = less drop than gravity alone), typical of professional pitch shapes.
PITCH_TYPES: Dict[str, Dict[str, float]] = {
    "fastball": {"kmh": 145, "vaa": 4.8, "hb": 20, "ivb": 40},
    "sinker": {"kmh": 142, "vaa": 6.0, "hb": 38, "ivb": 20},
    "slider": {"kmh": 133, "vaa": 6.6, "hb": -13, "ivb": 5},
    "changeup": {"kmh": 130, "vaa": 7.2, "hb": 35, "ivb": 18},
    "splitter": {"kmh": 132, "vaa": 7.6, "hb": 20, "ivb": 6},
    "curveball": {"kmh": 118, "vaa": 9.4, "hb": -20, "ivb": -30},
}


@dataclass
class Pitcher:
    name: str
    team: str
    command_cm: float                    # spread of misses around the target
    arsenal: Dict[str, float] = field(default_factory=dict)  # pitch type -> share of pitches
    ahead_breaking: float = 0.5          # how much more he throws non-fastballs when ahead in the count
    follow: Dict[str, Dict[str, float]] = field(default_factory=dict)  # previous pitch -> preference multipliers
    stamina: int = 85                    # pitches before he starts to tire
    throws: str = "R"                    # R or L
    velo: float = 0.0                    # his speed above or below the league norm, km/h
    shape: Dict[str, Tuple[float, float]] = field(default_factory=dict)  # pitch -> (hb, ivb) offsets in cm


@dataclass
class Batter:
    name: str
    team: str
    height_cm: float
    discipline: float                   # 0..1, higher = fewer chases
    contact: float                      # 0..1, higher = fewer whiffs
    power: float                        # 0..1, higher = more extra-base hits
    bats: str = "R"                     # R, L or S (switch: bats opposite the pitcher)


def _name(rng: np.random.Generator, used: set) -> str:
    while True:
        name = f"{rng.choice(_SURNAMES)} {rng.choice(_GIVEN)}"
        if name not in used:
            used.add(name)
            return name


def _arsenal(rng: np.random.Generator) -> Dict[str, float]:
    """A fastball 40-58% of the time, plus two or three other pitches. Shares sum to exactly 1."""
    extras = rng.choice([p for p in PITCH_TYPES if p != "fastball"], size=int(rng.integers(2, 4)), replace=False)
    weights = np.concatenate([[rng.uniform(0.40, 0.58)], rng.dirichlet(np.ones(len(extras)))])
    weights[1:] *= 1 - weights[0]
    weights = np.round(weights, 3)
    weights[0] += 1 - weights.sum()
    return {p: float(w) for p, w in zip(["fastball", *extras], weights)}


def _follow(arsenal: Dict[str, float], rng: np.random.Generator) -> Dict[str, Dict[str, float]]:
    """Each pitcher's sequencing habit: after each pitch, some pitches are preferred, some avoided."""
    return {prev: {p: float(rng.choice([0.5, 1.0, 1.0, 1.8])) for p in arsenal} for prev in arsenal}


def _pitcher(team: str, rng: np.random.Generator, used: set) -> Pitcher:
    """About a quarter of pitchers are left-handed. Each has his own speed and pitch shapes."""
    arsenal = _arsenal(rng)
    shape = {k: (float(rng.normal(0, 4)), float(rng.normal(0, 4))) for k in arsenal}
    return Pitcher(_name(rng, used), team, float(rng.uniform(9, 17)), arsenal,
                   float(rng.uniform(0.2, 1.2)), _follow(arsenal, rng), int(rng.integers(70, 105)),
                   "L" if rng.random() < 0.27 else "R", float(rng.normal(0, 2.5)), shape)


def _batter(team: str, rng: np.random.Generator, used: set) -> Batter:
    """About 58% bat right-handed, 36% left, 6% switch."""
    return Batter(_name(rng, used), team, float(np.clip(rng.normal(181, 6), 168, 197)),
                  float(rng.beta(5, 4)), float(rng.beta(6, 3)), float(rng.beta(3, 5)),
                  str(rng.choice(["R", "L", "S"], p=[0.58, 0.36, 0.06])))


def roster(team: str, rng: np.random.Generator, used: set) -> tuple:
    """Six pitchers and nine batters for one team."""
    pitchers = [_pitcher(team, rng, used) for _ in range(6)]
    batters = [_batter(team, rng, used) for _ in range(9)]
    return pitchers, batters


def league(seed: int = 2025) -> Dict[str, tuple]:
    rng = np.random.default_rng(seed)
    used: set = set()
    return {team: roster(team, rng, used) for team in TEAMS}


def all_pitchers(rosters: Dict[str, tuple]) -> List[Pitcher]:
    return [p for pitchers, _ in rosters.values() for p in pitchers]
