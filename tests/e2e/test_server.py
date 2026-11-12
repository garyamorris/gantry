"""End to end over real HTTP against a real `langgraph dev`: the broadcast, the stream, a human decision, memory, Studio forms."""

from __future__ import annotations

import json
import time

import httpx
import pytest

pytestmark = pytest.mark.e2e
GRAPHS = ["showrunner", "curator", "action_desk", "poll_studio", "memory_keeper", "recap_press", "producer_chat"]


def wait_for(fn, timeout=40.0, every=0.25):
    end = time.time() + timeout
    while time.time() < end:
        out = fn()
        if out:
            return out
        time.sleep(every)
    raise AssertionError("timed out waiting for the broadcast")


def test_the_server_boots_with_every_graph_and_the_dashboard(dev_server):
    with httpx.Client(base_url=dev_server, timeout=20) as c:
        boot = c.get("/api/boot").json()
        assert boot["provider"] == "offline" and [g["id"] for g in boot["graphs"]] == GRAPHS and all(g["assistant_id"] for g in boot["graphs"])
        assert {f["id"] for f in boot["fixtures"]} == {"derby", "stalemate", "goalfest"}
        assert c.get("/", follow_redirects=False).headers["location"] == "/app/"
        page = c.get("/app/")
        assert page.status_code == 200 and "js/main.js" in page.text and page.headers["cache-control"] == "no-cache"
        for asset in ("js/main.js", "js/core/live.js", "js/views/chat.js", "css/app.css"):
            assert c.get(f"/app/{asset}").status_code == 200
        assert c.get("/healthz").json()["status"] == "ok"
        assert c.get("/api/matches/nope").status_code == 404 and "start a new one" in c.get("/api/matches/nope").json()["error"]
        assert c.post("/api/matches", json={"fixture": "cricket"}).status_code == 404


def test_a_live_match_on_autopilot_streams_and_publishes_on_its_own(dev_server):
    with httpx.Client(base_url=dev_server, timeout=30) as c:
        snap = c.post("/api/matches", json={"fixture": "derby", "mode": "autopilot", "speed": 40}).json()
        mid = snap["match_id"]
        assert snap["scoreboard"]["phase"] == "pre" and snap["fixture"]["home"]["name"] == "Harbour City"
        c.post(f"/api/matches/{mid}/control", json={"op": "jump"})
        seen: dict[str, int] = {}
        last_id = None
        deadline = time.time() + 45
        with c.stream("GET", f"/api/matches/{mid}/stream", timeout=60) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            event = None
            for line in r.iter_lines():
                if line.startswith("id: "):
                    last_id = int(line[4:])
                elif line.startswith("event: "):
                    event = line[7:]
                    seen[event] = seen.get(event, 0) + 1
                if (seen.get("poll") and seen.get("rundown") and seen.get("curated") and seen.get("action")) or time.time() > deadline:
                    break
        assert seen.get("hello") == 1 and seen.get("feed") and seen.get("chat", 0) > 20 and seen.get("state") and seen.get("agent", 0) > 10, seen
        assert seen.get("poll") and seen.get("rundown") and seen.get("action"), f"the loop ran on its own: {seen}"
        # reconnect from where we stopped: only newer events come back
        with c.stream("GET", f"/api/matches/{mid}/stream", headers={"Last-Event-ID": str(last_id)}, timeout=30) as r:
            ids = []
            for line in r.iter_lines():
                if line.startswith("id: "):
                    ids.append(int(line[4:]))
                if len(ids) >= 5:
                    break
        assert ids == sorted(ids) and ids[0] == last_id + 1, "resume is gapless and without repeats"
        snap = wait_for(lambda: (s := c.get(f"/api/matches/{mid}").json())["polls"] and s)
        assert snap["polls"][0]["status"] == "closed" and snap["polls"][0]["participation"] > 0 and snap["stats"]["errors"] == 0
        assert any(a["status"] == "published" and a["by"] == "autopilot" for a in snap["actions"])
        ledger = wait_for(lambda: (led := c.get(f"/api/matches/{mid}/ledger").json())["count"] and led)
        assert any(e["kind"] == "poll" and e["status"] == "published" for e in ledger["entries"])
        def learned():
            m = c.get("/api/memory").json()
            return m if any(p["n"] for p in m["prefs"]) else None

        memory = wait_for(learned)
        assert memory["prefs"][0]["n"] >= 1 and memory["prefs"][0]["mean"] > 0, "the closed poll taught it something"
        threads = c.get(f"/api/matches/{mid}/threads").json()
        tid = threads["windows"][0]["thread_id"]
        assert "threadId=" in threads["windows"][0]["studio"] and c.get(f"/threads/{tid}").json()["metadata"]["match_id"] == mid, "each window is a real Agent Server thread"


