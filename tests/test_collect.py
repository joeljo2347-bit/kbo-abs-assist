import pytest

from abs_assist.collect import ingest, open_store, validate
from abs_assist.sim import season


def event(**overrides):
    base = {"game_id": 1, "inning": 1, "half": "top", "pitcher": "P", "pitcher_team": "LG Twins", "batter": "B",
            "batter_team": "KT Wiz", "batter_height_cm": 180.0, "balls": 0, "strikes": 0, "pitch_type": "slider",
            "kmh": 133.0, "x_cm": 0.0, "z_mid_cm": 49.5, "z_end_cm": 48.0, "swing": False, "result": "ball"}
    return {**base, **overrides}


def test_a_simulated_game_is_stored_with_calls():
    db = open_store()
    rows = [r for r in season(2)]
    report = ingest(db, rows)
    assert report.accepted == len(rows) and not report.rejected and report.disagreements == 0
    stored = db.execute("SELECT COUNT(*), SUM(abs_strike) FROM pitches").fetchone()
    assert stored[0] == len(rows) and stored[1] == sum(r["abs_strike"] for r in rows)


def test_bad_events_are_refused_with_a_reason():
    assert validate(event(balls=4)) == "balls=4 is outside 0..3"
    assert validate({k: v for k, v in event().items() if k != "x_cm"}) == "missing x_cm"
    assert validate(event(kmh="fast")) == "kmh must be a number"
    assert validate(event(batter_side="X")) == "batter_side must be R or L"
    assert validate(event(hb_cm=120.0)) == "hb_cm=120.0 is outside -90..90"
    assert validate(event(hb_cm=-12.5, ivb_cm=40.0, pitcher_throws="L", batter_side="R")) is None
    report = ingest(open_store(), [event(), event(batter_height_cm=90)])
    assert report.accepted == 1 and report.rejected == [(1, "batter_height_cm=90 is outside 150..215")]


def test_duplicates_are_ignored_and_feed_disagreements_flagged():
    db = open_store()
    report = ingest(db, [event(reported_strike=True), event(reported_strike=True)])
    assert report.accepted == 1 and report.duplicates == 1 and report.disagreements == 1
    rule = db.execute("SELECT abs_rule, abs_strike FROM pitches").fetchone()
    assert rule == ("bottom, back of plate", 0)  # in at the middle, out at the back: a ball


@pytest.mark.parametrize("bad,reason", [
    ({"pa_result": "hit_by_pitch"}, "pa_result='hit_by_pitch' is not one of"),
    ({"result": "balk"}, "result='balk' is not one of"),
    ({"swing": "false"}, "swing must be true or false"),
    ({"hb_cm": "abc"}, "hb_cm must be a number"),
    ({"half": "middle"}, "half='middle' is not one of"),
    ({"pitcher": 7}, "pitcher must be text"),
])
def test_malformed_events_are_refused_not_crashing(bad, reason):
    assert validate(event(**bad)).startswith(reason)
    report = ingest(open_store(), [event(**bad), event()])
    assert report.accepted == 1 and report.rejected[0][0] == 0
