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
    for game, pitcher, balls, strikes, kind in db.execute(
            "SELECT game_id, pitcher, balls, strikes, pitch_type FROM pitches ORDER BY id"):
        model.update(pitcher, balls, strikes, prev.get((game, pitcher), ""), kind)
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
    {"name": "take_guide", "description": "Pitches this batter does better to take than swing at in a count.",
     "parameters": {"batter": _param("string", "exact batter name"), "balls": _param("integer", "0-3"),
                    "strikes": _param("integer", "0-2")}},
    {"name": "predict_next_pitch", "description": "Probability of each pitch type this pitcher throws next.",
     "parameters": {"pitcher": _param("string", "exact pitcher name"), "balls": _param("integer", "0-3"),
                    "strikes": _param("integer", "0-2"), "previous_pitch": _param("string", "previous pitch type, or empty")}},
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
            "predict_next_pitch": lambda pitcher, balls, strikes, previous_pitch="": {
                k: round(v, 3) for k, v in self.predictor.predict(pitcher, int(balls), int(strikes), previous_pitch).items()},
            "strikes_lost_at_back": lambda team: lost_at_back(rows(self.db, pitcher_team=team)),
        }

    def find_players(self, query: str) -> Dict[str, List[str]]:
        like = f"%{query}%"
        pitchers = self.db.execute("SELECT DISTINCT pitcher FROM pitches WHERE pitcher LIKE ? OR pitcher_team LIKE ? LIMIT 12", (like, like))
        batters = self.db.execute("SELECT DISTINCT batter FROM pitches WHERE batter LIKE ? OR batter_team LIKE ? LIMIT 12", (like, like))
        return {"pitchers": [p for (p,) in pitchers], "batters": [b for (b,) in batters]}

    def resolve(self, value: str, column: str) -> Optional[str]:
        """A player's exact name from how a person (or a model) typed it: Unicode spaces and hyphens,
        any case, or a unique part of the name."""
        wanted = normalize(value)
        names = [n for (n,) in self.db.execute(f"SELECT DISTINCT {column} FROM pitches")]
        exact = [n for n in names if normalize(n) == wanted]
        partial = [n for n in names if wanted in normalize(n)]
        return exact[0] if exact else partial[0] if len(partial) == 1 else None

    def call(self, name: str, args: Dict[str, Any]) -> Any:
        if name not in self.run:
            return {"error": f"Unknown tool {name}."}
        for column in ("pitcher", "batter"):
            if isinstance(args.get(column), str) and args[column]:
                found = self.resolve(args[column], column)
                if found is None:
                    return {"error": f"No {column} named {args[column]!r}; use find_players."}
                args = {**args, column: found}
        try:
            return self.run[name](**args)
        except (TypeError, KeyError, ValueError) as exc:
            return {"error": f"{name} failed: {exc}"}
