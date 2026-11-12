"""Nodes of the `memory_keeper` graph: how the producer learns what works.

Runs every time a poll closes. `record` files the result. `learn` folds it into that template's running stats
(participation, across matches) and, when the evidence is strong enough, drafts a written lesson. In manual mode a
human approves what is remembered (a memory-write `interrupt`); otherwise it is written on the spot.

The stats are what change behaviour: `gantry.engine.polls.expected_engagement` reads them when the next candidates
are ranked, so a kind of poll this audience ignores sinks, and one it loves rises. Lessons are for the humans (and the
model, which is shown the relevant ones when it writes).
"""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from gantry.common.stream import emit
from gantry.flows.memory_keeper.state import KeeperState
from gantry.memory.ledger import GraphStore, ledger_patch, lesson_put, lessons_search, poll_result_put, prefs_all, prefs_update
from gantry.models.factory import voice

MIN_SAMPLES = 2  # polls of one template before a lesson may be drawn from it
MIN_TOTAL = 4  # polls overall before there is an average worth comparing against
MARGIN = 0.25  # a template must beat or trail the average by this much
NAMES = {
    "decision": "Polls on a disputed call (VAR, offside, a penalty shout)", "penalty_outcome": "“What happens next?” polls at a penalty",
    "red_card": "Red-card verdict polls", "winner_from_here": "“Who wins from here?” polls", "goal_rating": "Goal-rating polls",
    "next_goal": "“Who scores next?” polls", "vibe_check": "“Rate this half” polls", "motm": "Player-of-the-match polls",
    "potm_half": "Player-of-the-half polls", "second_half": "Second-half prediction polls", "sub_wish": "“Who should come on?” polls",
    "sub_verdict": "Substitution verdict polls", "keeper_save": "Save-rating polls", "hattrick_watch": "Hat-trick watch polls",
    "chat_split": "Polls that settle an argument in the chat", "miss": "“How did he miss?” polls",
}


def _k(state: KeeperState, *parts: Any) -> str:
    return ":".join(str(p) for p in ("keeper", state["poll_result"]["id"], *parts))


async def studio_defaults(state: KeeperState) -> dict[str, Any]:
    if state.get("poll_result"):
        return {}
    sample = {"id": "studio:poll:decision:e030", "topic": "decision:e030", "template": "decision", "question": "Okafor's goal ruled out for offside. Right call?",
              "options": ["✅ Correct", "❌ Wrong", "🤷 Too tight to call"], "votes": [2410, 2190, 880], "total": 5480, "viewers": 18400, "participation": 0.2978,
              "opened_vt": 3420, "closed_vt": 3510, "minute": "52'", "by": "autopilot"}
    emit("note", "keeper:defaults", agent="defaults", text="Filled in what was left blank: a sample closed poll.")
    return {"match_id": state.get("match_id") or "studio-derby-7", "mode": state.get("mode") or "manual", "poll_result": sample}


async def record(state: KeeperState, runtime: Runtime[Any]) -> dict[str, Any]:
    """File the result and stamp the ledger entry with how the poll actually did (Store writes, idempotent by poll id)."""
    st = GraphStore(runtime.store)
    r, match_id = state["poll_result"], state["match_id"]
    await poll_result_put(st, match_id, r)
    winner = r["options"][max(range(len(r["votes"])), key=lambda i: r["votes"][i])] if r.get("votes") else None
    await ledger_patch(st, match_id, r["id"], participation=r["participation"], votes=r["votes"], total=r["total"], winner=winner, closed_vt=r["closed_vt"])
    emit("memory.outcome", _k(state, "outcome"), agent="keeper", poll_id=r["id"], template=r["template"], question=r["question"], participation=r["participation"],
         total=r["total"], winner=winner)
    return {}


async def learn(state: KeeperState, runtime: Runtime[Any]) -> dict[str, Any]:
    """Fold the result into the template's stats; draft a lesson if this template now clearly beats or trails the rest."""
    st = GraphStore(runtime.store)
    r = state["poll_result"]
    pref = await prefs_update(st, r["template"], participation=r["participation"], sample=r["id"])
    prefs = await prefs_all(st)
    total_n = sum(p["n"] for p in prefs.values())
    average = round(sum(p["mean"] * p["n"] for p in prefs.values()) / max(total_n, 1), 4)
    emit("memory.pref", _k(state, "pref"), agent="keeper", template=r["template"], n=pref["n"], mean=pref["mean"], average=average)
    lesson = None
    if pref["n"] >= MIN_SAMPLES and total_n >= MIN_TOTAL and average > 0:
        gap = (pref["mean"] - average) / average
        if abs(gap) >= MARGIN:
            direction = "up" if gap > 0 else "down"
            lid = f"{r['template']}:{direction}"
            existing = [x for x in await lessons_search(st, None, limit=100) if x["id"] == lid]
            if not existing:
                name = NAMES.get(r["template"], f"“{r['template']}” polls")
                draft = (f"{name} pull {pref['mean']:.0%} of viewers on this channel, against an average of {average:.0%}. Reach for them when the moment allows."
                         if direction == "up" else
                         f"{name} only pull {pref['mean']:.0%} of viewers on this channel, against an average of {average:.0%}. Use them sparingly; prefer a poll tied to a moment.")
                text = await voice("the producer, writing a note for tomorrow's shift", draft, tag="lesson", words=45)
                lesson = {"id": lid, "template": r["template"], "direction": direction, "text": text, "evidence": {"n": pref["n"], "mean": pref["mean"], "average": average}}
    return {"pref": pref, "average": average, "lesson": lesson}


def route_after_learn(state: KeeperState) -> str:
    return "curate" if state.get("lesson") else "close"


async def curate(state: KeeperState) -> dict[str, Any]:
    """Memory-write review: in manual mode a human decides what the producer remembers. Garbage keeps nothing."""
    lesson = state["lesson"]
    assert lesson is not None
    if (state.get("mode") or "manual") != "manual":
        return {"keep": True}
    raw = interrupt({"kind": "memory_write", "lesson": lesson, "choices": ["keep", "discard"]})  # pure above this line
    keep = raw is True or (isinstance(raw, dict) and raw.get("decision") == "keep") or raw == "keep"
    return {"keep": bool(keep)}


async def close(state: KeeperState, runtime: Runtime[Any]) -> dict[str, Any]:
    st = GraphStore(runtime.store)
    lesson = state.get("lesson")
    written = None
    if lesson and state.get("keep"):
        await lesson_put(st, lesson)
        written = lesson
        emit("memory.lesson", _k(state, "lesson"), agent="keeper", **lesson)
    r = state["poll_result"]
    return {"learned": {"poll_id": r["id"], "template": r["template"], "participation": r["participation"], "pref": state.get("pref"), "average": state.get("average"), "lesson": written}}
