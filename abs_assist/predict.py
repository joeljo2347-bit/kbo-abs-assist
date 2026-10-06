"""Predict the next pitch type, learning from every pitch it sees.

For each pitcher it counts which pitch he throws against each batter side, in each count, and after
each previous pitch. Thin situations borrow from broader ones: (pitcher, side, count, previous pitch)
leans on (pitcher, side, count), then (pitcher, side), then his overall mix, then the league's habit
in that count.
Each new pitch updates every level at once, so predictions sharpen as games come in.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Hashable, List, Tuple

PRIOR = 6.0  # how many pitches of evidence a broader level is worth


def _situation(balls: int, strikes: int) -> str:
    return "two_strikes" if strikes == 2 else "behind" if balls > strikes else "ahead" if strikes > balls else "even"


class PitchPredictor:
    def __init__(self, prior: float = PRIOR):
        self.prior = prior
        self.counts: Dict[Hashable, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.types: set = set()

    def _levels(self, pitcher: str, balls: int, strikes: int, prev: str, side: str = "") -> List[Hashable]:
        """From broadest to most specific."""
        s = _situation(balls, strikes)
        return [("league", s), ("p", pitcher), ("p", pitcher, side), ("p", pitcher, side, s), ("p", pitcher, side, s, prev)]

    def update(self, pitcher: str, balls: int, strikes: int, prev: str, thrown: str, side: str = "") -> None:
        self.types.add(thrown)
        for key in self._levels(pitcher, balls, strikes, prev, side):
            self.counts[key][thrown] += 1

    def predict(self, pitcher: str, balls: int, strikes: int, prev: str = "", side: str = "") -> Dict[str, float]:
        """Probability of each pitch type, most likely first."""
        if not self.types:
            return {}
        dist = {t: 1 / len(self.types) for t in self.types}
        for key in self._levels(pitcher, balls, strikes, prev, side):
            seen = self.counts.get(key, {})
            n = sum(seen.values())
            dist = {t: (seen.get(t, 0) + self.prior * dist[t]) / (n + self.prior) for t in self.types}
        if ("p", pitcher) in self.counts:  # only pitches he actually throws
            dist = {t: p for t, p in dist.items() if self.counts[("p", pitcher)].get(t)}
        total = sum(dist.values())
        return dict(sorted(((t, p / total) for t, p in dist.items()), key=lambda kv: -kv[1]))

    def top(self, pitcher: str, balls: int, strikes: int, prev: str = "", side: str = "") -> Tuple[str, float]:
        dist = self.predict(pitcher, balls, strikes, prev, side)
        return next(iter(dist.items())) if dist else ("", 0.0)
