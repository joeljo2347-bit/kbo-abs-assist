import pytest

from abs_assist.zone import ZoneRules


def test_unknown_season_is_a_clear_error():
    with pytest.raises(ValueError, match="No published ABS zone for 2019"):
        ZoneRules(2019)
