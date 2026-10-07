"""The analysis tools the AI coach can call. Each returns plain data; the coach only explains it."""

from __future__ import annotations

import sqlite3
import unicodedata
from typing import Any, Callable, Dict, List, Optional, Tuple

from abs_assist.analyze import batter_profile, lost_at_back, pitcher_profile, rows
from abs_assist.compare import BATTER_METRICS, PITCHER_METRICS, League, arsenal, context, leaderboard, low_pitch_calls
from abs_assist.predict import PitchPredictor
from abs_assist.strategy import Strategy
from abs_assist.zone import SEASON_SHARES, ZONE_WIDTH_CM


def train_predictor(db: sqlite3.Connection) -> PitchPredictor:
    """Replay every collected pitch in order, as the live system would have seen it."""
    model = PitchPredictor()
    prev: Dict[Tuple[int, str], str] = {}
    for game, pitcher, balls, strikes, kind, side in db.execute(
            "SELECT game_id, pitcher, balls, strikes, pitch_type, batter_side FROM pitches ORDER BY id"):
        model.update(pitcher, balls, strikes, prev.get((game, pitcher), ""), kind, side or "")
        prev[(game, pitcher)] = kind
    return model


def _param(kind: str, about: str) -> Dict[str, str]:
    return {"type": kind, "description": about}


SCHEMA: List[Dict[str, Any]] = [
    {"name": "find_players", "description": "Find pitchers and batters by (part of) a name or a team name.",
     "parameters": {"query": _param("string", "name or team, e.g. 'LG Twins' or 'Kim'")}},
    {"name": "pitcher_profile", "description": "A pitcher's mix, zone rate, chase and whiff rates, fastball speed, strikes lost at the "
                                            "back of the plate, each with the league average and his rank.",
     "parameters": {"pitcher": _param("string", "exact pitcher name")}},
    {"name": "batter_profile", "description": "A batter's height, ABS zone, swing, chase and whiff rates, batting average, home runs, "
                                           "strikeouts and walks, each rate with the league average and his rank.",
     "parameters": {"batter": _param("string", "exact batter name")}},
    {"name": "recommend_pitch", "description": "Best and worst pitch types and locations for this pitcher against this batter in a count.",
     "parameters": {"pitcher": _param("string", "exact pitcher name"), "batter": _param("string", "exact batter name"),
                    "balls": _param("integer", "0-3"), "strikes": _param("integer", "0-2")}},
    {"name": "attack_plan",
     "description": "How to pitch a batter in a count when NO pitcher is named: the best and worst pitch types "
                    "and locations in the league against him. If a pitcher is named, use recommend_pitch instead.",
     "parameters": {"batter": _param("string", "exact batter name"), "balls": _param("integer", "0-3"),
                    "strikes": _param("integer", "0-2")}},
    {"name": "take_guide", "description": "The hitter's side: pitches this batter should take or lay off in a count "
                                       "(he does better taking than swinging).",
     "parameters": {"batter": _param("string", "exact batter name"), "balls": _param("integer", "0-3"),
                    "strikes": _param("integer", "0-2")}},
    {"name": "predict_next_pitch", "description": "Probability of each pitch type this pitcher throws next.",
     "parameters": {"pitcher": _param("string", "exact pitcher name"), "balls": _param("integer", "0-3"),
                    "strikes": _param("integer", "0-2"), "previous_pitch": _param("string", "previous pitch type, or empty"),
                    "batter_side": _param("string", "the batter's side, R or L, or empty if unknown")}},
    {"name": "pitcher_arsenal", "description": "A pitcher's pitch types: usage, average and top speed, movement, whiff rate, "
                                            "and what he throws next after each pitch.",
     "parameters": {"pitcher": _param("string", "exact pitcher name")}},
    {"name": "leaderboard", "description": "Rank players or teams by a stat (who leads, who is highest or lowest), with the league "
                                        "average. Use it for any 'who/which ... most' question instead of looking players up one by one.",
     "parameters": {"metric": _param("string", "batters/team_batting: " + ", ".join(BATTER_METRICS)
                                     + "; pitchers/team_pitching: " + ", ".join(PITCHER_METRICS)),
                    "who": _param("string", "batters, pitchers, team_batting or team_pitching"),
                    "team": _param("string", "limit players to one team, or empty for the league")}},
    {"name": "abs_rules", "description": "The KBO's published ABS zone rules, how the zone follows the batter's height, and how "
                                      "often low pitches of each type are called balls (the two-plane effect).",
     "parameters": {}},
    {"name": "strikes_lost_at_back",
     "description": "Taken pitches in the zone at the middle of the plate but called balls at the back edge, for a team's pitchers.",
     "parameters": {"team": _param("string", "team name, e.g. 'LG Twins'")}},
]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    for dash in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014"):
        text = text.replace(dash, "-")
    return " ".join(text.replace("\u202f", " ").replace("\u00a0", " ").split()).lower()


