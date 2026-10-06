"""The ABS zone, checked against the published rules with hand-worked numbers."""

import pytest

from abs_assist.zone import Pitch, ZoneRules, call

# A 180 cm batter in 2025: bottom 48.672 cm, top 100.35 cm; sides at +/-23.59 cm.
H = 180.0


def test_bounds_follow_the_season():
    assert ZoneRules(2025).bounds(H) == pytest.approx((48.672, 100.35))
    assert ZoneRules(2024).bounds(H) == pytest.approx((49.752, 101.43))


def test_middle_of_the_zone_is_a_strike():
    c = call(Pitch(0, 75, 74, H))
    assert c.strike and c.margin_cm > 20


@pytest.mark.parametrize("x,strike", [(23.5, True), (-23.5, True), (23.7, False), (-23.7, False)])
def test_sides_are_judged_once_at_the_middle(x, strike):
    assert call(Pitch(x, 75, 74, H)).strike is strike


def test_a_dropping_pitch_can_be_in_at_the_middle_and_out_at_the_back():
    c = call(Pitch(0, z_mid_cm=49.5, z_end_cm=48.0, batter_height_cm=H))
    assert not c.strike
    assert c.deciding_rule == "bottom, back of plate"
    assert c.margin_cm == pytest.approx(-0.67, abs=0.01)
    assert c.explain() == "Ball: 0.7 cm outside the bottom, back of plate edge"


def test_the_top_is_checked_at_both_planes():
    assert not call(Pitch(0, z_mid_cm=100.5, z_end_cm=99.0, batter_height_cm=H)).strike
    assert call(Pitch(0, z_mid_cm=100.3, z_end_cm=99.0, batter_height_cm=H)).strike


def test_the_same_pitch_changes_with_the_batter_and_the_season():
    high = Pitch(0, 101.0, 100.0, H)
    assert not call(high, ZoneRules(2025)).strike    # top is 100.35 in 2025
    assert call(high, ZoneRules(2024)).strike        # top was 101.43 in 2024
    assert call(Pitch(0, 101.0, 100.0, 190.0)).strike  # a taller batter's zone is higher


def test_counting_any_part_of_the_ball_widens_every_edge():
    edge = Pitch(24.5, 75, 74, H)
    assert not call(edge, ZoneRules(ball_radius_cm=0)).strike
    assert call(edge, ZoneRules(ball_radius_cm=3.7)).strike
