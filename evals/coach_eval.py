"""The AI coach, graded blind.

    python -m evals.coach_eval run       # ask the questions; writes evals/coach_runs.jsonl
    python -m evals.coach_eval packet    # writes evals/blind/packet.md + packet.jsonl (+ key.json)
    python -m evals.coach_eval score     # reads evals/blind/grades.jsonl; writes evals/blind/results.md

Add `heldout` (e.g. `run heldout`) for the held-out set: ten questions written after the coach was
last changed, never used to change it, run once and reported as they came out (evals/heldout/).
`heldout2` to `heldout6` were each written and committed before the changes that followed the set before.

The questions use real names from the simulated league and include traps: data the tools don't
have (ERA), and questions that need several lookups. The grader sees only each question, every
tool call with its result, and the answer; no expected answers; ids are opaque and shuffled.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Dict, List

from abs_assist.api import build_store
from abs_assist.coach import Coach, http_chat
from abs_assist.tools import Toolbox

HERE = Path(__file__).resolve().parent
SET = sys.argv[2] if len(sys.argv) > 2 else "dev"   # dev, heldout or heldout2
OUT = HERE / {"dev": "blind"}.get(SET, SET)
RUNS = HERE / ("coach_runs.jsonl" if SET == "dev" else f"coach_runs_{SET}.jsonl")

RUBRIC = """You are grading an AI assistant that answers baseball coaches' questions using analysis tools.
You know nothing else about it. Grade strictly, from each record alone.

Each item has the question, every tool call the assistant made with the tool's result, and the final answer.
Tool results may include a "how_to_read" field that explains what their numbers mean.

