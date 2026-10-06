"""Analysis and strategy on a small simulated sample (fast, deterministic)."""

import pytest

from abs_assist.analyze import batter_profile, called_strike_map, cell, lost_at_back, pitcher_profile, rows
from abs_assist.collect import ingest, open_store
from abs_assist.sim import season
from abs_assist.strategy import Rates, Strategy, count_values, evaluate


@pytest.fixture(scope="module")
def db():
    store = open_store()
    ingest(store, season(40))
    return store


def test_cells_are_relative_to_the_batters_zone():
    tall = {"x_cm": 0.0, "z_mid_cm": 100.0, "batter_height_cm": 195.0}
    short = {"x_cm": 0.0, "z_mid_cm": 100.0, "batter_height_cm": 170.0}
    assert cell(tall) == ("high", "heart") and cell(short) == ("above", "heart")
    assert cell({"x_cm": 30.0, "z_mid_cm": 75.0, "batter_height_cm": 180.0}) == ("middle", "off")


def test_called_strike_map_is_consistent_with_the_rules(db):
    m = called_strike_map(rows(db))
    assert m["middle/heart"]["called_strike_rate"] == 1.0
    assert m["below/heart"]["called_strike_rate"] == 0.0  # below the zone at the middle: never a strike
    assert all(v["taken"] > 0 for v in m.values())


def test_strikes_lost_at_the_back_are_real_two_plane_cases(db):
    lost = lost_at_back(rows(db))
    assert lost["strikes_lost"] > 0
    assert sum(lost["by_pitch_type"].values()) == lost["strikes_lost"]


def test_profiles(db):
    name = rows(db)[0]["pitcher"]
    prof = pitcher_profile(db, name)
    assert prof["pitches"] > 0 and abs(sum(prof["pitch_mix"].values()) - 1) < 0.01
    batter = rows(db)[0]["batter"]
    assert batter_profile(db, batter)["zone_cm"][0] < batter_profile(db, batter)["zone_cm"][1]
    assert "error" in pitcher_profile(db, "Nobody")


def test_count_values_rise_with_balls_and_fall_with_strikes(db):
    v = count_values(rows(db))
    assert v[(3, 0)] > v[(0, 0)] > v[(0, 2)] and v[(0, 3)] == 0.0 and v[(4, 0)] == 0.69


def test_evaluate_uses_the_count():
    values = {(b, s): 0.3 + 0.1 * b - 0.1 * s for b in range(5) for s in range(4)}
    always_strike = Rates(n=100, takes=100, called_strikes=100)
    ev = evaluate(always_strike, 1, 1, values, (1.0, 1.0))
    assert ev["take"] == pytest.approx(values[(1, 2)]) and ev["p_swing"] == 0.0


def test_recommendations_come_from_his_arsenal(db):
    st = Strategy(db)
    name, batter = rows(db)[0]["pitcher"], rows(db)[0]["batter"]
    arsenal = {k for (k,) in db.execute("SELECT DISTINCT pitch_type FROM pitches WHERE pitcher=?", (name,))}
    rec = st.recommend(name, batter, 1, 2)
    assert rec["best"] and all(r["pitch"].split(",")[0] in arsenal for r in rec["best"])
    assert rec["best"][0]["batter_value_after_runs"] <= rec["worst"][-1]["batter_value_after_runs"]
