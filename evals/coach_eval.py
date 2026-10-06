"""The AI coach, graded blind.

    python -m evals.coach_eval run       # ask the questions; writes evals/coach_runs.jsonl
    python -m evals.coach_eval packet    # writes evals/blind/packet.md + packet.jsonl (+ key.json)
    python -m evals.coach_eval score     # reads evals/blind/grades.jsonl; writes evals/blind/results.md

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
OUT = HERE / "blind"
RUNS = HERE / "coach_runs.jsonl"

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


def run() -> None:
    tb = Toolbox(build_store(Path("data/abs.db")))
    coach, out = Coach(tb, http_chat()), []
    for q in questions(tb):
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
