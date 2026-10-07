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
    replies = iter([KeyError("choices"), call("find_players", query="LG Twins"), {"content": "Fine."}])

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


def test_a_runs_value_is_not_a_percentage():
    from abs_assist.coach import unsourced
    evidence = json.dumps({"gain_from_taking_runs": 0.409, "chase_rate": 0.23})
    assert unsourced("Taking gains him 41% more runs.", evidence) == [0.41]
    assert unsourced("Taking gains him 0.409 runs; he chases 23%.", evidence) == []


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


def test_an_answer_with_nothing_looked_up_is_sent_back_once(tools):
    queue = [{"content": "Which batter?"}, call("find_players", query="LG Twins"), {"content": "Here they are."}]
    out = Coach(tools, lambda m, s: queue.pop(0)).ask("Who pitches for the LG Twins?")
    assert out["answer"] == "Here they are." and out["corrected"]


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


def test_count_and_runs_checks():
    from abs_assist.coach import count_problems, unlabeled_runs
    calls = [{"tool": "recommend_pitch", "args": {"balls": 0, "strikes": 2}}]
    assert count_problems("What should he throw on 2-0?", calls)
    assert not count_problems("What should he throw on 0-2?", calls)
    assert not count_problems("Full count: what now?", [{"tool": "x", "args": {"balls": 3, "strikes": 2}}])
    evidence = json.dumps({"batter_value_after_runs": 0.223, "chase_rate": 0.31})
    assert unlabeled_runs("The slider is best at 0.223.", evidence)
    assert not unlabeled_runs("The slider leaves him 0.223 expected runs.", evidence)


def test_empty_player_names_are_refused(tools):
    assert "can't be empty" in tools.call("attack_plan", {"batter": "", "balls": 0, "strikes": 2})["error"]


def test_team_questions_need_team_rankings():
    from abs_assist.coach import scope_problems
    players = [{"tool": "leaderboard", "args": {"metric": "chase_rate", "who": "batters"}}]
    teams = [{"tool": "leaderboard", "args": {"metric": "chase_rate", "who": "team_batting"}}]
    assert scope_problems("Which team's hitters chase the most?", players)
    assert not scope_problems("Which team's hitters chase the most?", teams)
    assert not scope_problems("Which KT Wiz hitter whiffs the most?", players)


def test_take_advice_must_match_the_verdict():
    from abs_assist.coach import stance_problems
    guide = [{"tool": "take_guide", "result": json.dumps({"summary": "Verdict: swing at strikes, take balls. In the zone ..."})}]
    assert stance_problems("He should take the first pitch.", guide)
    assert not stance_problems("Swing at strikes and take the balls: he chases too much.", guide)
    assert not stance_problems("Anything.", [])


def test_a_twice_contradicted_verdict_is_stated_by_code(tools):
    batter = tools.find_players("KT Wiz")["batters"][0]
    queue = [call("take_guide", batter=batter, balls=0, strikes=0), {"content": "He should take the first pitch."},
             {"content": "He should take the first pitch, really."}]
    out = Coach(tools, lambda m, s: queue.pop(0)).ask(f"Should {batter} swing at the first pitch?")
    verdict = tools.call("take_guide", {"batter": batter, "balls": 0, "strikes": 0})["summary"]
    expected = verdict.removeprefix("Verdict: ").split(". ")[0]
    assert out["answer"].startswith("On 0-0: " + expected) or "take the first pitch" not in out["answer"]


def test_markdown_does_not_hide_contradictions():
    from abs_assist.coach import stance_problems
    guide = [{"tool": "take_guide", "result": json.dumps({"summary": "Verdict: swing at strikes, take balls. x"})}]
    assert stance_problems("He should **take** the first pitch; verdict: swing at strikes.", guide)


def test_every_main_tool_writes_its_own_answer(tools):
    from abs_assist.visuals import for_calls
    pitcher, batter = tools.find_players("LG Twins")["pitchers"][0], tools.find_players("KT Wiz")["batters"][0]
    for name, args in (("recommend_pitch", {"pitcher": pitcher, "batter": batter, "balls": 1, "strikes": 2}),
                       ("attack_plan", {"batter": batter, "balls": 0, "strikes": 2}),
                       ("take_guide", {"batter": batter, "balls": 0, "strikes": 0}),
                       ("leaderboard", {"metric": "chase_rate", "who": "team_batting"}),
                       ("pitcher_arsenal", {"pitcher": pitcher}),
                       ("predict_next_pitch", {"pitcher": pitcher, "balls": 0, "strikes": 0}),
                       ("strikes_lost_at_back", {"team": "LG Twins"})):
        result = tools.call(name, args)
        assert result.get("answer"), name
        assert for_calls([{"tool": name, "args": args, "result": json.dumps(result)}]), name


def test_a_rewrite_that_still_fails_is_replaced_by_the_code_answer(tools):
    pitcher, batter = tools.find_players("LG Twins")["pitchers"][0], tools.find_players("KT Wiz")["batters"][0]
    args = {"pitcher": pitcher, "batter": batter, "balls": 1, "strikes": 2}
    queue = [call("recommend_pitch", **args), {"content": "Throw him a 97% heater."}, {"content": "Still a 97% heater."}]
    out = Coach(tools, lambda m, s: queue.pop(0)).ask(f"What should {pitcher} throw {batter} on 1-2?")
    assert out["answer"] == tools.call("recommend_pitch", args)["answer"]


def test_the_recommended_pitch_keeps_its_location():
    from abs_assist.coach import location_problems
    calls = [{"tool": "recommend_pitch", "result": json.dumps({"best": [{"pitch": "sinker, letter-high, on the edge"}]})}]
    assert location_problems("Throw the sinker on the edge.", calls)
    assert not location_problems("Throw the sinker letter‑high, on the edge.", calls)


def test_yes_no_routing_and_team_scope_checks(tools):
    from abs_assist.coach import routing_problems, scope_problems, yes_no_problems
    guide = [{"tool": "take_guide", "result": "{}"}]
    assert yes_no_problems("**Yes** - he should be patient.", guide)
    assert not yes_no_problems("Swing at strikes, take balls.", guide)
    pitcher = tools.find_players("LG Twins")["pitchers"][0]
    plan = [{"tool": "attack_plan", "args": {}}]
    assert routing_problems(f"{pitcher} vs Kim, 1-1. Plan?", plan, tools.pitchers())
    assert not routing_problems("How do we pitch Kim on 1-1?", plan, tools.pitchers())
    one_team = [{"tool": "strikes_lost_at_back", "args": {"team": "LG Twins"}}]
    assert scope_problems("Which team's pitchers lose the most strikes at the back?", one_team)


def test_step_limit_returns_what_was_looked_up(tools):
    pitchers = tools.find_players("LG Twins")["pitchers"][:2]
    queue = [call("pitcher_profile", pitcher=p) for p in pitchers] * 5
    out = Coach(tools, lambda m, s: queue.pop(0)).ask(f"Who misses more bats, {pitchers[0]} or {pitchers[1]}?")
    assert pitchers[0] in out["answer"] and pitchers[1] in out["answer"]
