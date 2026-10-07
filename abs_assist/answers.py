"""A one-sentence answer for each tool result, written by code from the result alone.

The coach is told to build on it. If the model's answer still fails the checks after one rewrite,
this sentence is shown instead, so the worst case is a plain, correct answer rather than a wrong one.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

Result = Dict[str, Any]


def _pct(x: Optional[float]) -> str:
    return "–" if x is None else f"{round(x * 100)}%"


def _stat(metric: str, v: Optional[float]) -> str:
    if v is None:
        return "–"
    if metric == "batting_average":
        return f"{v:.3f}".lstrip("0")
    return _pct(v) if metric.endswith("_rate") else f"{round(v, 1):g}"


def pitch_plan(r: Result) -> Optional[str]:
    if not r.get("best"):
        return None
    b = r["best"][0]
    text = (f"On {r['count']}, throw a {b['pitch']}: {_pct(b['called_strike_chance_if_taken'])} called strike if he takes it, "
            f"{_pct(b['whiff_chance_if_swung_at'])} whiff if he swings, leaving him {b['batter_value_after_runs']} expected runs "
            f"(from {r['batter_value_now_runs']} before the pitch).")
    return text + (f" Avoid a {r['worst'][-1]['pitch']}." if r.get("worst") else "")


def take(r: Result) -> Optional[str]:
    verdict, _, detail = str(r.get("summary", "")).removeprefix("Verdict: ").partition(". ")
    return f"On {r['count']}: {verdict}. {detail}" if verdict else None


def board(r: Result) -> Optional[str]:
    rows: List[Result] = r.get("ranking") or []
    if not rows:
        return None
    m = r["metric"]
    first = ", then ".join(f"{x['name']}" + ("" if x["name"] == x.get("team") else f" ({x['team']})") + f" {_stat(m, x[m])}"
                           for x in rows[:3])
    kind = "teams" if str(r.get("who", "")).startswith("team") else "players"
    return f"Highest {m.replace('_', ' ')} among {kind}: {first}. League average: {_stat(m, r.get('league_average'))}."


def arsenal(r: Result) -> Optional[str]:
    if not r.get("arsenal"):
        return None
    main = r["arsenal"][0]
    text = f"{r['pitcher']}'s main pitch is the {main['pitch']} ({_pct(main['usage'])} of his pitches, {main['avg_kmh']} km/h on average)."
    best = next((a for a in r["arsenal"] if a["pitch"] == r.get("secondary_with_most_whiffs")), None)
    return text + (f" His secondary pitch with the most swings and misses is the {best['pitch']} ({_pct(best['whiff_rate'])})." if best else "")


def predict(r: Result) -> Optional[str]:
    probs = list((r.get("probabilities") or {}).items())
    if not probs:
        return None
    return "Most likely next: " + ", then ".join(f"{k} ({_pct(v)})" for k, v in probs[:3]) + "."


def lost(r: Result) -> Optional[str]:
    kinds = list(r.get("by_pitch_type", {}).items())
    most = f", most on the {kinds[0][0]} ({kinds[0][1]})" if kinds else ""
    return (f"{r['strikes_lost']} taken pitches were strikes at the middle of the plate but balls at the back "
            f"({r['share_of_takes'] * 100:.1f}% of {r['taken_pitches']:,} taken){most}.")


WRITERS: Dict[str, Callable[[Result], Optional[str]]] = {
    "recommend_pitch": pitch_plan, "attack_plan": pitch_plan, "take_guide": take, "leaderboard": board,
    "pitcher_arsenal": arsenal, "predict_next_pitch": predict, "strikes_lost_at_back": lost,
}


def write(tool: str, result: Result) -> Optional[str]:
    """The code's own answer for this result, or None when the tool has none (profiles, rules, lists)."""
    try:
        return WRITERS[tool](result) if tool in WRITERS else None
    except (KeyError, IndexError, TypeError, ValueError):
        return None
