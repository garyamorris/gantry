"""The four graphs of the loop: curator, poll_studio, action_desk, showrunner. Offline, deterministic, in-memory."""

from __future__ import annotations

import pytest
from langgraph.types import Command

from gantry.common.stream import capture
from gantry.flows.action_desk.build import build as build_desk
from gantry.flows.action_desk.nodes import clean_decision
from gantry.flows.curator.build import build as build_curator
from gantry.flows.curator.nodes import EditorPicks, clean_picks
from gantry.flows.poll_studio.build import build as build_studio
from gantry.flows.showrunner.build import build as build_show
from gantry.live.windows import sample_window
from gantry.memory.ledger import GraphStore, ledger_list, prefs_all
from gantry.models import offline as o
from helpers import cfg, drive, kinds

APPROVE = lambda p: {"decision": "approve"}  # noqa: E731


# ------------------------------------------------------------------------------------------------- curator
async def test_curator_ranks_the_moment_and_curates_the_chat():
    with capture() as events:
        out = await build_curator().ainvoke(sample_window("derby", 7))
    r = out["rundown"]
    assert r["headline"]["type"] == "goal" and r["moments"][0]["score"] >= 0.95
    assert 1 <= len(r["comments"]["highlights"]) <= 3 and r["comments"]["hidden"], "the late winner's chat has both gold and spam in it"
    assert all(h["id"] in r["comments"]["shortlist"] for h in r["comments"]["highlights"])
    assert "90+3'" in r["note"] and r["editor"] == "model"
    ks = kinds(events)
    assert ks.count("chat.scored") == 2, "52 comments = two shards, scored in parallel (Send)"
    assert ks.index("moment.ranked") < ks.index("chat.scored") < ks.index("editor.pick") < ks.index("curation.result")
    result = next(e for e in events if e["kind"] == "curation.result")["data"]
    assert result["highlight"] == [h["id"] for h in r["comments"]["highlights"]] and len(result["hidden"]) == len(r["comments"]["hidden"])
    assert len({e["key"] for e in events}) == len(events), "event keys are unique within a run"


async def test_curator_fills_a_blank_studio_form_and_survives_an_empty_chat():
    with capture() as events:
        out = await build_curator().ainvoke({"fixture": "goalfest", "minute": 88})
    assert out["rundown"]["headline"]["type"] == "goal" and events[0]["kind"] == "note" and "goalfest" in events[0]["data"]["text"]
    w = sample_window("stalemate", 7, minute=10)
    w["window"]["comments"] = []
    out = await build_curator().ainvoke(w)
    assert out["rundown"]["comments"]["highlights"] == [] and out["rundown"]["comments"]["total"] == 0


async def test_the_editor_can_reorder_the_shortlist_but_never_leave_it():
    """A model that picks a hidden comment, an unknown id and a duplicate: only real shortlist ids survive."""
    w = sample_window("derby", 7)
    base = (await build_curator().ainvoke(w))["rundown"]
    last = base["comments"]["shortlist"][-1]
    hidden = base["comments"]["hidden"][0]["id"]
    o.SCRIPT_OVERRIDES["smart"] = o.Script([o.Rule("rogue", o.schema("EditorPicks"), lambda c: o.struct({"highlight_ids": [hidden, "nope", last, last], "pin_id": hidden}))])
    r = (await build_curator().ainvoke(w))["rundown"]
    assert [h["id"] for h in r["comments"]["highlights"]] == [last] and r["comments"]["pin"] is None
    assert clean_picks(EditorPicks(highlight_ids=["a", "a", "zz", "b", "c", "d"], pin_id="d"), [{"id": x} for x in "abcd"]) == (["a", "b", "c"], None)


async def test_a_failing_model_never_stops_the_curator():
    o.SCRIPT_OVERRIDES["smart"] = o.Script([o.Rule("down", lambda c: True, lambda c: o.fail(TimeoutError("provider down")))])
    o.SCRIPT_OVERRIDES["fast"] = o.Script([o.Rule("down", lambda c: True, lambda c: o.fail(TimeoutError("provider down")))])
    r = (await build_curator().ainvoke(sample_window("derby", 7)))["rundown"]
    assert r["editor"] == "fallback" and r["comments"]["highlights"] and r["note"], "the engine's own picks and note go out"


# --------------------------------------------------------------------------------------------- poll_studio
async def test_three_writers_one_judge_one_poll():
    with capture() as events:
        out = await build_studio().ainvoke({"fixture": "derby", "minute": 63})
    p = out["poll"]
    assert p["template"] == "red_card" and "Dragovic" in p["question"].title() and 2 <= len(p["options"]) <= 4 and p["judge"] >= 0.78 and p["rounds"] == 1
    drafts = [e["data"] for e in events if e["kind"] == "poll.draft"]
    assert sorted(d["writer"] for d in drafts) == ["analyst", "hype", "terrace"] and len(p["runners_up"]) == 2
    assert kinds(events)[-1] == "poll.final"


