"""Nodes of the `poll_studio` graph: THE FIRST FEATURE. A live moment in, one good poll out.

Three writers (an analyst, a hype voice, a voice from the terraces) draft the same poll in parallel; a judge scores
every draft against a rubric; drafts that fail go back to their writer with the judge's notes, for at most two more
rounds. The engine supplies the candidate (the topic, the facts, baseline wording) and the rules; the writers supply
the words. Whatever a model writes is validated before the judge sees it, and the baseline wording is the fallback.
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.types import Command, Send
from pydantic import BaseModel, Field

from gantry.common.rng import stable_int
from gantry.common.stream import emit
from gantry.engine import polls
from gantry.flows.poll_studio.state import StudioState, WriteInput
from gantry.models.factory import structured

MAX_ROUNDS = 2  # revision rounds after the first draft
VOICES = {
    "analyst": "a precise, neutral match analyst. Plain words, no emoji, no hype.",
    "hype": "the channel's hype voice. Short, loud, capitals and one emoji are welcome.",
    "terrace": "a fan in the stands talking to other fans. Warm, colloquial, a bit wry.",
}


class PollDraft(BaseModel):
    """A live poll: one question, two to four short options."""

    question: str = Field(description="The poll question, under 90 characters, neutral, answerable at a glance")
    options: list[str] = Field(description="Two to four distinct options, each under 26 characters")


def _k(cand: dict[str, Any], *parts: Any) -> str:
    return ":".join(str(p) for p in ("studio", cand["topic"], *parts))


async def studio_defaults(state: StudioState) -> dict[str, Any]:
    """Studio's blank form: plan a brief from a sample window (the same engine path the action desk uses)."""
    if state.get("poll_brief"):
        return {}
    from gantry.engine import ranking
    from gantry.flows.defaults import complete_window

    win, _ = complete_window(dict(state))
    w, ms = win["window"], win["match_state"]
    moments = ranking.rank_moments(w["events"], w["timeline"], w["comments"], w["chat"]["baseline_per_min"])
    cands = polls.plan(moments, [], ms, w["trigger"], w["to_vt"])
    fx = ms["fixture"]
    brief = {"candidates": cands, "avoid": [], "lessons": [],
             "match": {"fixture": f"{fx['home']['name']} v {fx['away']['name']}", "minute": ms["minute"], "score": ms["score"]}}
    emit("note", f"studio:defaults:{w['id']}", agent="defaults", text=f"Filled in what was left blank: a poll brief from the {fx['home']['name']} window at {ms['minute']}.")
    return {"poll_brief": brief}


def brief(state: StudioState) -> Command[Literal["write", "finalize"]]:
    """Pick the candidate to write (the engine ranked them) and hand it to all three writers at once."""
    b = state.get("poll_brief") or {}
    cands = b.get("candidates") or []
    if not cands:
        return Command(goto="finalize", update={"round": 0})
    cand = cands[0]
    emit("poll.brief", _k(cand, "brief"), agent="studio", topic=cand["topic"], template=cand["template"], priority=cand.get("priority"),
         expected=cand.get("expected"), value=cand.get("value"), alternatives=[c["topic"] for c in cands[1:]])
    sends = [Send("write", {"writer": w, "round": 0, "candidate": cand, "notes": [], "match": b.get("match") or {}, "lessons": [x.get("text", "") for x in b.get("lessons") or []]})
             for w in polls.WRITERS]
    return Command(goto=sends, update={"round": 0})


