"""The coach loop and its number check, with a scripted model (no model server needed)."""

import json

import pytest

from abs_assist.coach import Coach, unsourced
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


def test_number_check():
    evidence = json.dumps({"whiff_rate": 0.312, "pitches": 412, "count": "1-2"})
    assert unsourced("Whiffs 31% of the time on 412 pitches in a 1-2 count.", evidence) == []
    assert unsourced("Whiffs 45% of the time.", evidence) == [0.45]
    assert unsourced("Throw it 2 times.", evidence) == []
    big = json.dumps({"taken_pitches": 10916})
    assert unsourced("Of 10,916 taken pitches...", big) == []
    assert unsourced("Of 10,961 taken pitches...", big) == [10961.0]


def test_answers_from_a_tool(tools):
    pitcher = tools.db.execute("SELECT pitcher FROM pitches LIMIT 1").fetchone()[0]
    rate = tools.call("pitcher_profile", {"pitcher": pitcher})["whiff_rate"]
    coach = Coach(tools, scripted(tool_call("pitcher_profile", pitcher=pitcher),
                                  {"content": f"His whiff rate is {rate * 100:.1f}%."}))
    out = coach.ask(f"How often does {pitcher} get whiffs?")
    assert out["tools_used"] == ["pitcher_profile"] and not out["corrected"]


def test_invented_numbers_are_sent_back_once(tools):
    coach = Coach(tools, scripted({"content": "He whiffs 47% of the time."},
                                  tool_call("find_players", query="LG Twins"),
                                  {"content": "I need an exact name; here are LG Twins players."}))
    out = coach.ask("How often does their ace get whiffs?")
    assert out["corrected"] and out["tools_used"] == ["find_players"]


def test_tool_errors_are_reported_not_raised(tools):
    assert "error" in tools.call("recommend_pitch", {"pitcher": "X"})
    assert tools.call("nope", {}) == {"error": "Unknown tool nope."}


def test_names_resolve_however_they_are_typed(tools):
    name = tools.db.execute("SELECT pitcher FROM pitches LIMIT 1").fetchone()[0]
    typed = name.replace(" ", " ").replace("-", "‑").upper()
    assert tools.resolve(typed, "pitcher") == name
    assert "error" not in tools.call("pitcher_profile", {"pitcher": typed})
    assert tools.call("pitcher_profile", {"pitcher": "Nobody Here"})["error"].startswith("No pitcher named")
