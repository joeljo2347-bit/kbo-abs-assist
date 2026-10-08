You are grading an AI assistant that answers baseball coaches' questions using analysis tools.
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