async def write(payload: WriteInput) -> dict[str, Any]:
    """One writer, one draft (the map step). On a revision round the judge's notes come with it."""
    cand, writer, rnd = payload["candidate"], payload["writer"], payload["round"]
    base = polls.baseline_draft(cand, writer, revision=rnd)
    facts = {"moment": cand["facts"], "match": payload["match"], "topic": cand["topic"], "must_mention": cand["mentions"],
             "options_must_come_from": cand.get("allowed_options"), "judge_notes": payload["notes"], "lessons_from_past_polls": payload["lessons"]}
    value, source = await structured(
        PollDraft, role="smart", draft=PollDraft(**base), facts=facts,
        system=(f"You write live polls for a sports stream's chat. You are {VOICES[writer]} "
                "Write ONE poll about the moment in FACTS: a question under 90 characters that a viewer can answer in two seconds, and two to four "
                "distinct options under 26 characters each. Be neutral: never lead the voter. Use only names and facts given. "
                "If judge_notes are present, fix exactly what they say."),
    )
    try:
        draft = polls.validate(value.model_dump(), cand)
    except ValueError:
        draft, source = polls.validate(base, cand), "fallback"
    emit("poll.draft", _k(cand, "draft", writer, rnd), agent=writer, writer=writer, round=rnd, source=source, **draft)
    return {"drafts": [{"writer": writer, "round": rnd, "source": source, **draft}]}


def judge(state: StudioState) -> Command[Literal["write", "finalize"]]:
    """Score this round's drafts (the reduce step, deferred until all writers are in).

    Live rules: the first passing draft ends it (a revision round costs seconds the moment does not have). Only when NO
    draft passes do the writers get the judge's notes and another go, and never for an urgent poll. If the rounds run
    out, the engine's baseline wording goes out instead: a plain poll on time beats a perfect one too late.
    """
    b = state["poll_brief"]
    cand, rnd = b["candidates"][0], state.get("round", 0)
    latest = {d["writer"]: d for d in state.get("drafts") or [] if d["round"] <= rnd}  # each writer's newest draft
    scored = [{**latest[w], **polls.rubric(latest[w], cand, b.get("avoid"))} for w in polls.WRITERS if w in latest]
    best = max(scored, key=lambda d: (d["score"], stable_int(cand["topic"], d["writer"])))
    emit("poll.judged", _k(cand, "judged", rnd), agent="judge", round=rnd, best=best["writer"], passed=best["passed"],
         scores=[{"writer": d["writer"], "score": d["score"], "passed": d["passed"], "notes": d["notes"]} for d in scored])
    urgent = cand.get("ttl_s", 999) <= 90
    if not best["passed"] and rnd < MAX_ROUNDS and not urgent:
        sends = [Send("write", {"writer": d["writer"], "round": rnd + 1, "candidate": cand, "notes": d["notes"], "match": b.get("match") or {},
                                "lessons": [x.get("text", "") for x in b.get("lessons") or []]}) for d in scored]
        return Command(goto=sends, update={"round": rnd + 1, "verdict": {"round": rnd, "best": best, "scored": scored}})
    if not best["passed"]:
        base = polls.validate(polls.baseline_draft(cand, "terrace", revision=1), cand)
        best = {"writer": "engine", "round": rnd, "source": "fallback", **base, **polls.rubric(base, cand, b.get("avoid"))}
        scored = [*scored, best]
    return Command(goto="finalize", update={"verdict": {"round": rnd, "best": best, "scored": scored}})


def finalize(state: StudioState) -> dict[str, Any]:
    verdict = state.get("verdict")
    if not verdict:
        return {"poll": None}
    cand = state["poll_brief"]["candidates"][0]
    best = verdict["best"]
    poll = {
        "question": best["question"], "options": best["options"], "writer": best["writer"], "judge": best["score"], "rounds": verdict["round"] + 1,
        "template": cand["template"], "topic": cand["topic"], "event_id": cand.get("event_id"), "event_vt": cand.get("event_vt"), "risk": cand["risk"],
        "duration_s": cand["duration_s"], "expires_vt": cand.get("expires_vt"), "priority": cand.get("priority", 0.5), "expected": cand.get("expected", 0.5),
        "runners_up": [{"writer": d["writer"], "question": d["question"], "options": d["options"], "score": d["score"]} for d in verdict["scored"] if d["writer"] != best["writer"]],
    }
    emit("poll.final", _k(cand, "final"), agent="studio", **{k: poll[k] for k in ("question", "options", "writer", "judge", "rounds", "template", "topic")})
    return {"poll": poll}
