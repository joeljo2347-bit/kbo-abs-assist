"""Regressions for bugs found in an independent review."""

import json

import pytest
from fastapi.testclient import TestClient

from abs_assist import visuals
from abs_assist.api import build_store, create_app
from abs_assist.coach import Coach, Conversation
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
    replies = iter([KeyError("choices"), call("find_players", query="LG Twins"), {"content": "Fine."}])

    def chat(messages, schema):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply
    client = TestClient(create_app(db, chat=chat))
    first = client.post("/api/coach", json={"question": "q1"})
    assert first.status_code == 503
    assert client.post("/api/coach", json={"question": "q2"}).json()["answer"].startswith("Found pitchers")


def test_league_tools(tools):
    board = tools.call("leaderboard", {"metric": "whiff_rate", "who": "batters", "team": "KT Wiz"})
    values = [r["whiff_rate"] for r in board["ranking"]]
    assert values == sorted(values, reverse=True) and board["league_average"] is not None
    teams = tools.call("leaderboard", {"metric": "in_zone_rate", "who": "team_pitching"})
    assert teams["qualified"] == 10
    pitcher = tools.find_players("LG Twins")["pitchers"][0]
    profile = tools.call("pitcher_profile", {"pitcher": pitcher})
    assert profile["fastball_kmh"] and "verdict" in profile["compared_with_league"]["whiff_rate"]
    arsenal = tools.call("pitcher_arsenal", {"pitcher": pitcher})
    assert arsenal["arsenal"][0]["usage"] >= arsenal["arsenal"][-1]["usage"] and arsenal["next_pitch_after"]
    assert "curveball" in tools.call("abs_rules", {})["low_pitches"]
    assert "error" in tools.call("leaderboard", {"metric": "era", "who": "pitchers"})


def test_new_visuals(tools):
    from abs_assist.visuals import for_calls
    for name, args in (("leaderboard", {"metric": "batting_average"}), ("abs_rules", {}),
                       ("pitcher_arsenal", {"pitcher": tools.find_players("LG Twins")["pitchers"][0]})):
        assert for_calls([{"tool": name, "args": args, "result": json.dumps(tools.call(name, args))}]), name


def test_computed_verdicts(tools):
    from abs_assist.tools import zone_change
    assert zone_change(2024, 2025)["summary"].startswith("the same share of the batter's height") and "lower" in zone_change(2024, 2025)["summary"]
    batter = tools.find_players("KT Wiz")["batters"][0]
    chase = tools.call("batter_profile", {"batter": batter})["compared_with_league"]["chase_rate"]
    lower, higher, n = chase["others_lower"], chase["others_higher"], chase["others"]
    assert lower + higher <= n
    expected = "higher than most" if lower > 0.6 * n else "lower than most" if higher > 0.6 * n else "in the middle"
    assert chase["verdict"].startswith(expected)
    guide = tools.call("take_guide", {"batter": batter, "balls": 3, "strikes": 1})
    assert guide["summary"].startswith("Verdict: ")


def test_empty_player_names_are_refused(tools):
    assert "can't be empty" in tools.call("attack_plan", {"batter": "", "balls": 0, "strikes": 2})["error"]


def test_every_main_tool_has_a_written_answer_and_a_visual(tools):
    from abs_assist.visuals import for_calls
    pitcher, batter = tools.find_players("LG Twins")["pitchers"][0], tools.find_players("KT Wiz")["batters"][0]
    for name, args in (("recommend_pitch", {"pitcher": pitcher, "batter": batter, "balls": 1, "strikes": 2}),
                       ("attack_plan", {"batter": batter, "balls": 0, "strikes": 2}),
                       ("take_guide", {"batter": batter, "balls": 0, "strikes": 0}),
                       ("leaderboard", {"metric": "chase_rate", "who": "team_batting"}),
                       ("pitcher_arsenal", {"pitcher": pitcher}),
                       ("predict_next_pitch", {"pitcher": pitcher, "balls": 0, "strikes": 0}),
                       ("strikes_lost_at_back", {"team": "LG Twins"})):
        from abs_assist.compose import NOTHING, answer
        result = tools.call(name, args)
        written = answer("", [{"tool": name, "args": args, "result": json.dumps(result)}], tools)
        assert written and written != NOTHING, name
        assert for_calls([{"tool": name, "args": args, "result": json.dumps(result)}]), name


def test_step_limit_returns_what_was_looked_up(tools):
    pitchers = tools.find_players("LG Twins")["pitchers"][:2]
    queue = [call("pitcher_profile", pitcher=p) for p in pitchers] * 5
    out = Coach(tools, lambda m, s: queue.pop(0)).ask(f"Who misses more bats, {pitchers[0]} or {pitchers[1]}?")
    assert pitchers[0] in out["answer"] and pitchers[1] in out["answer"]
