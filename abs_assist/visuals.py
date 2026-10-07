"""What the coach shows next to an answer, built from tool results only (never from the model's text).

Each block has one job, so a coach can read it at a glance:
- headline: the answer in a few words
- tiles:    two to four key numbers, each with a plain label
- bars:     one series, sorted, with the item that matters highlighted
- table:    players side by side, the leader in each column in bold
- zone:     the strike zone with one target region
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

Block = Dict[str, Any]
HEIGHTS = {"below the zone": "below", "knee-high": "low", "belt-high": "middle", "letter-high": "high", "above the zone": "above"}
SIDES = {"over the middle": "middle", "toward a corner": "inside", "on the edge": "edge", "off the plate": "off"}


def _round(x: float) -> int:
    """Ordinary rounding (72.5 -> 73), as people read numbers; Python's round() would give 72."""
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


def pct(x: Optional[float]) -> str:
    return "–" if x is None else f"{_round(x * 100)}%"


def per100(runs: float) -> str:
    return f"{_round(runs * 100)}"


def recommend(r: Dict[str, Any]) -> List[Block]:
    if not r["best"]:
        return []
    best = r["best"][0]
    kind, height, side = [p.strip() for p in best["pitch"].split(",")]
    return [
        {"type": "headline", "label": f"Best pitch on {r['count']}", "text": best["pitch"]},
        {"type": "zone", "height": HEIGHTS.get(height, "middle"), "side": SIDES.get(side, "middle"), "label": kind},
        {"type": "tiles", "items": [
            {"label": "Called a strike if he takes it", "value": pct(best["called_strike_chance_if_taken"])},
            {"label": "He misses if he swings", "value": pct(best["whiff_chance_if_swung_at"])},
            {"label": "Hitter's expected runs per 100 plate appearances",
             "value": per100(best["batter_value_after_runs"]), "note": f"down from {per100(r['batter_value_now_runs'])} now"}]},
    ] + _avoid(r.get("worst") or [])


def _avoid(worst: List[Dict[str, Any]]) -> List[Block]:
    """The worst option as a note; nothing when there were too few options to name one."""
    if not worst:
        return []
    return [{"type": "note", "text": f"Avoid: {worst[-1]['pitch']} ({per100(worst[-1]['batter_value_after_runs'])} runs per 100)."}]


def _bars(title: str, data: Dict[str, float], fmt: Callable[[float], str]) -> Block:
    items = sorted(data.items(), key=lambda kv: -kv[1])
    return {"type": "bars", "title": title, "items": [
        {"label": k, "value": v, "display": fmt(v), "highlight": i == 0} for i, (k, v) in enumerate(items)]}


def predict(r: Dict[str, float]) -> List[Block]:
    top = max(r, key=lambda k: r[k]) if r else ""
    return [_bars(f"What he throws next: {top} most likely", r, pct)] if r else []


def lost(r: Dict[str, Any]) -> List[Block]:
    return [{"type": "tiles", "items": [
        {"label": "Strikes lost at the back of the plate", "value": f"{r['strikes_lost']:,}",
         "note": f"{r['share_of_takes'] * 100:.1f}% of {r['taken_pitches']:,} taken pitches"}]},
        _bars("Strikes lost, by pitch type", r["by_pitch_type"], lambda v: f"{int(v)}")]


def _league(r: Dict[str, Any], metric: str) -> str:
    """'league 26%' for a tile's note, when the profile carries the league average."""
    avg = (r.get("compared_with_league") or {}).get(metric, {}).get("league_average")
    return "" if avg is None else f"league {pct(avg)}"


def pitcher(r: Dict[str, Any]) -> List[Block]:
    tiles = [{"label": "Pitches in the zone", "value": pct(r["in_zone_rate"]), "note": _league(r, "in_zone_rate")},
             {"label": "Batters chase", "value": pct(r["chase_rate"]), "note": _league(r, "chase_rate")},
             {"label": "Swings that miss", "value": pct(r["whiff_rate"]), "note": _league(r, "whiff_rate")}]
    return [{"type": "tiles", "items": tiles}, _bars(f"{r['pitcher']}: how often he throws each pitch", r["pitch_mix"], pct)]