class Toolbox:
    def __init__(self, db: sqlite3.Connection):
        self.db, self.strategy, self.predictor, self.league = db, Strategy(db), train_predictor(db), League(db)
        self.run: Dict[str, Callable[..., Any]] = {
            "find_players": self.find_players,
            "pitcher_profile": self.pitcher_profile,
            "batter_profile": self.batter_profile,
            "pitcher_arsenal": lambda pitcher: arsenal(db, pitcher),
            "leaderboard": lambda metric, who="batters", team="": leaderboard(self.league, metric, who, team),
            "abs_rules": self.abs_rules,
            "recommend_pitch": lambda pitcher, batter, balls, strikes: self.strategy.recommend(pitcher, batter, int(balls), int(strikes)),
            "take_guide": lambda batter, balls, strikes: self.strategy.take_guide(batter, int(balls), int(strikes)),
            "attack_plan": lambda batter, balls, strikes: self.strategy.attack_plan(batter, int(balls), int(strikes)),
            "predict_next_pitch": lambda pitcher, balls, strikes, previous_pitch="", batter_side="": {
                k: round(v, 3) for k, v in self.predictor.predict(
                    pitcher, int(balls), int(strikes), previous_pitch, batter_side).items()},
            "strikes_lost_at_back": lambda team="": lost_at_back(rows(self.db, pitcher_team=team or None)),
        }

    def pitcher_profile(self, pitcher: str) -> Dict[str, Any]:
        out = pitcher_profile(self.db, pitcher)
        line = self.league.qualified("pitcher").get(pitcher, {})
        return out if "error" in out else {**out, "fastball_kmh": line.get("fastball_kmh"),
                                           "compared_with_league": context(self.league, "pitcher", pitcher, PITCHER_METRICS)}

    def batter_profile(self, batter: str) -> Dict[str, Any]:
        out = batter_profile(self.db, batter)
        line = self.league.qualified("batter").get(batter, {})
        stats = {k: line.get(k) for k in ("batting_average", "hits", "home_runs", "strikeouts", "walks")}
        return out if "error" in out else {**out, **stats,
                                           "compared_with_league": context(self.league, "batter", batter, BATTER_METRICS)}

    def abs_rules(self) -> Dict[str, Any]:
        shares = {str(y): {"top_share_of_height": t, "bottom_share_of_height": b} for y, (t, b) in SEASON_SHARES.items()}
        return {"how_to_read": "The zone's top and bottom are fixed shares of the batter's height, so taller batters get a "
                               "higher, taller zone. Top and bottom are checked at the middle and at the back of the plate; "
                               "the sides once, at the middle. low_pitches: taken pitches in the bottom tenth of the zone at "
                               "the middle of the plate, and the share ABS called balls (it was still dropping by the back).",
                "zone_by_season": shares, "width_cm": ZONE_WIDTH_CM, "plate_depth_cm": 43.18, "middle_to_back_cm": 21.59,
                "example_180cm_batter_2025_cm": [round(180 * SEASON_SHARES[2025][1], 1), round(180 * SEASON_SHARES[2025][0], 1)],
                "low_pitches": self.league.cached("low_pitches", lambda: low_pitch_calls(self.db))}

    def find_players(self, query: str) -> Dict[str, List[str]]:
        """Players whose name or team contains the query (% and _ are matched literally)."""
        like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        sql = "SELECT DISTINCT {0} FROM pitches WHERE {0} LIKE ? ESCAPE '\\' OR {0}_team LIKE ? ESCAPE '\\' LIMIT 12"
        pitchers = self.db.execute(sql.format("pitcher"), (like, like))
        batters = self.db.execute(sql.format("batter"), (like, like))
        return {"pitchers": [p for (p,) in pitchers], "batters": [b for (b,) in batters]}

    def resolve(self, value: str, column: str) -> Optional[str]:
        """A player's exact name from how a person (or a model) typed it: Unicode spaces and hyphens,
        any case, or a unique part of the name."""
        wanted = normalize(value)
        names = [n for (n,) in self.db.execute(f"SELECT DISTINCT {column} FROM pitches")]
        exact = [n for n in names if normalize(n) == wanted]
        partial = [n for n in names if wanted in normalize(n)]
        return exact[0] if exact else partial[0] if len(partial) == 1 else None

    def _typed(self, name: str, args: Dict[str, Any]) -> Optional[str]:
        """An error if an argument is unknown, or text where text is expected isn't."""
        params = next(t["parameters"] for t in SCHEMA if t["name"] == name)
        for key, value in args.items():
            if key not in params:
                return f"{name} has no argument {key!r}; its arguments are {', '.join(params)}."
            if params[key]["type"] == "string" and not isinstance(value, str):
                return f"{key} must be text."
        return None

    def _pitch_context(self, args: Dict[str, Any]) -> Any:
        """predict_next_pitch's previous pitch and batter side, normalized, or an error dict."""
        prev = normalize(args.get("previous_pitch") or "")
        side = (args.get("batter_side") or "").strip().upper()[:1]
        if prev and prev not in self.predictor.types:
            return {"error": f"previous_pitch must be one of {', '.join(sorted(self.predictor.types))}, or empty."}
        if side not in ("", "R", "L"):
            return {"error": "batter_side must be R, L or empty."}
        return {**args, "previous_pitch": prev, "batter_side": side}

    def _checked(self, args: Dict[str, Any]) -> Any:
        """Arguments with names resolved and counts checked, or an error dict."""
        for key, top in (("balls", 3), ("strikes", 2)):
            if key in args:
                try:
                    args = {**args, key: int(args[key])}
                except (TypeError, ValueError):
                    return {"error": f"{key} must be a whole number"}
                if not 0 <= args[key] <= top:
                    return {"error": f"{key} must be between 0 and {top}"}
        if isinstance(args.get("team"), str) and args["team"].strip().lower() not in ("", "all", "all teams"):
            team = self.resolve(args["team"], "pitcher_team")
            if team is None:
                return {"error": f"No team named {args['team']!r}."}
            args = {**args, "team": team}
        elif "team" in args:
            args = {**args, "team": ""}
        return args

    def _not_found(self, column: str, name: str) -> str:
        other = "batter" if column == "pitcher" else "pitcher"
        if self.resolve(name, other):
            return f"{name} is a {other}, not a {column}; use the {other} tools (e.g. {other}_profile)."
        return f"No {column} named {name!r}; use find_players."

    def call(self, name: str, args: Dict[str, Any]) -> Any:
        if name not in self.run:
            return {"error": f"Unknown tool {name}."}
        problem = self._typed(name, args)
        if problem:
            return {"error": problem}
        args = self._checked(self._pitch_context(args) if name == "predict_next_pitch" else args)
        if "error" in args:
            return args
        for column in ("pitcher", "batter"):
            if isinstance(args.get(column), str) and args[column]:
                found = self.resolve(args[column], column)
                if found is None:
                    return {"error": self._not_found(column, args[column])}
                args = {**args, column: found}
        try:
            return self.run[name](**args)
        except Exception as exc:  # the model reads the error and can try again; the request never fails
            return {"error": f"{name} failed: {exc}"}
