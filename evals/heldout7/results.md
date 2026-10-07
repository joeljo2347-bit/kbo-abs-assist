# AI coach, graded blind

**Passed 8/10**, useful 9/10.

| Question | Pass | Grader's note |
|---|---|---|
| How many strikes do KT Wiz pitchers lose at the back of the plate? | no | share_of_takes is 0.0355 (3.55%), but the answer reports 3.5%, which is truncated rather than rounded to 3.6%. |
| How should we pitch Choi Ha-jun with two strikes and no balls? | yes |  |
| Is Jang Tae-yang hard to strike out? | yes | Numbers are supported, but the strikeout-rate sentence is repeated word for word. |
| Should Kim Dong-hyun swing more on 3-0? | yes |  |
| What should Kang Ji-ho throw Oh Jun-seo with the count 0-0? | yes |  |
| What will Oh Jae-won throw next after a curveball on a 1-1 count? | no | No tool calls; the answer is a raw HTTP 500 error and does not address the question. |
| What's Jung Ha-jun's WAR? | yes |  |
| Which pitchers throw the most pitches in the zone? | yes |  |
| Which team's hitters hit the most home runs? | yes |  |
| Who throws harder, Jung Woo-jin or Kim Jun-seo? | yes |  |
