"""How well does next-pitch prediction work, and does it improve as it sees more games?

Streams a simulated season in order. Before each pitch the model predicts; then it learns the
pitch. Compared with two baselines and with the ceiling: the simulation's own odds for that pitch,
which no predictor can beat on average because pitchers choose with some randomness.

    python -m evals.predict_eval            # writes evals/predict_results.md
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List

import numpy as np

from abs_assist.players import all_pitchers, league
from abs_assist.predict import PitchPredictor
from abs_assist.sim import season, type_odds

HERE = Path(__file__).resolve().parent
GAMES, SEED, BINS = 720, 2025, 6


def true_odds(pitcher, r: Dict, prev: str) -> Dict[str, float]:
    """The simulation's own probabilities for this pitch: the same function it draws from."""
    return type_odds(pitcher, r["balls"], r["strikes"], prev, r["batter_side"] == r["pitcher_throws"])


def score_stream(games: int = GAMES, seed: int = SEED) -> List[Dict]:
    """One record per pitch: game, and whether each method's top guess was right."""
    pitchers = {p.name: p for p in all_pitchers(league(seed))}
    model, out = PitchPredictor(), []
    mix: Dict[str, Dict[str, int]] = {}
    prev: Dict[tuple, str] = {}
    for r in season(games, seed):
        name, key = r["pitcher"], (r["game_id"], r["pitcher"])
        before = prev.get(key, "")
        side = r["batter_side"]
        guess, p_model = model.top(name, r["balls"], r["strikes"], before, side)
        dist = model.predict(name, r["balls"], r["strikes"], before, side)
        own = mix.setdefault(name, {})
        odds = true_odds(pitchers[name], r, before)
        out.append({"game": r["game_id"], "model": guess == r["pitch_type"],
                    "own_mix": bool(own) and max(own, key=lambda k: own[k]) == r["pitch_type"],
                    "fastball": r["pitch_type"] == "fastball",
                    "ceiling": max(odds, key=lambda k: odds[k]) == r["pitch_type"],
                    "logloss": -math.log(max(dist.get(r["pitch_type"], 1e-6), 1e-6)) if dist else None})
        model.update(name, r["balls"], r["strikes"], before, r["pitch_type"], side)
        own[r["pitch_type"]] = own.get(r["pitch_type"], 0) + 1
        prev[key] = r["pitch_type"]
    return out


def table(records: List[Dict], games: int = GAMES) -> List[str]:
    edges = np.linspace(0, games, BINS + 1).astype(int)
    lines = ["| Games seen | Model | Pitcher's own most common pitch | Always fastball | Ceiling (true odds) |",
             "|---|---|---|---|---|"]
    for lo, hi in zip(edges[:-1], edges[1:]):
        part = [r for r in records if lo <= r["game"] < hi]
        pct = {m: sum(r[m] for r in part) / len(part) for m in ("model", "own_mix", "fastball", "ceiling")}
        lines.append(f"| {lo}-{hi} | **{pct['model']:.1%}** | {pct['own_mix']:.1%} | {pct['fastball']:.1%} | {pct['ceiling']:.1%} |")
    return lines


def main() -> None:
    records = score_stream()
    late = [r for r in records if r["game"] >= GAMES // 2]
    gap = sum(r["ceiling"] for r in late) / len(late) - sum(r["model"] for r in late) / len(late)
    text = ["# Next-pitch prediction", "",
            f"{len(records):,} pitches over {GAMES} simulated games, predicted before each pitch, learned after.", "",
            *table(records), "",
            f"Second half of the season: the model is within {gap:.1%} of the ceiling."]
    (HERE / "predict_results.md").write_text("\n".join(text) + "\n")
    print("\n".join(text))


if __name__ == "__main__":
    main()
