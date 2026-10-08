"""HTTP API and the dashboard, on localhost.

    uvicorn abs_assist.api:app --port 8000      # then open http://localhost:8000

On first start the database is built from a simulated season (ABS_DB, default data/abs.db).
"""

from __future__ import annotations

import os
import random
import sqlite3
import threading
import urllib.error
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from abs_assist import scouting
from abs_assist.analyze import height_in_zone, rows
from abs_assist.coach import Coach, Conversation, http_chat
from abs_assist.collect import ingest, open_store
from abs_assist.live import LiveTracker, baseline
from abs_assist.predict import PitchPredictor
from abs_assist.scouting import Filters
from abs_assist.sim import season
from abs_assist.tools import Toolbox

STATIC = Path(__file__).resolve().parent / "static"
router = APIRouter()


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    conversation_id: Optional[str] = Field(None, max_length=40)


MAX_CONVERSATIONS = 200


def build_store(path: Path, games: int = 720) -> sqlite3.Connection:
    """Open the database, filling it from a simulated season if it has no pitches (also after a first
    build that was interrupted: ingest commits only at the end)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = open_store(path)
    if not db.execute("SELECT EXISTS(SELECT 1 FROM pitches)").fetchone()[0]:
        ingest(db, season(games))
    return db


def _tools(request: Request) -> Toolbox:
    return request.app.state.tools


def _locked(request: Request):
    """One SQLite connection is shared, and SQLite connections aren't safe across threads:
    database work runs one request at a time."""
    return request.app.state.lock


def _point(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": r["id"], "x": r["x_cm"], "h_mid": round(height_in_zone(r), 3),
            "h_end": round(height_in_zone(r, "z_end_cm"), 3), "strike": bool(r["abs_strike"]),
            "swing": bool(r["swing"]), "type": r["pitch_type"], "kmh": r["kmh"], "result": r["result"],
            "why": ("Strike" if r["abs_strike"] else "Ball") + f": {abs(r['abs_margin_cm']):.1f} cm "
                   + ("inside" if r["abs_strike"] else "outside") + f" the {r['abs_rule']} edge"}


@router.get("/")
def page() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@router.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@router.get("/api/meta")
def meta(request: Request) -> Dict[str, Any]:
    with _locked(request):
        return _meta(_tools(request).db)


def _meta(db: sqlite3.Connection) -> Dict[str, Any]:
    teams = [t for (t,) in db.execute("SELECT DISTINCT pitcher_team FROM pitches ORDER BY 1")]
    roster = {t: {"pitchers": [p for (p,) in db.execute("SELECT DISTINCT pitcher FROM pitches WHERE pitcher_team=? ORDER BY 1", (t,))],
                  "batters": [b for (b,) in db.execute("SELECT DISTINCT batter FROM pitches WHERE batter_team=? ORDER BY 1", (t,))]}
              for t in teams}
    games, pitches = db.execute("SELECT COUNT(DISTINCT game_id), COUNT(*) FROM pitches").fetchone()
    game_ids = [g for (g,) in db.execute("SELECT DISTINCT game_id FROM pitches ORDER BY 1")]
    return {"teams": teams, "roster": roster, "games": games, "game_ids": game_ids, "pitches": pitches}


@router.get("/api/pitches")
def pitches(request: Request, pitcher: Optional[str] = None, batter: Optional[str] = None,
            pitcher_team: Optional[str] = None, pitch_type: Optional[str] = None, taken_only: bool = True,
            limit: int = Query(1500, ge=1, le=5000)) -> Dict[str, Any]:
    with _locked(request):
        data = rows(_tools(request).db, pitcher=pitcher, batter=batter, pitcher_team=pitcher_team, pitch_type=pitch_type)
    data = [r for r in data if not (taken_only and r["swing"])]
    sample = random.Random(7).sample(data, min(limit, len(data)))
    return {"total": len(data), "shown": len(sample), "points": [_point(r) for r in sample]}


@router.get("/api/scout")
def scout(request: Request, pitcher: str, opponent: Optional[str] = None, side: Optional[str] = None,
          kmh_min: float = 0, kmh_max: float = 200, hb_min: float = -100, hb_max: float = 100,
          ivb_min: float = -100, ivb_max: float = 100) -> Dict[str, Any]:
    """A pitcher's arsenal against a team, filtered by batter side, speed and movement."""
    if side not in (None, "", "R", "L"):
        raise HTTPException(422, "side must be R or L")
    filters = Filters(side or None, (kmh_min, kmh_max), (hb_min, hb_max), (ivb_min, ivb_max))
    with _locked(request):
        name = _tools(request).resolve(pitcher, "pitcher")
        if name is None:
            raise HTTPException(404, f"No pitcher named {pitcher!r}.")
        return scouting.scout(_tools(request).db, name, opponent or None, filters)


