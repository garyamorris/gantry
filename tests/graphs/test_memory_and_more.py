"""memory_keeper, recap_press and producer_chat."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from gantry.flows.memory_keeper.build import build as build_keeper
from gantry.flows.producer_chat.build import build as build_chat
from gantry.flows.recap_press.build import build as build_recap
from gantry.memory.ledger import (
    GraphStore,
    fold_pref,
    ledger_list,
    ledger_put,
    ledger_similar,
    lessons_search,
    memory_reset,
    poll_results,
    prefs_all,
)
from helpers import cfg, drive, kinds


def result(i: int, template: str, part: float) -> dict:
    return {"id": f"p{i}", "topic": f"{template}:{i}", "template": template, "question": f"Question {i}?", "options": ["a", "b"], "votes": [int(1000 * part), 300],
            "total": int(1000 * part) + 300, "viewers": 10000, "participation": part, "opened_vt": i * 100, "closed_vt": i * 100 + 60, "minute": f"{i}'", "by": "autopilot"}


async def feed(graph, mode, rows, chooser=lambda p: {"decision": "keep"}):
    out = []
    for i, (t, p) in enumerate(rows):
        out.append(await drive(graph, {"match_id": "m1", "mode": mode, "poll_result": result(i, t, p)}, cfg(f"keeper-{mode}-{i}"), chooser))
    return out


ROWS = [("vibe_check", 0.08), ("decision", 0.31), ("goal_rating", 0.17), ("decision", 0.29), ("vibe_check", 0.07)]


async def test_the_keeper_learns_preferences_and_writes_lessons_when_the_evidence_is_in(saver, store):
    st = GraphStore(store)
    await ledger_put(st, "m1", {"id": "p1", "kind": "poll", "topic": "decision:1", "status": "published", "question": "Question 1?", "options": ["a", "b"], "vt": 100})
    runs = await feed(build_keeper(saver, store), "autopilot", ROWS)
    prefs = await prefs_all(st)
    assert prefs["decision"]["n"] == 2 and abs(prefs["decision"]["mean"] - 0.30) < 1e-6 and prefs["vibe_check"]["mean"] < 0.08
    lessons = {x["id"]: x for x in await lessons_search(st, None, limit=10)}
    assert set(lessons) == {"decision:up", "vibe_check:down"}
    assert "30%" in lessons["decision:up"]["text"] and lessons["decision:up"]["evidence"]["n"] == 2
    assert [bool(v["learned"]["lesson"]) for v, _, _ in runs] == [False, False, False, True, True], "no lesson before there is an average worth comparing against"
    assert all(asked == [] for _, _, asked in runs), "outside manual mode memory is written without asking"
    entry = next(e for e in await ledger_list(st, "m1") if e["id"] == "p1")
    assert entry["participation"] == 0.31 and entry["winner"] == "a", "the ledger entry is stamped with how the poll did"
    assert len(await poll_results(st, "m1")) == 5
    hit = (await lessons_search(st, "a disputed VAR offside call", limit=1))[0]
    assert hit["id"] == "decision:up", "lessons are recalled by meaning"
    assert await memory_reset(st) >= 5 and await prefs_all(st) == {}


async def test_in_manual_mode_a_human_decides_what_is_remembered(saver, store):
    runs = await feed(build_keeper(saver, store), "manual", ROWS, chooser=lambda p: {"decision": "discard"} if p["lesson"]["direction"] == "down" else {"decision": "keep"})
    asked = [a for _, _, a in runs if a]
    assert len(asked) == 2 and asked[0][0]["kind"] == "memory_write" and asked[0][0]["choices"] == ["keep", "discard"]
    lessons = [x["id"] for x in await lessons_search(GraphStore(store), None, limit=10)]
    assert lessons == ["decision:up"], "the discarded lesson was not written"
    assert "memory.lesson" in kinds(runs[3][1]) and "memory.lesson" not in kinds(runs[4][1])


async def test_a_replayed_poll_result_is_not_counted_twice(saver, store):
    graph = build_keeper(saver, store)
    for thread in ("k-a", "k-b"):
        await graph.ainvoke({"match_id": "m1", "mode": "autopilot", "poll_result": result(1, "decision", 0.3)}, cfg(thread))
    assert (await prefs_all(GraphStore(store)))["decision"]["n"] == 1
    p = fold_pref(None, template="t", participation=0.2, sample="s1")
    assert fold_pref(p, template="t", participation=0.9, sample="s1") == p and fold_pref(p, template="t", decision="rejected")["rejected"] == 1
    out = await graph.ainvoke({}, cfg("k-studio"))
    assert out["learned"]["template"] == "decision", "a blank Studio form runs on a sample poll"


async def test_semantic_search_finds_a_poll_that_was_already_asked(store):
    st = GraphStore(store)
    await ledger_put(st, "m2", {"id": "a", "kind": "poll", "topic": "winner:1-1:0", "status": "published", "question": "Who wins from here?", "options": ["Home", "Draw", "Away"], "vt": 1})
    await ledger_put(st, "m2", {"id": "b", "kind": "highlight", "topic": "highlight:e1", "status": "published", "title": "12' GOAL", "vt": 2})
    hits = await ledger_similar(st, "m2", "Who wins from here?")
    assert hits[0]["id"] == "a" and hits[0]["score"] > 0.72 and all(h["id"] != "b" for h in hits)
    far = await ledger_similar(st, "m2", "Was the red card for Dragovic deserved?")
    assert not far or far[0]["score"] < 0.72


async def test_the_full_time_recap_remembers_the_half_time_one(saver, store):
    st = GraphStore(store)
    for i, _part in enumerate((0.31, 0.12)):
        await ledger_put(st, "m3", {"id": f"p{i}", "kind": "poll", "topic": f"t{i}", "status": "published", "question": f"Question {i}?", "options": ["a", "b"], "vt": i})
    await build_keeper(saver, store).ainvoke({"match_id": "m3", "mode": "autopilot", "poll_result": result(0, "decision", 0.31)}, cfg("k-recap"))
    graph = build_recap(saver, store)
    ht = (await graph.ainvoke({"match_id": "m3", "kind": "halftime"}, cfg("recap-m3")))["recap"]
    ft = (await graph.ainvoke({"match_id": "m3", "kind": "fulltime"}, cfg("recap-m3")))["recap"]
    assert ht["text"].startswith("Half-time: Harbour City 1-1") and "At the break" not in ht["text"]
    assert ft["text"].startswith("Full-time: Harbour City 2-1") and "At the break it was Harbour City 1-1" in ft["text"], "`previous` carried the half-time recap"
    assert ft["desk"]["published"]["poll"] == 2 and ft["desk"]["best_poll"]["question"] == "Question 0?" and "31%" in ft["text"]
    assert "Key moments" not in ft["text"] and len(ft["text"]) < 330 and ft["moments"], "the voiced recap is posted in the chat: short text, the moments travel as data"
    again = (await graph.ainvoke({"match_id": "m3", "kind": "fulltime"}, cfg("recap-m3")))["recap"]
    assert again["text"] == ft["text"], "re-running full-time does not pile up memos"


async def test_ask_the_producer_what_it_did_and_why(saver, store):
    st = GraphStore(store)
    await ledger_put(st, "m4", {"id": "a", "kind": "poll", "topic": "red:e1", "status": "published", "question": "Red card: deserved?", "minute": "63'", "reason": "autopilot: confidence 0.9", "vt": 1})
    await ledger_put(st, "m4", {"id": "skip:x", "kind": "poll", "topic": "next_goal:1", "status": "suppressed", "question": "Who scores next?", "minute": "70'", "reason": "a poll is already live", "vt": 2})
    await build_keeper(saver, store).ainvoke({"match_id": "m4", "mode": "autopilot", "poll_result": result(1, "red_card", 0.27)}, cfg("k-chat"))
    agent = build_chat(saver, store)

    async def ask(q: str, thread: str) -> list:
        out = await agent.ainvoke({"messages": [HumanMessage(q)]}, cfg(thread), context={"match_id": "m4"})
        return out["messages"]

    done = await ask("What have you done so far?", "c1")
    assert done[1].tool_calls[0]["name"] == "actions_taken" and done[1].tool_calls[0]["args"] == {"status": "published"}
    assert "Red card: deserved?" in done[-1].text and "63'" in done[-1].text
    why = await ask("Why didn't you run a next-goal poll?", "c2")
    assert why[1].tool_calls[0]["args"] == {"status": "suppressed"} and "a poll is already live" in why[-1].text
    perf = await ask("Which polls are performing best?", "c3")
    assert perf[1].tool_calls[0]["name"] == "poll_performance" and "red_card: 27%" in perf[-1].text
    learned = await ask("What lessons have you learned?", "c4")
    assert learned[1].tool_calls[0]["name"] == "lessons_learned" and "No lessons written down yet" in learned[-1].text
    lost = await agent.ainvoke({"messages": [HumanMessage("What have you done?")]}, cfg("c5"))
    assert "No match selected" in lost["messages"][-1].text