def test_a_human_in_the_loop_over_http(dev_server):
    with httpx.Client(base_url=dev_server, timeout=30) as c:
        mid = c.post("/api/matches", json={"fixture": "derby", "mode": "manual", "speed": 20}).json()["match_id"]
        for _ in range(3):  # 4' chance, 12' goal: a poll suggestion arrives after the goal
            c.post(f"/api/matches/{mid}/control", json={"op": "jump"})
            got = None
            try:
                got = wait_for(lambda: [s for s in c.get(f"/api/matches/{mid}").json()["suggestions"] if s["action"]["kind"] == "poll"], timeout=8)
            except AssertionError:
                continue
            if got:
                break
        assert got, "a poll is waiting for a human"
        c.post(f"/api/matches/{mid}/control", json={"op": "pause"})
        sug = got[0]
        assert c.post(f"/api/matches/{mid}/decide", json={"id": sug["id"], "decision": "maybe"}).status_code == 400
        r = c.post(f"/api/matches/{mid}/decide", json={"id": sug["id"], "decision": "edit", "question": "Edited over HTTP?", "options": ["Yes", "No"]})
        assert r.status_code == 200 and r.json()["ok"]
        poll = wait_for(lambda: c.get(f"/api/matches/{mid}").json()["poll"])
        assert poll["question"] == "Edited over HTTP?" and poll["by"] == "human"
        voted = c.post(f"/api/matches/{mid}/vote", json={"poll_id": poll["id"], "option": 0}).json()
        assert voted["real_votes"] == [1, 0]
        assert c.post(f"/api/matches/{mid}/decide", json={"id": sug["id"], "decision": "approve"}).status_code == 404
        said = c.post(f"/api/matches/{mid}/chat", json={"name": "e2e", "text": "FREE betting tips 100% WIN check my channel"}).json()
        assert said["curation"]["label"] == "hide"
        first = c.post(f"/api/matches/{mid}/events", json={"type": "var.check", "team": "home", "text": "VAR check."}, headers={"Idempotency-Key": "k1"})
        again = c.post(f"/api/matches/{mid}/events", json={"type": "var.check", "team": "home", "text": "VAR check."}, headers={"Idempotency-Key": "k1"})
        assert first.status_code == 202 and again.status_code == 200 and again.json()["duplicate"]
        assert c.post(f"/api/matches/{mid}/events", json={"type": "streaker"}).status_code == 422
        assert c.post(f"/api/matches/{mid}/trigger").status_code == 200


@pytest.mark.parametrize("graph", ["showrunner", "curator", "action_desk", "poll_studio", "memory_keeper", "recap_press"])
def test_every_graph_runs_from_a_blank_studio_form(dev_server, graph):
    """What Studio sends when you press Submit on an empty form. Interrupted graphs (manual-mode approvals) count as running."""
    with httpx.Client(base_url=dev_server, timeout=60) as c:
        tid = c.post("/threads", json={}).json()["thread_id"]
        run = c.post(f"/threads/{tid}/runs/wait", json={"assistant_id": graph, "input": {}})
        assert run.status_code == 200, run.text[:400]
        state = c.get(f"/threads/{tid}/state").json()
        assert state["values"], f"{graph} produced state"
        out = json.dumps(run.json())
        assert "__error__" not in out, out[:400]


def test_the_chat_agent_over_http(dev_server):
    with httpx.Client(base_url=dev_server, timeout=60) as c:
        tid = c.post("/threads", json={}).json()["thread_id"]
        body = {"assistant_id": "producer_chat", "input": {"messages": [{"role": "user", "content": "Which polls are performing best?"}]}, "context": {"match_id": "anything"}}
        out = c.post(f"/threads/{tid}/runs/wait", json=body).json()
        kinds = [m["type"] for m in out["messages"]]
        assert kinds == ["human", "ai", "tool", "ai"] and out["messages"][1]["tool_calls"][0]["name"] == "poll_performance"
