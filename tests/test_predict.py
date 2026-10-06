import pytest

from abs_assist.predict import PitchPredictor


def test_it_learns_a_count_habit():
    m = PitchPredictor()
    for _ in range(30):
        m.update("Kim", 0, 2, "fastball", "slider")   # ahead: always a slider
        m.update("Kim", 2, 0, "slider", "fastball")   # behind: always a fastball
    assert m.top("Kim", 0, 2, "fastball")[0] == "slider"
    assert m.top("Kim", 3, 1, "slider")[0] == "fastball"


def test_probabilities_sum_to_one_and_only_his_pitches():
    m = PitchPredictor()
    m.update("Kim", 0, 0, "", "fastball")
    m.update("Lee", 0, 0, "", "curveball")
    dist = m.predict("Kim", 0, 0)
    assert sum(dist.values()) == pytest.approx(1.0)
    assert set(dist) == {"fastball"}


def test_an_unknown_pitcher_falls_back_to_the_league():
    m = PitchPredictor()
    for _ in range(10):
        m.update("Kim", 0, 2, "", "splitter")
    assert m.top("Nobody", 0, 2)[0] == "splitter"
    assert PitchPredictor().predict("Nobody", 0, 0) == {}
