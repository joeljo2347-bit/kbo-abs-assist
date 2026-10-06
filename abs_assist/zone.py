"""The KBO ABS strike zone and how it calls a pitch.

Published rules (KBO, 2024-2025):
- Top and bottom of the zone are a share of the batter's height: 56.35% / 27.64% in 2024,
  55.75% / 27.04% from 2025 (the same size, lowered).
- Top and bottom are checked twice, at the middle of home plate and at its back edge: the pitch
  must be inside both times.
- Left and right: the plate (43.18 cm) plus 2 cm on each side, 47.18 cm in all, checked once at
  the middle of the plate. Where the catcher catches the ball doesn't matter.

Not specified in the published text: whether any part of the ball counts or only its center.
`ZoneRules.ball_radius_cm` makes that a setting; 0 (the center) is the default.

Coordinates: `x_cm` is left-right from the middle of the plate (0 = center); heights are cm
above the ground.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

SEASON_SHARES = {2024: (0.5635, 0.2764), 2025: (0.5575, 0.2704)}
ZONE_WIDTH_CM = 47.18


@dataclass(frozen=True)
class ZoneRules:
    season: int = 2025
    ball_radius_cm: float = 0.0

    def bounds(self, batter_height_cm: float) -> Tuple[float, float]:
        """(bottom, top) of the zone in cm for a batter of this height."""
        top_share, bottom_share = SEASON_SHARES[self.season]
        return batter_height_cm * bottom_share, batter_height_cm * top_share


@dataclass(frozen=True)
class Pitch:
    x_cm: float            # left-right at the middle of the plate
    z_mid_cm: float        # height at the middle of the plate
    z_end_cm: float        # height at the back edge of the plate
    batter_height_cm: float


@dataclass(frozen=True)
class Call:
    strike: bool
    margin_cm: float       # how far inside (+) or outside (-) the deciding edge the pitch was
    deciding_rule: str     # e.g. "bottom, back of plate"

    def explain(self) -> str:
        word = "Strike" if self.strike else "Ball"
        side = "inside" if self.strike else "outside"
        return f"{word}: {abs(self.margin_cm):.1f} cm {side} the {self.deciding_rule} edge"


def margins(pitch: Pitch, rules: ZoneRules) -> List[Tuple[float, str]]:
    """Distance inside each edge the rules check (negative = outside), with the edge's name."""
    bottom, top = rules.bounds(pitch.batter_height_cm)
    r = rules.ball_radius_cm
    return [
        (ZONE_WIDTH_CM / 2 + r - abs(pitch.x_cm), "side, middle of plate"),
        (top + r - pitch.z_mid_cm, "top, middle of plate"),
        (top + r - pitch.z_end_cm, "top, back of plate"),
        (pitch.z_mid_cm - bottom + r, "bottom, middle of plate"),
        (pitch.z_end_cm - bottom + r, "bottom, back of plate"),
    ]


def call(pitch: Pitch, rules: ZoneRules = ZoneRules()) -> Call:
    """A strike only if the pitch is inside every edge; the closest edge decides."""
    margin, rule = min(margins(pitch, rules))
    return Call(strike=margin >= 0, margin_cm=round(margin, 2), deciding_rule=rule)
