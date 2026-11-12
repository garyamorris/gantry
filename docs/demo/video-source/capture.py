#!/usr/bin/env python3
"""capture.py: run real windows through a running Gantry server and record every stream event (the "recorded runs" the replay visuals use).

    uv run python docs/demo/video-source/capture.py [--base http://127.0.0.1:2030] [--out captures/real]

Writes <out>/structure/<graph>.json  (GET /assistants/{id}/graph?xray=true: the REAL graph structure, subgraphs expanded),
      <out>/events.jsonl             {"t", "thread_id", "run_id", "graph_id", "event", "data"} per SSE event, all stream modes, subgraph namespaces kept,
      <out>/threads.json             what each thread was (graph, label, input summary).
and a tiny <out>/facts.json with the numbers the narration quotes (window sizes, shard counts, scores), read back from the recorded events.

Every run is a real Agent Server run on a real thread, so it is also visible in Studio and traced if tracing is on. Windows are cut from the
simulator (`gantry.live.windows.sample_window`) exactly as the live runtime cuts them.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from typing import Any

from langgraph_sdk import get_client

from gantry.live.windows import sample_window

HERE = os.path.dirname(os.path.abspath(__file__))
MODES = ["tasks", "updates", "custom", "messages", "checkpoints"]


class Recorder:
    def __init__(self, out: str) -> None:
        self.out = out
        os.makedirs(os.path.join(out, "structure"), exist_ok=True)
        self.f = open(os.path.join(out, "events.jsonl"), "w", encoding="utf-8")
        self.t0 = time.monotonic()
        self.threads: list[dict[str, Any]] = []

    def write(self, thread_id: str, run_id: str | None, graph: str, event: str, data: Any) -> None:
        rec = {"t": round(time.monotonic() - self.t0, 3), "thread_id": thread_id, "run_id": run_id or "?", "graph_id": graph, "event": event, "data": data}
        self.f.write(json.dumps(rec, default=str) + "\n")
        self.f.flush()


async def structure(client: Any, out: str) -> dict[str, str]:
    ids: dict[str, str] = {}
    for a in await client.assistants.search(limit=50):
        gid = a["graph_id"]
        if gid in ids or (a.get("metadata") or {}).get("created_by") != "system":
            continue
        ids[gid] = a["assistant_id"]
        g = await client.assistants.get_graph(a["assistant_id"], xray=True)
        json.dump({"graph_id": gid, "assistant_id": a["assistant_id"], "description": a.get("description"), "graph": g}, open(os.path.join(out, "structure", f"{gid}.json"), "w"), indent=1)
    print("structure:", sorted(ids))
    return ids


async def stream(client: Any, rec: Recorder, thread: str, graph: str, *, input: Any = None, resume: dict[str, Any] | None = None, context: dict[str, Any] | None = None) -> str:
    """One run, recorded. Returns the run id."""
    run_id = "?"
    kwargs: dict[str, Any] = {"command": {"resume": resume}} if resume is not None else {"input": input}
    if context:
        kwargs["context"] = context
    async for part in client.runs.stream(thread, graph, stream_mode=MODES, stream_subgraphs=True, **kwargs):
        if part.event == "metadata" and isinstance(part.data, dict) and part.data.get("run_id"):
            run_id = part.data["run_id"]
        rec.write(thread, run_id, graph, part.event, part.data)
    return run_id


async def pending(client: Any, thread: str) -> list[dict[str, Any]]:
    st = await client.threads.get_state(thread)
    out = []
    for it in st.get("interrupts") or []:
        if isinstance(it, dict) and it.get("id"):
            out.append({"id": it["id"], "value": it.get("value")})
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:2030")
    ap.add_argument("--out", default=os.path.join(HERE, "captures", "real"))
    a = ap.parse_args()
    client = get_client(url=a.base)
    rec = Recorder(a.out)
    await structure(client, a.out)
    facts: dict[str, Any] = {}

    # a clean slate: what the producer knows is what these runs teach it
    import httpx

    httpx.post(f"{a.base}/api/memory/reset", timeout=30)

    async def new_thread(label: str, graph: str, summary: dict[str, Any]) -> str:
        t = await client.threads.create(metadata={"app": "gantry", "video": label})
        rec.threads.append({"thread_id": t["thread_id"], "graph_id": graph, "label": label, "summary": summary, "metadata": {"graph_id": graph}, "runs": []})
        return t["thread_id"]

    # ---- 1. the red card at 63', manual mode: three suggestions, answered one at a time by id (the whole loop, with interrupts)
    w = sample_window("derby", 7, minute=63, mode="manual")
    w["match_id"] = "video-derby-63"
    facts["window_63"] = {"events": len(w["window"]["events"]), "comments": len(w["window"]["comments"]), "trigger": w["window"]["trigger"], "minute": w["match_state"]["minute"]}
    tid = await new_thread("showrunner-63", "showrunner", {"minute": "63'", "mode": "manual"})
    t_start = rec.t0
    await stream(client, rec, tid, "showrunner", input=w)
    pend = await pending(client, tid)
    facts["interrupts_first"] = [p["value"]["action"]["kind"] for p in pend]
    by_kind = {p["value"]["action"]["kind"]: p for p in pend}
    await asyncio.sleep(0.6)
    await stream(client, rec, tid, "showrunner", resume={by_kind["poll"]["id"]: {"decision": "approve", "by": "human"}})
    left = await pending(client, tid)
    facts["interrupts_after_poll"] = [p["value"]["action"]["kind"] for p in left]
    await asyncio.sleep(0.6)
    rest = {p["id"]: ({"decision": "reject", "by": "human", "note": "not now"} if p["value"]["action"]["kind"] == "highlight" else {"decision": "approve", "by": "human"}) for p in left}
    await stream(client, rec, tid, "showrunner", resume=rest)
    facts["end_state"] = (await client.threads.get_state(tid))["values"].get("summary")

    # ---- 2. flow 1 alone: the late winner at 90+3' (the busiest chat of the match)
    w2 = sample_window("derby", 7)
    tid2 = await new_thread("curator-winner", "curator", {"minute": w2["match_state"]["minute"]})
    await stream(client, rec, tid2, "curator", input=w2)
    out2 = (await client.threads.get_state(tid2))["values"]["rundown"]
    facts["curator"] = {"comments": len(w2["window"]["comments"]), "shards": -(-len(w2["window"]["comments"]) // 40), "headline": out2["headline"]["text"], "score": out2["headline"]["score"],
                        "highlights": len(out2["comments"]["highlights"]), "hidden": len(out2["comments"]["hidden"]), "collapsed": len(out2["comments"]["collapsed"]), "total": out2["comments"]["total"]}

    # ---- 3. the first feature alone: a poll for the penalty, three writers and a judge
    tid3 = await new_thread("poll-penalty", "poll_studio", {"minute": "71'"})
    await stream(client, rec, tid3, "poll_studio", input={"fixture": "derby", "minute": 71})
    poll = (await client.threads.get_state(tid3))["values"]["poll"]
    facts["poll"] = {"question": poll["question"], "options": poll["options"], "writer": poll["writer"], "judge": poll["judge"], "rounds": poll["rounds"], "template": poll["template"]}

    # ---- 4. autopilot: the same kind of window with nobody at the desk (nothing waits)
    w4 = sample_window("derby", 7, minute=71, mode="autopilot")
    w4["match_id"] = "video-derby-71"
    tid4 = await new_thread("showrunner-71-autopilot", "showrunner", {"minute": "71'", "mode": "autopilot"})
    await stream(client, rec, tid4, "showrunner", input=w4)
    facts["autopilot_71"] = (await client.threads.get_state(tid4))["values"].get("summary")

    rec.f.close()
    json.dump(rec.threads, open(os.path.join(a.out, "threads.json"), "w"), indent=1)
    json.dump(facts, open(os.path.join(a.out, "facts.json"), "w"), indent=1, default=str)
    print(json.dumps(facts, indent=1, default=str)[:3000])
    print("events:", sum(1 for _ in open(os.path.join(a.out, "events.jsonl"))), "->", a.out)


if __name__ == "__main__":
    asyncio.run(main())
