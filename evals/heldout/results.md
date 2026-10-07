# AI coach, graded blind

**Passed 2/10**, useful 4/10.

| Question | Pass | Grader's note |
|---|---|---|
| Does Kim Ji-hoon lose more strikes at the back of the plate than most pitchers? | yes |  |
| Is Kim Do-yun a free swinger or a patient hitter? | no | Makes up a 'free-swinger threshold' and says he 'rarely takes off-zone pitches', the opposite of what a 23% chase rate means. |
| Kang Tae-yang is up with a 3-1 count against Jung Jae-won. What should we throw? | yes |  |
| Should Kim Min-jun be swinging at 2-0 pitches? | no | Misreads gain_from_taking_runs 0.409 runs as '41% more runs'. |
| What does Kang Do-yun throw most, and how hard does he throw it? | no | Never answers how hard he throws and doesn't say velocity is unavailable. |
| What's Park Ha-jun likely to throw on 0-2 to a left-handed hitter? | no | Never looked up Park Ha-jun (e.g. pitcher_profile) and asked for a batter instead of answering. |
| What's Yoon Tae-yang's batting average with runners in scoring position? | no | Mislabels the stat as 'RBI-in-scoring-position average' and offers an overall batting average that no tool provided. |
| Which Doosan Bears hitter has the biggest ABS zone? | no | Checked only 2 of 9 Doosan batters, so the 'largest zone' claim is unsupported. |
| Which team's pitchers lose the most strikes at the back of the plate? | no | Queried only LG Twins, then claimed they lose the most strikes of any team. |
| Why does ABS call so many low curveballs balls? | no | Called no tools and gives no explanation, only a vague claim that numbers are unavailable. |