def batter(r: Dict[str, Any]) -> List[Block]:
    low, top = r["zone_cm"]
    return [{"type": "tiles", "items": [
        {"label": "His ABS zone", "value": f"{low:.0f}–{top:.0f} cm", "note": f"{r['height_cm']:.0f} cm tall"},
        {"label": "Swings at strikes", "value": pct(r["zone_swing_rate"]), "note": _league(r, "zone_swing_rate")},
        {"label": "Chases balls", "value": pct(r["chase_rate"]), "note": _league(r, "chase_rate")},
        {"label": "Swings that miss", "value": pct(r["whiff_rate"]), "note": _league(r, "whiff_rate")}]}]


def _stat(metric: str) -> Callable[[float], str]:
    if metric == "batting_average":
        return lambda v: f"{v:.3f}".lstrip("0")
    return pct if metric.endswith("_rate") else lambda v: f"{v:g}"


def board(r: Dict[str, Any]) -> List[Block]:
    metric = next(k for k in r["ranking"][0] if k not in ("name", "pitches"))
    fmt = _stat(metric)
    data = {row["name"]: row[metric] for row in r["ranking"]}
    avg = r.get("league_average")
    note = [{"type": "note", "text": f"League average: {fmt(avg)}. {r['qualified']} qualified."}] if avg is not None else []
    return [_bars(f"{metric.replace('_', ' ')}, highest first", data, fmt), *note]


def pitch_arsenal(r: Dict[str, Any]) -> List[Block]:
    rows = [[a["pitch"], pct(a["usage"]), f"{a['avg_kmh']} km/h", pct(a["whiff_rate"])] for a in r["arsenal"]]
    return [{"type": "table", "title": f"{r['pitcher']}'s pitches, most used first",
             "columns": ["Pitch", "Usage", "Average speed", "Swings that miss"], "rows": rows, "bold": []}]


def rules(r: Dict[str, Any]) -> List[Block]:
    low = {k: v["called_ball_rate"] for k, v in r["low_pitches"].items() if v["called_ball_rate"] is not None}
    return [_bars("Low pitches in the zone at the middle, called balls by ABS", low, pct)]


def take_guide(r: Dict[str, Any]) -> List[Block]:
    rows = [[t["pitch"], pct(t["p_called_strike"])] for t in r["take"]]
    return [{"type": "table", "title": f"Pitches to take on {r['count']}", "columns": ["Pitch", "Called a strike"],
             "rows": rows, "bold": []}] if rows else []


COMPARE = {"pitcher_profile": ("pitcher", [("in_zone_rate", "In the zone"), ("chase_rate", "Chase"), ("whiff_rate", "Swings that miss")]),
           "batter_profile": ("batter", [("zone_swing_rate", "Swings at strikes"), ("chase_rate", "Chases balls"),
                                         ("whiff_rate", "Swings that miss")])}


def compare(tool: str, results: List[Dict[str, Any]]) -> Block:
    """Players side by side; the highest value in each column is marked bold."""
    name, metrics = COMPARE[tool]
    rows = [[r[name], *[pct(r[m]) for m, _ in metrics]] for r in results]
    bold = []
    for col in range(1, len(metrics) + 1):  # bold the leaders as shown; a column where everyone ties has none
        shown = [_round((results[j][metrics[col - 1][0]] or 0) * 100) for j in range(len(results))]
        if len(set(shown)) > 1:
            bold += [[col, j] for j, v in enumerate(shown) if v == max(shown)]
    return {"type": "table", "title": "Side by side", "columns": ["", *[label for _, label in metrics]],
            "rows": rows, "bold": bold}


BUILDERS: Dict[str, Callable[[Any], List[Block]]] = {
    "recommend_pitch": recommend, "attack_plan": recommend, "predict_next_pitch": predict, "strikes_lost_at_back": lost,
    "pitcher_profile": pitcher, "batter_profile": batter, "take_guide": take_guide,
    "leaderboard": board, "pitcher_arsenal": pitch_arsenal, "abs_rules": rules}


def for_calls(calls: List[Dict[str, Any]]) -> List[Block]:
    """Blocks for the most useful result of this answer: a comparison when several players were
    looked up, otherwise the last useful tool's view."""
    found: Dict[str, List[Any]] = {}
    for call in calls:
        data = json.loads(call["result"])
        if call["tool"] in BUILDERS and not (isinstance(data, dict) and "error" in data):
            found.setdefault(call["tool"], []).append(data)
    try:
        for tool, results in found.items():
            if tool in COMPARE and len(results) > 1:
                return [compare(tool, results)]
        last = [c["tool"] for c in calls if c["tool"] in found]
        return BUILDERS[last[-1]](found[last[-1]][-1]) if last else []
    except (KeyError, IndexError, TypeError, ValueError, StopIteration):
        return []  # a visual must never break an answer