@router.get("/api/tool/{name}")
def tool(name: str, request: Request) -> Any:
    """Any analysis tool by name, with its arguments as query parameters (the coach uses the same)."""
    with _locked(request):
        result = _tools(request).call(name, dict(request.query_params))
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(400, result["error"])
    return result


def _predictor_before(db: sqlite3.Connection, game_id: int):
    """A predictor that has seen only games before this one, so a replay never peeks ahead."""
    model = PitchPredictor()
    prev: Dict[tuple, str] = {}
    for game, pitcher, balls, strikes, kind, side in db.execute(
            "SELECT game_id, pitcher, balls, strikes, pitch_type, batter_side FROM pitches WHERE game_id < ? ORDER BY id",
            (game_id,)):
        model.update(pitcher, balls, strikes, prev.get((game, pitcher), ""), kind, side or "")
        prev[(game, pitcher)] = kind
    return model


def _replay_step(r: Dict[str, Any], model, tracker: LiveTracker, prev: Dict[str, str]) -> Dict[str, Any]:
    side = r["batter_side"] or ""
    guess = model.predict(r["pitcher"], r["balls"], r["strikes"], prev.get(r["pitcher"], ""), side)
    alerts = tracker.add(r)
    model.update(r["pitcher"], r["balls"], r["strikes"], prev.get(r["pitcher"], ""), r["pitch_type"], side)
    prev[r["pitcher"]] = r["pitch_type"]
    return {**_point(r), "inning": r["inning"], "half": r["half"], "pitcher": r["pitcher"], "batter": r["batter"],
            "count": f"{r['balls']}-{r['strikes']}",
            "predicted": [[k, round(v, 3)] for k, v in list(guess.items())[:3]],
            "hit": bool(guess) and next(iter(guess)) == r["pitch_type"], "alerts": alerts,
            "pa_result": r["pa_result"]}


@router.get("/api/live/{game_id}")
def live(request: Request, game_id: int = PathParam(ge=0, le=2**63 - 1)) -> Dict[str, Any]:
    db = _tools(request).db
    with _locked(request):
        game = sorted(rows(db, game_id=game_id), key=lambda r: r["id"])
        if not game:
            raise HTTPException(404, "No such game.")
        model = _predictor_before(db, game_id)
        names = {r["pitcher"] for r in game}
        tracker = LiveTracker({n: b for n in names if (b := baseline(db, n, game_id))})
    prev: Dict[str, str] = {}
    steps = [_replay_step(r, model, tracker, prev) for r in game]
    teams = {r["half"]: r["batter_team"] for r in game}
    return {"game_id": game_id, "away": teams.get("top"), "home": teams.get("bottom"), "pitches": steps,
            "summaries": [tracker.summary(n) for n in sorted(names)]}


def _conversation(request: Request, conversation_id: Optional[str]) -> Tuple[str, Conversation]:
    """The conversation to continue, or a new one. The oldest are forgotten past MAX_CONVERSATIONS."""
    store: "OrderedDict[str, Conversation]" = request.app.state.conversations
    with request.app.state.conversations_lock:
        if conversation_id and conversation_id in store:
            store.move_to_end(conversation_id)
            return conversation_id, store[conversation_id]
        new_id = uuid.uuid4().hex[:16]
        store[new_id] = Conversation()
        while len(store) > MAX_CONVERSATIONS:
            store.popitem(last=False)
        return new_id, store[new_id]


@router.post("/api/coach")
def coach(body: Question, request: Request) -> Dict[str, Any]:
    conversation_id, convo = _conversation(request, body.conversation_id)
    try:
        with convo.lock:  # one question at a time per conversation
            out = Coach(_tools(request), request.app.state.chat, _locked(request), request.app.state.writer).ask(body.question, convo)
    except (urllib.error.URLError, OSError) as exc:
        raise HTTPException(503, f"The AI coach needs a model server (see README): {exc}") from None
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(503, f"The model server sent a reply the coach couldn't read: {exc!r}") from None
    return {"conversation_id": conversation_id, "answer": out["answer"],
            "tools_used": out["tools_used"], "visuals": out["visuals"]}


def create_app(db: Optional[sqlite3.Connection] = None, chat=None) -> FastAPI:
    app = FastAPI(title="KBO ABS Assist", version="1.0")
    db = db or build_store(Path(os.environ.get("ABS_DB", "data/abs.db")))
    app.state.tools, app.state.chat, app.state.lock = Toolbox(db), chat or http_chat(), threading.Lock()
    app.state.writer = chat is None and os.environ.get("COACH_WRITER", "model") == "model"  # tests script the model
    app.state.conversations, app.state.conversations_lock = OrderedDict(), threading.Lock()
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


_app: List[FastAPI] = []


def __getattr__(name: str):  # `uvicorn abs_assist.api:app` builds the app once, on first use
    if name == "app":
        if not _app:
            _app.append(create_app())
        return _app[0]
    raise AttributeError(name)
