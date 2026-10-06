"""Scouting a pitcher against a team, with filters."""

import pytest

from abs_assist.collect import ingest, open_store
from abs_assist.scouting import Filters, scout
from abs_assist.sim import season


@pytest.fixture(scope="module")
def db():
    store = open_store()
    ingest(store, season(40))
    return store


def first_pitcher(db):
    return db.execute("SELECT pitcher, pitcher_team FROM pitches LIMIT 1").fetchone()


def test_arsenal_is_ranked_by_usage_and_adds_up(db):
    pitcher, _ = first_pitcher(db)
    out = scout(db, pitcher, None, Filters())
    usage = [a["usage"] for a in out["arsenal"]]
    assert usage == sorted(usage, reverse=True) and abs(sum(usage) - 1) < 0.01
    assert out["throws"] in ("R", "L") and out["points"]


def test_filters_narrow_the_pitches(db):
    pitcher, _ = first_pitcher(db)
    everything = scout(db, pitcher, None, Filters())["pitches"]
    fast = scout(db, pitcher, None, Filters(kmh=(140, 200)))
    assert 0 < fast["pitches"] < everything and all(p["kmh"] >= 140 for p in fast["points"])
    righties = scout(db, pitcher, None, Filters(side="R"))
    lefties = scout(db, pitcher, None, Filters(side="L"))
    assert righties["pitches"] + lefties["pitches"] == everything


def test_opponent_and_empty_results(db):
    pitcher, team = first_pitcher(db)
    assert scout(db, pitcher, team, Filters())["pitches"] == 0  # never faces his own team
    assert scout(db, "Nobody", None, Filters())["arsenal"] == []
