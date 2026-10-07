"""Visual blocks come from tool results only, and say plainly what they show."""

import json

from abs_assist import visuals


def call(tool, result):
    return {"tool": tool, "args": {}, "result": json.dumps(result)}


REC = {"count": "1-2", "batter_value_now_runs": 0.197,
       "best": [{"pitch": "slider, belt-high, on the edge", "batter_value_after_runs": 0.141,
                 "whiff_chance_if_swung_at": 0.21, "called_strike_chance_if_taken": 1.0}],
       "worst": [{"pitch": "curveball, letter-high, over the middle", "batter_value_after_runs": 0.25}]}


def test_recommendation_reads_as_headline_zone_tiles_and_an_avoid_note():
    head, zone, tiles, note = visuals.for_calls([call("recommend_pitch", REC)])
    assert head["text"] == "slider, belt-high, on the edge"
    assert zone == {"type": "zone", "height": "middle", "side": "edge", "label": "slider"}
    assert [t["value"] for t in tiles["items"]] == ["100%", "21%", "14"] and tiles["items"][2]["note"] == "down from 20 now"
    assert note["text"] == "Avoid: curveball, letter-high, over the middle (25 runs per 100)."


def test_prediction_bars_are_sorted_with_the_top_one_highlighted():
    (bars,) = visuals.for_calls([call("predict_next_pitch", {"probabilities": {"slider": 0.2, "fastball": 0.7, "curveball": 0.1}})])
    assert [i["label"] for i in bars["items"]] == ["fastball", "slider", "curveball"]
    assert [i["highlight"] for i in bars["items"]] == [True, False, False] and bars["items"][0]["display"] == "70%"


def test_several_players_become_one_table_with_leaders_in_bold():
    a = {"batter": "Kim", "zone_swing_rate": 0.65, "chase_rate": 0.17, "whiff_rate": 0.15}
    b = {**a, "batter": "Lee", "chase_rate": 0.20}
    (table,) = visuals.for_calls([call("batter_profile", a), call("batter_profile", b), call("find_players", {})])
    assert table["rows"] == [["Kim", "65%", "17%", "15%"], ["Lee", "65%", "20%", "15%"]]
    assert table["bold"] == [[2, 1]]  # Lee leads chase; the other columns are ties, so no bold
    assert visuals.pct(0.725) == "73%"


def test_errors_and_lookups_alone_show_nothing():
    assert visuals.for_calls([call("pitcher_profile", {"error": "x"}), call("find_players", {"pitchers": []})]) == []


def test_empty_or_odd_results_show_nothing_instead_of_failing():
    empty = {"count": "0-0", "batter_value_now_runs": 0.3, "best": [], "worst": []}
    assert visuals.for_calls([call("recommend_pitch", empty)]) == []
    assert visuals.for_calls([call("pitcher_profile", {"pitcher": "Kim"})]) == []  # missing fields
