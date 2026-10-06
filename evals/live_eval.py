"""Does the live velocity alert catch real fatigue, and how fast?

Baselines come from the first half of a simulated season; the tracker then watches every game of
the second half. Thresholds were tuned on the dev season (seed 2025) only; the reported numbers
come from a separate test season (seed 7) that was never used for tuning. The simulation knows
when each pitcher starts to tire (past his stamina), so each outing is scored: caught (alert
after fatigue began), how many pitches later, or a false alarm (alert before he was tired).

    python -m evals.live_eval               # writes evals/live_results.md
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

from abs_assist.collect import ingest, open_store
from abs_assist.live import LiveTracker, baseline
from abs_assist.players import all_pitchers, league
from abs_assist.sim import season

HERE = Path(__file__).resolve().parent
GAMES, DEV_SEED, TEST_SEED = 720, 2025, 7


def outings(rows: List[Dict]) -> Dict[tuple, List[Dict]]:
    by: Dict[tuple, List[Dict]] = defaultdict(list)
    for r in rows:
        by[(r["game_id"], r["pitcher"])].append(r)
    return by


def score(pitches: List[Dict], stamina: int, base) -> Dict:
    tracker = LiveTracker({pitches[0]["pitcher"]: base})
    first = next((p["pitch_no"] for p in pitches if any(a.startswith("velocity") for a in tracker.add(p))), None)
    tired = pitches[-1]["pitch_no"] > stamina + 8  # tired long enough for 8 fastballs to show it
    return {"tired": tired, "alert_at": first, "onset": stamina + 1}


def run(seed: int) -> List[Dict]:
    rows = list(season(GAMES, seed))
    db = open_store()
    ingest(db, [r for r in rows if r["game_id"] < GAMES // 2])
    stamina = {p.name: p.stamina for p in all_pitchers(league(seed))}
    results = []
    for (game, name), pitches in outings([r for r in rows if r["game_id"] >= GAMES // 2]).items():
        base = baseline(db, name, game)
        if base and len(pitches) >= 30:
            results.append(score(pitches, stamina[name], base))
    return results


def summarize(res: List[Dict]) -> List[str]:
    tired = [r for r in res if r["tired"]]
    caught = [r for r in tired if r["alert_at"] and r["alert_at"] >= r["onset"]]
    false = [r for r in res if r["alert_at"] and r["alert_at"] < r["onset"]]
    lag = statistics.median(r["alert_at"] - r["onset"] for r in caught) if caught else None
    return [f"{len(res)}", f"{len(tired)}", f"**{len(caught)}/{len(tired)}** ({len(caught) / max(len(tired), 1):.0%})",
            f"{lag}", f"{len(false)} ({len(false) / max(len(res), 1):.1%})"]


def main() -> None:
    dev, test = summarize(run(DEV_SEED)), summarize(run(TEST_SEED))
    labels = ["Outings of 30+ pitches (second half)", "Outings where the pitcher tired",
              "Fatigue caught by the velocity alert", "Median pitches from fatigue onset to alert",
              "False alarms (alert before any fatigue)"]
    text = ["# Live tracking: fatigue alerts", "",
            "Velocity alert: the last 8 fastballs vs the first 10 of the same game, beyond normal noise.", "",
            "| | Test season (reported) | Dev season (tuned on) |", "|---|---|---|",
            *[f"| {label} | {t} | {d} |" for label, t, d in zip(labels, test, dev)]]
    (HERE / "live_results.md").write_text("\n".join(text) + "\n")
    print("\n".join(text))


if __name__ == "__main__":
    main()
