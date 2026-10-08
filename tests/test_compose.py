"""The coach's answers, written in code from tool results: each kind of question gets the answer it asks for."""

import json

import pytest

from abs_assist.collect import ingest, open_store
from abs_assist.compose import NOTHING, answer
from abs_assist.sim import season
from abs_assist.tools import Toolbox


@pytest.fixture(scope="module")
def tools():
    db = open_store()
    ingest(db, season(60))
    return Toolbox(db)


def ran(tools, name, **args):
    return {"tool": name, "args": args, "result": json.dumps(tools.call(name, args))}


def test_a_yes_no_question_is_answered_from_where_he_ranks(tools):
    batter = tools.find_players("KT Wiz")["batters"][0]
    call = ran(tools, "batter_profile", batter=batter)
    v = json.loads(call["result"])["compared_with_league"]["home_runs"]
    expected = "Yes." if v["others_lower"] > 0.6 * v["others"] else "No." if v["others_higher"] > 0.6 * v["others"] else "About average."
    text = answer(f"Is {batter} a power hitter?", [call], tools)
    assert text.startswith(expected) and "home runs" in text


def test_a_comparison_names_who_is_lower_when_asked_less(tools):
    a, b = tools.find_players("KT Wiz")["batters"][:2]
    calls = [ran(tools, "batter_profile", batter=a), ran(tools, "batter_profile", batter=b)]
    chase = {n: json.loads(c["result"])["chase_rate"] for n, c in zip((a, b), calls)}
    text = answer(f"Who chases less, {a} or {b}?", calls, tools)
    assert text.startswith(f"{min(chase, key=chase.get)} is lower on chase rate")


def test_pitch_mix_splits_and_what_follows_a_pitch(tools):
    pitcher = tools.find_players("LG Twins")["pitchers"][0]
    call = ran(tools, "pitcher_arsenal", pitcher=pitcher)
    both = answer(f"What does {pitcher} throw to left-handed hitters with two strikes?", [call], tools)
    assert "against left-handed batters" in both and "with two strikes" in both and "no split that combines" in both
    after = next(iter(json.loads(call["result"])["next_pitch_after"]))
    assert answer(f"What does {pitcher} throw after a {after}?", [call], tools).startswith(f"After a {after}")


def test_stats_the_data_does_not_have_are_said_plainly(tools):
    assert answer("What's Kim Ji-hoon's ERA?", [], tools).startswith("ERA: not available")
    assert answer("Who has the most RBIs?", [], tools).startswith("RBIs: not available")


def test_team_questions_use_team_rankings_and_counts_are_matched(tools):
    teams = ran(tools, "leaderboard", metric="walk_rate", who="team_pitching", order="lowest")
    players = ran(tools, "leaderboard", metric="walk_rate", who="pitchers")
    assert "among teams" in answer("Which team's pitchers walk the fewest batters?", [players, teams], tools)
    batter = tools.find_players("KT Wiz")["batters"][0]
    wrong, right = ran(tools, "attack_plan", batter=batter, balls=0, strikes=2), ran(tools, "attack_plan", batter=batter, balls=2, strikes=0)
    assert answer(f"How do we pitch {batter} on 2-0?", [wrong, right], tools).startswith("On 2-0")


def test_rules_answer_the_height_or_the_change_asked_about(tools):
    assert "175 cm batter" in answer("Zone for a 175 cm hitter?", [ran(tools, "abs_rules", batter_height_cm=175)], tools)
    change = answer("How did the zone change from 2024 to 2025?", [ran(tools, "abs_rules")], tools)
    assert "same share of the batter's height" in change


def test_follow_ups_and_skipped_lookups(tools):
    pitcher = tools.find_players("LG Twins")["pitchers"][0]
    first = [ran(tools, "pitcher_profile", pitcher=pitcher)]
    assert answer("Why?", [], tools, previous=first) == answer("Why?", first, tools)
    looked_up = answer(f"Tell me about {pitcher}.", [], tools)
    assert looked_up.startswith(pitcher) and looked_up != NOTHING


def test_skipped_lookups_are_filled_in(tools):
    pitcher, batter = tools.find_players("LG Twins")["pitchers"][0], tools.find_players("KT Wiz")["batters"][0]
    profiles = [ran(tools, "batter_profile", batter=batter), ran(tools, "pitcher_profile", pitcher=pitcher)]
    plan = answer(f"{batter} is up with a 3-1 count against {pitcher}. What should we throw?", profiles, tools)
    assert plan.startswith("On 3-1, throw") and f"for {pitcher} against {batter}" in plan
    two = answer(f"Should {batter} protect the plate more with two strikes?", [], tools)
    assert all(f"On {b}-2:" in two for b in range(4))
    heights = answer("How much does the zone's top move between a 170 cm and a 190 cm hitter?", [], tools)
    assert "From 170 cm to 190 cm, the top moves up" in heights
    walks = answer("Who leads the league in walks drawn?", [ran(tools, "leaderboard", metric="walk_rate")], tools)
    assert walks.startswith("Highest walks among players")