async def test_the_judge_sends_bad_drafts_back_with_notes_then_falls_back():
    """A model that always writes a long, leading question: two revision rounds with notes, then the engine's wording goes out."""
    seen_notes: list[list[str]] = []

    def wordy(c):
        import json

        facts = json.loads(c.human.split("FACTS (the only facts you may use):\n", 1)[1].split("\n\nA valid", 1)[0])
        seen_notes.append(facts["judge_notes"])
        return o.struct({"question": "Surely everyone watching agrees that was obviously a disgrace of a decision by the officials today, " * 2, "options": ["Yes", "No"]})

    o.SCRIPT_OVERRIDES["smart"] = o.Script([o.Rule("wordy", o.schema("PollDraft"), wordy)])
    with capture() as events:
        out = await build_studio().ainvoke({"fixture": "derby", "minute": 63})
    judged = [e["data"] for e in events if e["kind"] == "poll.judged"]
    assert [j["round"] for j in judged] == [0, 1, 2] and not any(j["passed"] for j in judged)
    assert seen_notes[0] == [] and any("characters" in n for n in seen_notes[-1]) and any("leads the voter" in n for n in seen_notes[-1])
    p = out["poll"]
    assert p["writer"] == "engine" and len(p["question"]) <= 90 and p["judge"] >= 0.78, "a plain poll on time beats a perfect one too late"


async def test_an_urgent_poll_is_never_revised_and_invalid_output_falls_back():
    o.SCRIPT_OVERRIDES["smart"] = o.Script([o.Rule("junk", o.schema("PollDraft"), lambda c: o.struct({"question": "", "options": ["only one"]}))])
    with capture() as events:
        out = await build_studio().ainvoke({"fixture": "derby", "minute": 71})
    assert out["poll"]["template"] == "penalty_outcome" and len(out["poll"]["options"]) == 3
    assert {e["data"]["source"] for e in events if e["kind"] == "poll.draft"} == {"fallback"}
    assert [e["data"]["round"] for e in events if e["kind"] == "poll.judged"] == [0]
    assert (await build_studio().ainvoke({"poll_brief": {"candidates": [], "avoid": []}}))["poll"] is None


# --------------------------------------------------------------------------------------------- action_desk
async def test_each_action_is_approved_on_its_own_by_id(saver, store):
    """Three suggestions pending at once; answering one publishes it immediately and leaves the others waiting."""
    graph = build_desk(saver, store)
    c = cfg("desk-1")
    with capture() as events:
        await graph.ainvoke({"fixture": "derby", "minute": 63, "mode": "manual"}, c)
        snap = await graph.aget_state(c)
        by_kind = {i.value["action"]["kind"]: i for i in snap.interrupts}
        assert set(by_kind) == {"poll", "highlight", "pin_comment"} and all(i.value["kind"] == "approve_action" for i in snap.interrupts)
        ledger = await ledger_list(GraphStore(store), "studio-derby-7")
        assert {e["status"] for e in ledger} == {"pending"}, "the next window must be able to see what is waiting"

        await graph.ainvoke(Command(resume={by_kind["poll"].id: {"decision": "approve"}}), c)
        published = [e["data"]["action"]["kind"] for e in events if e["kind"] == "action.publish"]
        assert published == ["poll"], "the poll goes out now, not when the others are answered"
        snap = await graph.aget_state(c)
        assert {i.value["action"]["kind"] for i in snap.interrupts} == {"highlight", "pin_comment"}

        await graph.ainvoke(Command(resume={by_kind["highlight"].id: {"decision": "reject", "note": "not now"}, by_kind["pin_comment"].id: {"decision": "expire"}}), c)
    snap = await graph.aget_state(c)
    assert not snap.interrupts and not snap.next
    status = {a["kind"]: (a["status"], a["by"]) for a in snap.values["actions"]}
    assert status == {"poll": ("published", "human"), "highlight": ("rejected", "human"), "pin_comment": ("expired", "timeout")}
    assert [e["data"]["action"]["kind"] for e in events if e["kind"] == "action.publish"] == ["poll"], "published exactly once across three resumes"
    ledger = {e["kind"]: e for e in await ledger_list(GraphStore(store), "studio-derby-7")}
    assert ledger["poll"]["status"] == "published" and ledger["highlight"]["reason"] == "not now"
    assert (await prefs_all(GraphStore(store)))["red_card"]["approved"] == 1


