# AI coach, graded blind

**Passed 6/10**, useful 8/10.

| Question | Pass | Grader's note |
|---|---|---|
| Among these KIA Tigers hitters, who chases the most: Choi Ji-hoon, Choi Woo-jin, Lim Yeon-woo? | yes |  |
| Give me a short scouting report on Yoon Hyun-woo: his ABS zone, how often he chases, how often he whiffs. | yes |  |
| How many strikes do LG Twins pitchers lose at the back of the plate, and on which pitches? | no | Says 'in a season', a time frame no tool result gives. |
| How should we pitch Jang Ji-ho with an 0-2 count? | yes |  |
| What is Jung Woo-jin likely to throw when he's behind 2-0 after a fastball? | yes |  |
| What should Kang Ha-jun throw Jang Ji-ho on a 1-2 count? | no | Presents a pitcher-agnostic plan as Kang Ha-jun's plan without checking that he throws a curveball or slider. |
| What's Lee Do-yun's ERA this season? | yes |  |
| Which pitches should Oh Ji-hoon lay off on the first pitch of an at-bat? | no | Misreads the pitcher-side attack plan: tells the batter to lay off letter-high edge pitches with a 100% called-strike chance, which is the wrong conclusion. |
| Who are the LG Twins pitchers? | yes |  |
| Who misses more bats, Kang Ha-jun or Jung Woo-jin? | no | Used batter_profile for pitchers, never tried a pitcher tool, then wrongly said the data isn't available. |
