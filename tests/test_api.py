"""The HTTP API end to end, on a small season, with a scripted model for the coach."""

import pytest
from fastapi.testclient import TestClient

from abs_assist.api import create_app
from abs_assist.collect import ingest, open_store
from abs_assist.sim import season


def replies(*texts):
    """A scripted model: looks something up once per conversation (the coach requires it), then answers."""
    queue = list(texts)
    lookup = {"content": "", "tool_calls": [{"id": "c1", "type": "function",
                                             "function": {"name": "find_players", "arguments": '{"query": "LG Twins"}'}}]}
    return lambda messages, schema: {"content": queue.pop(0)} if any(m["role"] == "tool" for m in messages) else lookup


@pytest.fixture(scope="module")
def client():
    db = open_store()
    ingest(db, season(30))
    return TestClient(create_app(db, chat=replies("First answer.", "Second answer.", "Third.")))


def test_meta_pitches_and_tools(client):
    meta = client.get("/api/meta").json()
    assert len(meta["teams"]) == 10 and meta["games"] == 30
    pts = client.get("/api/pitches", params={"pitcher_team": "LG Twins"}).json()
    assert pts["shown"] > 0 and {"x", "h_mid", "h_end", "why"} <= set(pts["points"][0])
    pitcher = meta["roster"]["LG Twins"]["pitchers"][0]
    assert client.get("/api/tool/pitcher_profile", params={"pitcher": pitcher}).json()["pitcher"] == pitcher
    assert client.get("/api/tool/pitcher_profile", params={"pitcher": "Nobody"}).status_code == 400


def test_live_replay_never_peeks_ahead(client):
    game = client.get("/api/live/20").json()
    assert game["pitches"] and all("predicted" in p for p in game["pitches"])
    assert client.get("/api/live/9999").status_code == 404


def test_coach_conversations_continue(client):
    first = client.post("/api/coach", json={"question": "Hi"}).json()
    second = client.post("/api/coach", json={"question": "And?", "conversation_id": first["conversation_id"]}).json()
    # The reply is written in code from the tools; a follow-up that calls none reuses the last turn's results.
    assert second["conversation_id"] == first["conversation_id"] and second["answer"] == first["answer"]
    assert first["answer"].startswith("Found pitchers")
    other = client.post("/api/coach", json={"question": "New", "conversation_id": "unknown"}).json()
    assert other["conversation_id"] != first["conversation_id"]
    assert client.post("/api/coach", json={"question": ""}).status_code == 422


def test_bad_parameters_are_refused_not_crashing(client):
    assert client.get("/api/pitches", params={"limit": -1}).status_code == 422
    assert client.get("/api/live/99999999999999999999").status_code == 422
    assert client.get("/api/tool/predict_next_pitch", params={"pitcher": "x", "balls": -7, "strikes": 9}).status_code == 400
