from abs_assist.live import Baseline, LiveTracker


def pitch(kmh=146.0, kind="fastball", strike=1, swing=False, result="called_strike"):
    return {"pitcher": "Kim", "pitch_type": kind, "kmh": kmh, "abs_strike": strike, "swing": swing, "result": result}


def test_velocity_drop_against_his_own_early_pitches_alerts_once():
    t = LiveTracker({"Kim": Baseline(fastball_kmh=146.0, zone_rate=0.5)})
    early = [a for k in [146.0, 145.5, 146.5, 146.0, 145.0, 147.0, 146.0, 145.5, 146.5, 146.0] for a in t.add(pitch(k))]
    late = [a for _ in range(7) for a in t.add(pitch(143.5))]
    assert early == [] and late == []                    # needs 8 slower fastballs
    assert t.add(pitch(143.5)) == ["velocity: last 8 fastballs 2.5 km/h below his first 10 today"]
    assert t.add(pitch(143.0)) == []                      # once per game


def test_normal_noise_does_not_alert():
    t = LiveTracker({"Kim": Baseline(fastball_kmh=146.0, zone_rate=0.5)})
    noisy = [146.0, 143.0, 149.0, 144.5, 147.5, 145.0, 148.0, 143.5, 146.5, 146.0] * 3
    assert [a for k in noisy for a in t.add(pitch(k))] == []


def test_losing_the_zone_and_pitch_counts():
    t = LiveTracker({"Kim": Baseline(fastball_kmh=None, zone_rate=0.55)})
    alerts = [a for i in range(100) for a in t.add(pitch(kind="slider", strike=int(i < 70 or i % 4 == 0)))]
    assert [a for a in alerts if a.startswith("zone")] == ["zone: 40% in the zone over the last 20, his norm is 55%"]
    assert "count: 90 pitches" in alerts and "count: 100 pitches" in alerts
    assert t.summary("Kim")["pitches"] == 100


def test_no_baseline_means_no_alerts():
    t = LiveTracker({})
    assert all(t.add(pitch(130.0)) == [] for _ in range(20))
