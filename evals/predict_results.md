# Next-pitch prediction

217,179 pitches over 720 simulated games, predicted before each pitch, learned after.

| Games seen | Model | Pitcher's own most common pitch | Always fastball | Ceiling (true odds) |
|---|---|---|---|---|
| 0-120 | **50.9%** | 48.8% | 47.9% | 55.0% |
| 120-240 | **52.9%** | 49.4% | 47.5% | 54.8% |
| 240-360 | **53.2%** | 49.3% | 47.6% | 54.4% |
| 360-480 | **53.4%** | 49.7% | 47.8% | 54.6% |
| 480-600 | **54.1%** | 49.7% | 48.1% | 54.9% |
| 600-720 | **54.1%** | 49.3% | 47.8% | 54.9% |

Second half of the season: the model is within 0.9% of the ceiling.