def test_pitch_questions_and_mixed_verdicts(tools):
    pitcher = tools.find_players("LG Twins")["pitchers"][0]
    profile = [ran(tools, "pitcher_profile", pitcher=pitcher)]
    assert answer(f"What's {pitcher}'s main pitch?", profile, tools).startswith(f"{pitcher}'s main pitch is the")
    assert "most swings and misses" in answer(f"What's {pitcher}'s best pitch for whiffs?", profile, tools)
    batter = tools.find_players("KT Wiz")["batters"][1]
    text = answer(f"Is {batter} a free swinger or a patient hitter?", [ran(tools, "batter_profile", batter=batter)], tools)
    assert text.split(":")[0] in ("A patient hitter", "A free swinger") or text.startswith("In between")


def test_rankings_fit_the_question(tools):
    players = ran(tools, "leaderboard", metric="chase_rate", who="batters")
    teams = answer("Which team's hitters chase the most?", [players], tools)
    assert teams.startswith("Highest chase rate among teams")
    fewest = answer("Which pitchers walk the fewest batters?", [ran(tools, "leaderboard", metric="walk_rate", who="pitchers")], tools)
    assert fewest.startswith("Lowest walk rate")


def test_hitter_traits_use_his_profile_and_zone_rate_is_not_the_rules(tools):
    batter, pitcher = tools.find_players("KT Wiz")["batters"][2], tools.find_players("LG Twins")["pitchers"][1]
    guide = [ran(tools, "take_guide", batter=batter, balls=0, strikes=0)]
    assert "chase rate" in answer(f"Is {batter} a patient hitter?", guide, tools)
    zone = answer(f"Is {pitcher}'s zone rate high or low?", [ran(tools, "pitcher_profile", pitcher=pitcher)], tools)
    assert "share of pitches in the zone" in zone and not zone.startswith(("Yes", "No"))
    assert answer("Is Lee Do-yun better than average at laying off pitches?", [], tools) is not None


def test_a_model_server_error_still_gets_an_answer(tools):
    import urllib.error

    from abs_assist.coach import Coach

    def broken(messages, schema):
        raise urllib.error.HTTPError("http://model", 500, "Internal Server Error", None, None)
    pitcher = tools.find_players("LG Twins")["pitchers"][0]
    out = Coach(tools, broken).ask(f"What will {pitcher} throw next after a fastball on a 1-1 count?")
    assert "most likely next" in out["answer"] and "after a fastball" in out["answer"]


def test_rounding_and_no_repeated_stats(tools):
    from abs_assist.compose import asked_stats, pct
    assert pct(0.0355) == "3.6%" and pct(0.25) == "25.0%" and pct(round(4107 / 115849, 5)) == "3.5%"
    stats = asked_stats("Is he hard to strike out, or does he strike out a lot?", "batter")
    assert [m for m, _ in stats].count("strikeout_rate") == 1


def test_what_a_hitter_actually_does_by_count(tools):
    batter = tools.find_players("KT Wiz")["batters"][0]
    profile = [ran(tools, "batter_profile", batter=batter)]
    two_oh = answer(f"In a 2-0 count, does {batter} swing or take?", profile, tools)
    assert f"{batter} on 2-0: swings at" in two_oh and "pitches seen" in two_oh
    early = answer(f"How aggressive is {batter} early in the count?", profile, tools)
    assert "on the first pitch" in early


def test_pitch_level_questions(tools):
    a, b = tools.find_players("LG Twins")["pitchers"][:2]
    arsenal = [ran(tools, "pitcher_arsenal", pitcher=a)]
    assert "misses the most bats" in answer(f"Velo on each pitch for {a}?", arsenal, tools)
    after = answer(f"After {a} throws a fastball, what usually comes next?", arsenal, tools)
    assert after.startswith("After a fastball")
    profiles = [ran(tools, "pitcher_profile", pitcher=p) for p in (a, b)]
    shared = {x["pitch"] for x in json.loads(profiles[0]["result"])["each_pitch"]} & \
             {x["pitch"] for x in json.loads(profiles[1]["result"])["each_pitch"]}
    pitch = sorted(shared)[0]
    assert "gets more swings and misses" in answer(f"{a} or {b}: whose {pitch} is better?", profiles, tools)


def test_ranking_follows_the_stat_asked(tools):
    wrong = ran(tools, "leaderboard", metric="zone_swing_rate", who="team_batting")
    assert "chase rate among teams" in answer("Which team swings at the most pitches out of the zone?", [wrong], tools)