For each item decide:
- "pass": true only if ALL hold: the answer addresses what was asked (or clearly says the data to answer
  it isn't available); every number and factual claim in the answer is supported by a tool result shown;
  no number is misread (e.g. presenting an expected value as a percentage of pitches); and it doesn't
  invent statistics the tools didn't provide.
- "useful": true if a coach could act on it (a clear recommendation or a clear "not available").
- "note": one short sentence on what was wrong; empty if nothing.

Be strict: when in doubt, mark it false.
Reply with one JSON object per line for every item id, exactly like:
{"id": "...", "pass": true, "useful": true, "note": ""}
"""


def questions(tb: Toolbox) -> List[str]:
    lg = tb.find_players("LG Twins")
    kt, kia = tb.find_players("KT Wiz"), tb.find_players("KIA Tigers")
    p1, p2, p3 = lg["pitchers"][:3]
    b1, b2, b3 = kt["batters"][:3]
    return [
        f"What should {p1} throw {b1} on a 1-2 count?",
        f"What is {p2} likely to throw when he's behind 2-0 after a fastball?",
        "How many strikes do LG Twins pitchers lose at the back of the plate, and on which pitches?",
        f"Give me a short scouting report on {b2}: his ABS zone, how often he chases, how often he whiffs.",
        f"Which pitches should {b3} lay off on the first pitch of an at-bat?",
        f"Who misses more bats, {p1} or {p2}?",
        "Who are the LG Twins pitchers?",
        f"What's {p3}'s ERA this season?",
        f"How should we pitch {b1} with an 0-2 count?",
        f"Among these KIA Tigers hitters, who chases the most: {', '.join(kia['batters'][:3])}?",
    ]


def heldout_questions(tb: Toolbox) -> List[str]:
    """Other teams, other kinds of question; written before any of them was asked."""
    ss, nc, ssg = tb.find_players("Samsung Lions"), tb.find_players("NC Dinos"), tb.find_players("SSG Landers")
    doosan, lotte = tb.find_players("Doosan Bears"), tb.find_players("Lotte Giants")
    return [
        f"What does {ss['pitchers'][0]} throw most, and how hard does he throw it?",
        f"{doosan['batters'][0]} is up with a 3-1 count against {ss['pitchers'][1]}. What should we throw?",
        f"Is {doosan['batters'][1]} a free swinger or a patient hitter?",
        "Which Doosan Bears hitter has the biggest ABS zone?",
        "Why does ABS call so many low curveballs balls?",
        f"Does {ssg['pitchers'][0]} lose more strikes at the back of the plate than most pitchers?",
        f"What's {nc['pitchers'][0]} likely to throw on 0-2 to a left-handed hitter?",
        f"What's {lotte['batters'][0]}'s batting average with runners in scoring position?",
        f"Should {lotte['batters'][1]} be swinging at 2-0 pitches?",
        "Which team's pitchers lose the most strikes at the back of the plate?",
    ]


def heldout2_questions(tb: Toolbox) -> List[str]:
    """A second held-out set, written and committed before the changes made after the first one."""
    kt, kiwoom, hanwha = tb.find_players("KT Wiz"), tb.find_players("Kiwoom Heroes"), tb.find_players("Hanwha Eagles")
    return [
        f"How hard does {hanwha['pitchers'][0]} throw his fastball, and what's his best secondary pitch?",
        f"Who chases more on the Kiwoom Heroes, {kiwoom['batters'][0]} or {kiwoom['batters'][1]}?",
        "Which KT Wiz hitter whiffs the most?",
        f"Is {kt['pitchers'][0]}'s zone rate high or low compared with other pitchers?",
        f"We face {hanwha['batters'][2]} with a full count. What should {kt['pitchers'][1]} throw?",
        "How does the ABS strike zone change with a batter's height?",
        f"What does {kiwoom['pitchers'][1]} usually throw after a slider?",
        f"How many home runs has {kt['batters'][2]} hit?",
        f"Should {hanwha['batters'][3]} take the first pitch?",
        "Which team's pitchers throw the most pitches in the strike zone?",
    ]


def heldout3_questions(tb: Toolbox) -> List[str]:
    """A third held-out set, committed before the changes made after the second."""
    ssg, lotte, samsung = tb.find_players("SSG Landers"), tb.find_players("Lotte Giants"), tb.find_players("Samsung Lions")
    return [
        f"What's {ssg['pitchers'][1]}'s go-to pitch with two strikes?",
        f"Is {lotte['batters'][2]} better than average at laying off pitches outside the zone?",
        "Who has the highest batting average on the SSG Landers?",
        f"How fast is {samsung['pitchers'][2]}'s fastball compared with the league?",
        f"{lotte['batters'][3]} up, 2-2 count, {ssg['pitchers'][0]} pitching. What's the plan?",
        "What's the difference between the 2024 and 2025 ABS zones?",
        f"Should {samsung['batters'][0]} be more aggressive on 3-1?",
        f"What's {lotte['pitchers'][0]}'s strikeout rate?",
        "Which pitchers in the league get the most swings and misses?",
        f"How often does {samsung['pitchers'][0]} throw his changeup to left-handed hitters?",
    ]


def heldout4_questions(tb: Toolbox) -> List[str]:
    """A fourth held-out set, committed before the coach was run on any other set after the fixes to sets 1-3."""
    kia, hanwha, nc = tb.find_players("KIA Tigers"), tb.find_players("Hanwha Eagles"), tb.find_players("NC Dinos")
    return [
        f"Does {kia['pitchers'][2]} throw harder than most pitchers?",
        f"Which pitch does {hanwha['pitchers'][1]} lean on against right-handed hitters?",
        "Who walks the most among NC Dinos hitters?",
        f"Is {nc['batters'][1]} aggressive on pitches in the zone?",
        f"{kia['batters'][3]} is batting, 0-1 count, {nc['pitchers'][0]} on the mound. What do we throw?",
        "Do taller hitters get a bigger ABS zone?",
        f"Should {hanwha['batters'][0]} swing at the first pitch?",
        f"What's {nc['pitchers'][2]}'s walk rate, and is that good?",
        "Which team's hitters chase the most?",
        f"What does {kia['pitchers'][0]} throw after a fastball?",
    ]


def heldout5_questions(tb: Toolbox) -> List[str]:
    """A fifth held-out set, committed with the fixes from sets 1-4 and before any rerun."""
    lg, doosan, kt = tb.find_players("LG Twins"), tb.find_players("Doosan Bears"), tb.find_players("KT Wiz")
    kiwoom, ssg = tb.find_players("Kiwoom Heroes"), tb.find_players("SSG Landers")
    return [
        f"How many strikeouts does {doosan['batters'][4]} have, and is that a lot?",
        f"What's {kiwoom['pitchers'][2]}'s best strikeout pitch?",
        "Which team's pitchers get hitters to chase the most?",
        f"Is {ssg['batters'][1]} tough to strike out?",
        f"Behind 2-1 to {lg['batters'][4]}, what should {doosan['pitchers'][3]} throw?",
        "How much does the ABS zone's top move between a 170 cm and a 190 cm hitter?",
        f"Does {kt['pitchers'][3]} throw a lot of strikes?",
        f"What's {lg['batters'][5]}'s on-base percentage against left-handed pitchers?",
        f"Should {kiwoom['batters'][2]} protect the plate more with two strikes?",
        "Who are the hardest throwers in the league?",
    ]


def heldout6_questions(tb: Toolbox) -> List[str]:
    """A sixth held-out set, committed with the last fixes and before any run of it."""
    samsung, kia, lotte = tb.find_players("Samsung Lions"), tb.find_players("KIA Tigers"), tb.find_players("Lotte Giants")
    nc, hanwha = tb.find_players("NC Dinos"), tb.find_players("Hanwha Eagles")
    return [
        f"Is {samsung['batters'][3]} a good contact hitter?",
        f"What's {kia['pitchers'][3]}'s main pitch and how often does he throw it?",
        "Which team's pitchers walk the most batters?",
        f"Should {lotte['batters'][4]} be patient on 2-1?",
        f"{nc['batters'][5]} at the plate, 1-1, {hanwha['pitchers'][2]} pitching. What's the call?",
        "How many centimetres does the ABS zone's bottom sit above the ground for a 175 cm hitter?",
        f"Does {samsung['pitchers'][1]} get a lot of swings and misses compared with other pitchers?",
        f"What's {kia['batters'][5]}'s slugging percentage?",
        "Who are the top home run hitters in the league?",
        f"What does {lotte['pitchers'][2]} throw to left-handed hitters with two strikes?",
    ]


def run() -> None:
    tb = Toolbox(build_store(Path("data/abs.db")))
    coach, out = Coach(tb, http_chat()), []
    pick = {"dev": questions, "heldout": heldout_questions, "heldout2": heldout2_questions, "heldout3": heldout3_questions,
            "heldout4": heldout4_questions, "heldout5": heldout5_questions,
            "heldout6": heldout6_questions}[SET]
    for q in pick(tb):
        r = coach.ask(q)
        out.append({"question": q, "calls": r["calls"], "answer": r["answer"], "corrected": r["corrected"]})
        print(f"- {q}\n  tools: {[c['tool'] for c in r['calls']]}\n  {r['answer'][:160]!r}")
    RUNS.write_text("\n".join(json.dumps(o) for o in out) + "\n")


def packet() -> None:
    items, key = [], {}
    for i, line in enumerate(RUNS.read_text().splitlines()):
        row = json.loads(line)
        item_id = hashlib.sha256(f"coach|{i}".encode()).hexdigest()[:10]
        items.append({"id": item_id, "question": row["question"], "tool_calls": row["calls"], "answer": row["answer"]})
        key[item_id] = i
    random.Random(7).shuffle(items)
    OUT.mkdir(exist_ok=True)
    (OUT / "packet.md").write_text(RUBRIC)
    (OUT / "packet.jsonl").write_text("\n".join(json.dumps(i) for i in items) + "\n")
    (OUT / "key.json").write_text(json.dumps(key, indent=1))
    print(f"{len(items)} items -> {OUT}")


def score() -> None:
    key = json.loads((OUT / "key.json").read_text())
    runs = [json.loads(line) for line in RUNS.read_text().splitlines()]
    grades = {g["id"]: g for g in map(json.loads, (OUT / "grades.jsonl").read_text().splitlines())}
    if set(grades) != set(key):
        sys.exit("The grader skipped or added items.")
    rows: List[Dict] = sorted(({**grades[i], "q": runs[n]["question"]} for i, n in key.items()), key=lambda r: r["q"])
    lines = ["# AI coach, graded blind", "",
             f"**Passed {sum(r['pass'] for r in rows)}/{len(rows)}**, useful {sum(r['useful'] for r in rows)}/{len(rows)}.", "",
             "| Question | Pass | Grader's note |", "|---|---|---|"]
    lines += [f"| {r['q']} | {'yes' if r['pass'] else 'no'} | {r['note']} |" for r in rows]
    (OUT / "results.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    {"run": run, "packet": packet, "score": score}[sys.argv[1]]()
