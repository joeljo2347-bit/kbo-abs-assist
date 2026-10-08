"""The hybrid reply: facts laid out by code, the model's wording checked against them, the code's draft as fallback."""

import json

from abs_assist import writer


def call(tool, result, **args):
    return {"tool": tool, "args": args, "result": json.dumps(result)}


PROFILE = call("batter_profile", {"batter": "Kim Do-yun", "chase_rate": 0.23, "batting_average": 0.276,
                                  "compared_with_league": {"batting_average": {"value": 0.276, "league_average": 0.258}}},
               batter="Kim Do-yun")


def test_facts_show_rates_as_percentages_and_averages_as_three_places():
    text = writer.facts([PROFILE])
    assert "chase rate (swings at balls): 23.0%" in text and "batting average: .276" in text and "value .276" in text


def test_problems_catch_invented_numbers_names_and_locations():
    text = writer.facts([PROFILE])
    assert writer.problems("He chases 23.0% and hits .276.", text, "Is Kim Do-yun patient?") == []
    assert writer.problems("He chases 31% of the time.", text, "")
    assert writer.problems("Lee Ji-hoon is better.", text, "")
    assert writer.problems("Throw it letter-high.", text, "")


def test_a_reply_that_fails_twice_falls_back_to_the_draft():
    replies = iter([{"content": "He hits 31 homers."}, {"content": "Still 31 homers."}])
    assert writer.write(lambda m, s: next(replies), "Power?", "Draft.", [PROFILE]) == "Draft."


def test_a_good_reply_is_kept_and_a_server_failure_keeps_the_draft():
    good = "Yes: he chases only 23.0% of pitches outside the zone."
    assert writer.write(lambda m, s: {"content": good}, "Is he patient?", "Draft.", [PROFILE]) == good

    def broken(messages, schema):
        raise OSError("model down")
    assert writer.write(broken, "Is he patient?", "Draft.", [PROFILE]) == "Draft."


def test_style_problems():
    assert writer.style_problems("| a | b |")
    assert writer.style_problems("김도윤은 좋은 타자다")
    assert writer.style_problems("next_pitch_after > fastball: 50%")
    assert writer.style_problems("He swings at 46.1% of first pitches — above his 44.0% overall.") == []


def test_advice_that_contradicts_the_verdict_is_sent_back():
    facts = "summary: Verdict: swing at strikes, take balls. In the zone ..."
    assert writer.stance_problems("He should take the first pitch.", facts)
    assert writer.stance_problems("Swing at strikes and take balls.", facts) == []
