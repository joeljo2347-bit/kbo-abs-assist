"""The coach loop and its number check, with a scripted model (no model server needed)."""

import json

import pytest

from abs_assist.coach import Coach
from abs_assist.collect import ingest, open_store
from abs_assist.sim import season
from abs_assist.tools import Toolbox


@pytest.fixture(scope="module")
def tools():
    db = open_store()
    ingest(db, season(30))
    return Toolbox(db)


def scripted(*replies):
    queue = list(replies)

    def chat(messages, schema):
        return queue.pop(0)
    return chat


def tool_call(name, **args):
    return {"content": "", "tool_calls": [{"id": "c1", "type": "function",
                                           "function": {"name": name, "arguments": json.dumps(args)}}]}


def test_answers_from_a_tool(tools):
    pitcher = tools.db.execute("SELECT pitcher FROM pitches LIMIT 1").fetchone()[0]
    rate = tools.call("pitcher_profile", {"pitcher": pitcher})["whiff_rate"]
    coach = Coach(tools, scripted(tool_call("pitcher_profile", pitcher=pitcher),
                                  {"content": f"His whiff rate is {rate * 100:.1f}%."}))
    out = coach.ask(f"How often does {pitcher} get whiffs?")
    assert out["tools_used"] == ["pitcher_profile"] and not out["corrected"]


def test_tool_errors_are_reported_not_raised(tools):
    assert "error" in tools.call("recommend_pitch", {"pitcher": "X"})
    assert tools.call("nope", {}) == {"error": "Unknown tool nope."}


def test_names_resolve_however_they_are_typed(tools):
    name = tools.db.execute("SELECT pitcher FROM pitches LIMIT 1").fetchone()[0]
    typed = name.replace(" ", " ").replace("-", "‑").upper()
    assert tools.resolve(typed, "pitcher") == name
    assert "error" not in tools.call("pitcher_profile", {"pitcher": typed})
    assert tools.call("pitcher_profile", {"pitcher": "Nobody Here"})["error"].startswith("No pitcher named")


def test_a_follow_up_can_use_numbers_from_an_earlier_turn(tools):
    from abs_assist.coach import Conversation
    pitcher = tools.db.execute("SELECT pitcher FROM pitches LIMIT 1").fetchone()[0]
    rate = tools.call("pitcher_profile", {"pitcher": pitcher})["whiff_rate"]
    coach, convo = Coach(tools, scripted(tool_call("pitcher_profile", pitcher=pitcher),
                                         {"content": f"{pitcher} is a strike thrower."},
                                         {"content": f"Because his whiff rate is {rate * 100:.1f}%."})), Conversation()
    coach.ask(f"Tell me about {pitcher}.", convo)
    out = coach.ask("Why do you say that?", convo)
    assert not out["corrected"] and out["tools_used"] == []
    assert [m["role"] for m in convo.messages].count("user") == 2


def test_trimming_keeps_tool_results_with_their_calls():
    from abs_assist.coach import KEEP_MESSAGES, Conversation
    convo = Conversation()
    for i in range(30):
        convo.messages += [{"role": "user", "content": f"q{i}"},
                           {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}"}]},
                           {"role": "tool", "tool_call_id": f"c{i}", "content": "{}"},
                           {"role": "assistant", "content": "a"}]
    kept = convo.trimmed()
    assert kept[0]["role"] == "system" and kept[1]["role"] == "user" and len(kept) <= KEEP_MESSAGES + 1


def test_tool_arguments_are_checked(tools):
    pitcher = tools.db.execute("SELECT pitcher FROM pitches LIMIT 1").fetchone()[0]
    assert tools.call("predict_next_pitch", {"pitcher": pitcher, "balls": -7, "strikes": 9})["error"].startswith("balls")
    assert tools.call("strikes_lost_at_back", {"team": "LG Tigers"})["error"] == "No team named 'LG Tigers'."
    assert tools.call("strikes_lost_at_back", {"team": "lg twins"})["taken_pitches"] > 0
    everyone = tools.call("strikes_lost_at_back", {"team": "all"})["taken_pitches"]
    assert everyone > tools.call("strikes_lost_at_back", {"team": "LG Twins"})["taken_pitches"]
    assert tools.find_players("%") == {"pitchers": [], "batters": []}  # % is literal, not "everything"


def test_a_long_turn_never_loses_the_question(tools):
    from abs_assist.coach import Conversation
    seen = []

    def chat(messages, schema):
        seen.append(messages)
        if len(seen) < 8:
            calls = [{"id": f"c{len(seen)}{i}", "type": "function",
                      "function": {"name": "find_players", "arguments": '{"query": "LG"}'}} for i in range(6)]
            return {"content": "", "tool_calls": calls}
        return {"content": "Done."}

    Coach(tools, chat).ask("Who are the LG Twins pitchers?", Conversation())
    last = seen[-1]
    assert last[1] == {"role": "user", "content": "Who are the LG Twins pitchers?"}
    ids = {c["id"] for m in last if m.get("tool_calls") for c in m["tool_calls"]}
    assert all(m["tool_call_id"] in ids for m in last if m["role"] == "tool")  # no orphaned results


def test_bad_tool_arguments_become_a_recoverable_error(tools):
    from abs_assist.coach import Conversation
    broken = {"content": "", "tool_calls": [{"type": "function", "function": {"name": "find_players", "arguments": "{oops"}}]}
    convo = Conversation()
    out = Coach(tools, scripted(broken, {"content": "Sorry, try again."})).ask("Who?", convo)
    from abs_assist.compose import NOTHING
    assert out["answer"] == NOTHING
    tool_msg = next(m for m in convo.messages if m["role"] == "tool")
    assert "not valid JSON" in tool_msg["content"] and tool_msg["tool_call_id"]


def test_a_pitcher_sent_to_a_batter_tool_is_pointed_to_the_pitcher_tools(tools):
    pitcher = tools.find_players("LG Twins")["pitchers"][0]
    out = tools.call("batter_profile", {"batter": pitcher})
    assert "is a pitcher" in out["note"] and out["pitcher"] == pitcher and "in_zone_rate" in out
    assert "games" in tools.call("strikes_lost_at_back", {"team": "LG Twins"})