async def test_an_edit_is_validated_and_garbage_never_publishes(saver, store):
    graph = build_desk(saver, store)

    def chooser(p):
        if p["action"]["kind"] == "poll":
            return {"decision": "edit", "question": "Dragovic off: fair?", "options": ["Fair", "Harsh"]}
        return "banana"

    values, _events, _ = await drive(graph, {"fixture": "derby", "minute": 63, "mode": "manual"}, cfg("desk-2"), chooser)
    poll = next(a for a in values["actions"] if a["kind"] == "poll")
    assert poll["status"] == "published" and poll["edited"] and poll["question"] == "Dragovic off: fair?" and poll["options"] == ["Fair", "Harsh"]
    assert all(a["status"] == "rejected" for a in values["actions"] if a["kind"] != "poll")
    assert (await prefs_all(GraphStore(store)))["red_card"]["edited"] == 1
    a = {"kind": "poll", "question": "Q?", "options": ["a", "b"]}
    assert clean_decision({"decision": "edit", "question": "Q?", "options": ["a", "b"]}, a)["decision"] == "approve", "an edit that changes nothing is an approval"
    assert clean_decision({"decision": "edit", "options": ["only"]}, a)["decision"] == "approve", "an invalid edit keeps the original"
    for junk in (None, 7, [], {}, {"decision": "yes please"}):
        assert clean_decision(junk, a)["decision"] == "reject"


async def test_memory_stops_the_desk_repeating_itself(saver, store):
    """Same match, same moment, a second window: nothing is proposed twice, and the ledger says why."""
    graph = build_desk(saver, store)
    first, _, _ = await drive(graph, {"fixture": "derby", "minute": 63, "mode": "autopilot"}, cfg("desk-3a"), APPROVE)
    assert {a["kind"] for a in first["actions"] if a["status"] == "published"} >= {"poll", "highlight"}
    with capture() as events:
        second = await graph.ainvoke({"fixture": "derby", "minute": 63, "mode": "autopilot"}, cfg("desk-3b"))
    assert [a for a in second["actions"] if a["status"] == "published" and a["kind"] in ("highlight", "pin_comment")] == []
    again = [a for a in second["actions"] if a["kind"] == "poll"]
    assert all(a["topic"] != "red:e033" for a in again if a["status"] == "published"), "the red-card poll is never asked twice"
    recalled = next(e for e in events if e["kind"] == "memory.recalled")["data"]
    assert recalled["ledger"]["published"] >= 2 and recalled["polls"][0]["question"]


async def test_autopilot_publishes_what_it_is_sure_of_and_assisted_holds_the_poll(saver, store):
    auto = await build_desk(saver, store).ainvoke({"fixture": "derby", "minute": 71, "mode": "autopilot", "match_id": "m-auto"}, cfg("desk-4a"))
    assert {a["kind"]: a["status"] for a in auto["actions"]}["poll"] == "published" and all(a["by"] == "autopilot" for a in auto["actions"] if a["status"] == "published")
    graph = build_desk(saver, store)
    await graph.ainvoke({"fixture": "derby", "minute": 71, "mode": "assisted", "match_id": "m-assist"}, cfg("desk-4b"))
    snap = await graph.aget_state(cfg("desk-4b"))
    assert [i.value["action"]["kind"] for i in snap.interrupts] == ["poll"], "in assisted mode only the poll waits"


# ---------------------------------------------------------------------------------------------- showrunner
async def test_one_window_end_to_end_with_interrupts_bubbling_through_two_subgraphs(saver, store):
    graph = build_show(saver, store)
    values, events, asked = await drive(graph, sample_window("derby", 7, minute=63, mode="manual"), cfg("show-1"), APPROVE)
    ks = kinds(events)
    assert ks[0] == "window.open" and ks[-1] == "window.done"
    for a, b in (("moment.ranked", "curation.result"), ("curation.result", "memory.recalled"), ("memory.recalled", "poll.brief"), ("poll.final", "action.screened"), ("action.screened", "action.publish")):
        assert ks.index(a) < ks.index(b), f"{a} before {b}"
    assert len(asked) >= 2 and {a["action"]["kind"] for a in asked} >= {"poll", "highlight"} and values["summary"]["actions"]["published"] and not values["summary"]["skipped"]
    assert values["summary"]["headline"].startswith("RED CARD")


async def test_triage_skips_a_dead_tick_without_running_anything(saver, store):
    w = sample_window("stalemate", 7, minute=10, trigger={"kind": "tick", "event_id": None, "reason": "quiet spell"})
    w["window"]["events"], w["window"]["comments"] = [], w["window"]["comments"][:2]
    with capture() as events:
        out = await build_show(saver, store).ainvoke(w, cfg("show-2"))
    assert out["summary"]["skipped"] and kinds(events) == ["window.open", "window.skip"]


@pytest.mark.parametrize("inp", [{}, {"fixture": "goalfest"}, {"fixture": "stalemate", "minute": 67, "mode": "autopilot"}])
async def test_showrunner_runs_from_a_sparse_studio_form(saver, store, inp):
    values, events, _ = await drive(build_show(saver, store), inp, cfg(f"show-studio-{len(inp)}"), APPROVE)
    assert values["summary"]["window_id"] == "w001" and events[0]["kind"] == "note"
