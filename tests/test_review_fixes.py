"""Regressions for bugs found in an independent review."""

import json

import pytest
from fastapi.testclient import TestClient

from abs_assist import visuals
from abs_assist.api import build_store, create_app
from abs_assist.coach import MAX_ROUNDS, RULE_FACTS, Coach, Conversation, problems
from abs_assist.collect import ingest, open_store, validate
from abs_assist.sim import season
from abs_assist.tools import Toolbox
from tests.test_collect import event


@pytest.fixture(scope="module")
def tools():
    db = open_store()
    ingest(db, season(30))
    return Toolbox(db)


def call(name, **args):
    return {"content": "", "tool_calls": [{"id": "c1", "type": "function",
                                           "function": {"name": name, "arguments": json.dumps(args)}}]}


def test_baseball_averages_are_checked_and_usage_words_are_not_heights():
    assert problems("He hits .412 against sliders.", RULE_FACTS)
    for fine in ("He throws his slider at a low rate.", "His curveball usage is high with two strikes.",
                 "Under the 2025 ABS rules it's a ball."):
        assert problems(fine, RULE_FACTS) == [], fine


def test_a_rejected_answer_on_the_last_round_is_never_returned(tools):
    queue = [call("find_players", query="LG Twins")] * (MAX_ROUNDS - 1) + [{"content": "He throws 162 km/h."}]
    out = Coach(tools, lambda m, s: queue.pop(0)).ask("How hard does he throw?")
    assert "162" not in out["answer"] and "couldn't finish" in out["answer"]


def test_bad_argument_types_are_tool_errors_not_crashes(tools):
    assert "error" in tools.call("find_players", {"query": 5})
    assert "error" in tools.call("pitcher_profile", {"pitcher": ["x"]})
    assert "error" in tools.call("predict_next_pitch", {"pitcher": "x", "balls": 0, "strikes": 0, "batter_side": "right"})


def test_a_failed_turn_leaves_the_conversation_as_it_was(tools):
    convo = Conversation()
    before = list(convo.messages)

    def broken(messages, schema):
        if len(messages) > 2:
            raise KeyError("choices")
        return call("find_players", query="LG Twins")
    with pytest.raises(KeyError):
        Coach(tools, broken).ask("q1", convo)
    assert convo.messages == before and convo.evidence == ""


def test_trimming_never_keeps_more_than_the_limit():
    convo = Conversation()
    convo.messages += [{"role": "user", "content": str(i)} for i in range(60)]
    convo.question_at = len(convo.messages)
    convo.messages += [{"role": "user", "content": "q"}] + [{"role": "assistant", "content": "", "tool_calls": []}] * 45
    assert len(convo.trimmed()) <= 41


def test_recommend_visual_without_a_worst_option():
    r = {"count": "1-2", "batter_value_now_runs": 0.2, "worst": [],
         "best": [{"pitch": "slider, knee-high, toward a corner", "called_strike_chance_if_taken": 0.7,
                   "whiff_chance_if_swung_at": 0.2, "batter_value_after_runs": 0.15}]}
    blocks = visuals.recommend(r)
    assert blocks and not any(b["type"] == "note" for b in blocks)


def test_an_interrupted_first_build_is_rebuilt(tmp_path):
    path = tmp_path / "abs.db"
    open_store(path).close()                      # the file exists but holds no pitches
    assert build_store(path, games=2).execute("SELECT COUNT(*) FROM pitches").fetchone()[0] > 0


def test_real_style_game_ids_work():
    rows = [{**r, "game_id": 2026040100 + r["game_id"]} for r in season(2)]
    db = open_store()
    ingest(db, rows)
    client = TestClient(create_app(db, chat=lambda m, s: {"content": ""}))
    meta = client.get("/api/meta").json()
    assert meta["games"] == 2 and meta["game_ids"] == [2026040100, 2026040101]
    assert client.get("/api/live/2026040101").status_code == 200


def test_contradictory_events_are_refused():
    assert "contradicts" in validate(event(swing=False, result="home_run"))
    assert "contradicts" in validate(event(swing=True, result="ball"))
    assert "can't follow" in validate(event(result="ball", pa_result="strikeout"))
    assert validate(event(result="ball", pa_result="walk", balls=3)) is None


def test_an_unreadable_model_reply_is_a_503_and_the_conversation_still_works():
    db = open_store()
    ingest(db, season(2))
    replies = iter([KeyError("choices"), {"content": "Fine."}])

    def chat(messages, schema):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply
    client = TestClient(create_app(db, chat=chat))
    first = client.post("/api/coach", json={"question": "q1"})
    assert first.status_code == 503
    assert client.post("/api/coach", json={"question": "q2"}).json()["answer"] == "Fine."


def test_inside_and_outside_are_not_tool_locations():
    assert problems("Lay off a sinker that's high and outside.", RULE_FACTS)
    assert problems("Pound him inside.", RULE_FACTS)
    assert problems("It was called a ball, just outside the zone.", RULE_FACTS) == []
