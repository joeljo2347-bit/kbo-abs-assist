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
    report = ingest(open_store(), [event(), event(batter_height_cm=90)])
    assert report.accepted == 1 and report.rejected == [(1, "batter_height_cm=90 is outside 150..215")]


def test_duplicates_are_ignored_and_feed_disagreements_flagged():
    db = open_store()
    report = ingest(db, [event(reported_strike=True), event(reported_strike=True)])
    assert report.accepted == 1 and report.duplicates == 1 and report.disagreements == 1
    rule = db.execute("SELECT abs_rule, abs_strike FROM pitches").fetchone()
    assert rule == ("bottom, back of plate", 0)  # in at the middle, out at the back: a ball
