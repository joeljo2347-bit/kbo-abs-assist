"""The analysis tools the AI coach can call. Each returns plain data; the coach only explains it."""

from __future__ import annotations

import sqlite3
import unicodedata
from typing import Any, Callable, Dict, List, Optional, Tuple

from abs_assist.analyze import batter_profile, lost_at_back, pitcher_profile, rows
from abs_assist.predict import PitchPredictor
from abs_assist.strategy import Strategy


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
    {"name": "pitcher_profile", "description": "A pitcher's mix, zone rate, chase and whiff rates, strikes lost at the back of the plate.",
     "parameters": {"pitcher": _param("string", "exact pitcher name")}},
    {"name": "batter_profile", "description": "A batter's height, ABS zone in cm, swing, chase and whiff rates.",
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
        self.db, self.strategy, self.predictor = db, Strategy(db), train_predictor(db)
        self.run: Dict[str, Callable[..., Any]] = {
            "find_players": self.find_players,
            "pitcher_profile": lambda pitcher: pitcher_profile(db, pitcher),
            "batter_profile": lambda batter: batter_profile(db, batter),
            "recommend_pitch": lambda pitcher, batter, balls, strikes: self.strategy.recommend(pitcher, batter, int(balls), int(strikes)),
            "take_guide": lambda batter, balls, strikes: self.strategy.take_guide(batter, int(balls), int(strikes)),
            "attack_plan": lambda batter, balls, strikes: self.strategy.attack_plan(batter, int(balls), int(strikes)),
            "predict_next_pitch": lambda pitcher, balls, strikes, previous_pitch="", batter_side="": {
                k: round(v, 3) for k, v in self.predictor.predict(
                    pitcher, int(balls), int(strikes), previous_pitch, batter_side).items()},
            "strikes_lost_at_back": lambda team="": lost_at_back(rows(self.db, pitcher_team=team or None)),
        }

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
        args = self._checked(args)
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
        except (TypeError, KeyError, ValueError) as exc:
            return {"error": f"{name} failed: {exc}"}
