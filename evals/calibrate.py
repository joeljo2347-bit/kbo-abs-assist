"""Fit the simulation to the KBO's official league totals.

    python -m evals.calibrate        # writes evals/calibration.md

Targets: KBO official team records, 2026 regular season (704 of 720 games), retrieved 2026-10-06:
koreabaseball.com/Record/Team/Hitter/Basic1.aspx, Basic2.aspx and Record/Team/Pitcher/Basic2.aspx.
League totals: 55,474 PA, 10,817 SO, 5,205 BB, 1,327 HR, .268 AVG, 216,587 pitches.
A grid search scores each setting by squared relative error, averaged over three simulated leagues
(each seed draws different players), then the best one is checked on a fresh full season.
"""

from __future__ import annotations

import itertools
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

from abs_assist import sim

HERE = Path(__file__).resolve().parent
TARGETS = {"K%": 10817 / 55474, "BB%": 5205 / 55474, "HR%": 1327 / 55474, "AVG": 0.268, "P/PA": 216587 / 55474}
GRID = {"whiff": [0.50, 0.58, 0.66, 0.74], "hit": [0.28, 0.30, 0.32], "power": [0.70, 0.80]}


def league_rates(games: int, seed: int) -> Dict[str, float]:
    rows = list(sim.season(games, seed))
    pa = Counter(r["pa_result"] for r in rows if r.get("pa_result"))
    n = sum(pa.values())
    hits = pa["single"] + pa["double"] + pa["home_run"]
    return {"K%": pa["strikeout"] / n, "BB%": pa["walk"] / n, "HR%": pa["home_run"] / n,
            "AVG": hits / (n - pa["walk"]), "P/PA": len(rows) / n}


def error(rates: Dict[str, float]) -> float:
    return sum(((rates[k] - t) / t) ** 2 for k, t in TARGETS.items())


def search(games: int = 150, seeds: Tuple[int, ...] = (11, 12, 13)) -> List[Tuple[float, Dict[str, float]]]:
    """Each setting is scored on several leagues (each seed draws different players), so the fit is
    to the league in general, not to one random roster."""
    scored = []
    for values in itertools.product(*GRID.values()):
        sim.CAL = sim.Calibration(**dict(zip(GRID, values)))
        rates = [league_rates(games, seed) for seed in seeds]
        mean = {k: sum(r[k] for r in rates) / len(rates) for k in TARGETS}
        scored.append((error(mean), dict(zip(GRID, values))))
    return sorted(scored, key=lambda t: t[0])


def main() -> None:
    ranked = search()
    best = ranked[0][1]
    sim.CAL = sim.Calibration(**best)
    check = league_rates(720, 2026)
    fmt = lambda k, v: f"{v:.3f}" if k in ("AVG",) else f"{v:.2f}" if k == "P/PA" else f"{v:.1%}"  # noqa: E731
    lines = ["# Calibration to the KBO's 2026 league totals", "",
             f"Grid search over {len(ranked)} settings; best: {best}.", "",
             "| | KBO 2026 (official) | Simulation, fresh 720-game season |", "|---|---|---|"]
    lines += [f"| {k} | {fmt(k, t)} | {fmt(k, check[k])} |" for k, t in TARGETS.items()]
    (HERE / "calibration.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
