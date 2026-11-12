"""Nodes of the `action_desk` graph: FLOW 2, generate actions.

    recall -> propose -> [poll_studio] -> screen -> handle x N (Send) -> finish

`recall` reads what the producer has already done this match and what it has learned across matches (the Store).
`propose` turns the curator's rundown into candidate actions: a poll brief for the studio, plus highlights, a pin and
host messages. `screen` applies the policy to each one: publish, ask a human, or suppress with a reason.

`handle` runs once per action, in parallel (Send), and is the only node with an `interrupt()`. Each action that needs
a human gets its OWN interrupt, so three suggestions can sit in the inbox and be answered in any order, by id; each
one publishes the moment it is approved, without waiting for the others. Above the `interrupt()` everything is
idempotent (a resumed node re-runs from line 1); the publish and the ledger write come after it.
"""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime
from langgraph.types import Send, interrupt

from gantry.common.rng import stable_int
from gantry.common.stream import emit
from gantry.engine import policy, polls
from gantry.flows.action_desk.state import DeskState, HandleInput
from gantry.memory.ledger import GraphStore, ledger_list, ledger_put, ledger_similar, lessons_search, prefs_all, prefs_update
from gantry.models.factory import voice

LABELS = {"goal": "GOAL", "card.red": "RED CARD", "penalty.awarded": "PENALTY", "penalty.saved": "PENALTY SAVED", "penalty.missed": "PENALTY MISSED",
          "goal.disallowed": "NO GOAL (VAR)", "var.no_penalty": "NO PENALTY (VAR)", "shot.woodwork": "OFF THE WOODWORK", "save.big": "BIG SAVE",
          "chance.big": "BIG CHANCE", "match.fulltime": "FULL TIME", "var.check": "VAR CHECK"}
PUBLIC_KEYS = ("id", "kind", "topic", "template", "question", "options", "duration_s", "text", "title", "comment", "event_id", "event_vt", "priority",
               "expected", "confidence", "risk", "expires_vt", "why", "writer", "judge", "runners_up", "window_id", "vt", "minute", "preempt")


def _k(state: DeskState, *parts: Any) -> str:
    return ":".join(str(p) for p in (state["window"]["id"], "desk", *parts))


def public_action(a: dict[str, Any]) -> dict[str, Any]:
    return {k: a[k] for k in PUBLIC_KEYS if a.get(k) is not None}


# ---------------------------------------------------------------------------------------------------- defaults
async def studio_defaults(state: DeskState) -> dict[str, Any]:
    """Studio's blank form: cut a sample window and curate it with the engine alone, so the desk has a rundown."""
    if state.get("rundown") and state.get("window"):
        return {}
    from gantry.engine import ranking
    from gantry.flows.defaults import complete_window

    update, filled = complete_window(dict(state))
    w = update.get("window") or state["window"]
    ms = update.get("match_state") or state["match_state"]
    moments = ranking.rank_moments(w["events"], w["timeline"], w["comments"], w["chat"]["baseline_per_min"])
    seen: dict[str, int] = {}
    lex = ranking.build_lexicon(moments, ms)
    scored = ranking.dedupe_pass([ranking.score_comment(c, lex, seen) for c in w["comments"]])
    short = ranking.shortlist(scored)
    headline = next((m for m in moments if m["tier"] != "routine"), None)
    update["rundown"] = {
        "window_id": w["id"], "to_vt": w["to_vt"], "moments": moments[:6], "headline": headline, "themes": ranking.find_themes(scored), "heat": 1.0, "note": "",
        "comments": {"highlights": short[:3], "pin": short[0] if short and short[0]["score"] >= 0.74 else None, "hidden": [], "collapsed": [], "total": len(scored)},
    }
    emit("note", f"{w['id']}:desk:defaults", agent="defaults", text="Filled in what was left blank: " + ", ".join([*filled, "an engine-only rundown"]) + ".")
    return update


# ------------------------------------------------------------------------------------------------------ recall
async def recall(state: DeskState, runtime: Runtime[Any]) -> dict[str, Any]:
    """What have I already done this match, what does this audience vote on, and what have I learned? (Store reads)"""
    st = GraphStore(runtime.store)
    ledger = await ledger_list(st, state["match_id"])
    prefs = await prefs_all(st)
    headline = (state.get("rundown") or {}).get("headline")
    lessons = await lessons_search(st, headline["text"] if headline else "a quiet spell in the match", limit=3)
    polls_done = [e for e in ledger if e.get("kind") == "poll" and e.get("status") in ("published", "pending")]
    emit("memory.recalled", _k(state, "recalled"), agent="memory",
         ledger={s: sum(1 for e in ledger if e.get("status") == s) for s in ("published", "pending", "rejected", "expired", "suppressed")},
         polls=[{"question": e.get("question"), "status": e.get("status"), "minute": e.get("minute"), "participation": e.get("participation")} for e in polls_done[-4:]],
         prefs=sorted(({"template": t, "n": p["n"], "mean": p["mean"]} for t, p in prefs.items() if p.get("n")), key=lambda p: -p["mean"])[:5],
         lessons=[{"text": x.get("text"), "score": x.get("score")} for x in lessons])
    return {"memory": {"ledger": ledger[-120:], "prefs": prefs, "lessons": lessons}}


# ----------------------------------------------------------------------------------------------------- propose
def _topic_open(topic: str, template: str | None, ledger: list[dict[str, Any]]) -> bool:
    """Cheap pre-filter so the studio never writes a poll the policy is certain to refuse."""
    done = [e for e in ledger if e.get("status") in ("published", "pending")]
    if any(e.get("topic") == topic for e in done) or any(e.get("topic") == topic and e.get("status") == "rejected" for e in ledger):
        return False
    used = sum(1 for e in done if e.get("kind") == "poll" and e.get("template") == template)
    return used < policy.MAX_PER_TEMPLATE.get(template or "", policy.DEFAULT_MAX_PER_TEMPLATE)


async def propose(state: DeskState) -> dict[str, Any]:
    rundown, ms, w, mem = state["rundown"], state["match_state"], state["window"], state["memory"]
    wid, now, ledger = w["id"], w["to_vt"], mem["ledger"]
    done = [e for e in ledger if e.get("status") in ("published", "pending")]
    used: dict[str, int] = {}
    for e in done:
        if e.get("kind") == "poll":
            used[e["template"]] = used.get(e["template"], 0) + 1

    proposals: list[dict[str, Any]] = []
    for m in rundown["moments"]:
        if m["tier"] == "headline" and m["type"] in LABELS:
            proposals.append({"kind": "highlight", "topic": f"highlight:{m['event_id']}", "event_id": m["event_id"], "event_vt": m["vt"], "minute": m["minute"],
                              "title": f"{m['minute']} · {LABELS[m['type']]}", "text": m["text"], "priority": m["score"], "risk": "low", "expires_vt": m["vt"] + 900})
        if m["type"] == "injury" and m.get("player"):
            line = await voice("the channel's host, warm and brief", f"Wishing {m['player']['name']} a speedy recovery. We'll bring you news as soon as we have it.", tag="host", words=24)
            proposals.append({"kind": "host_message", "topic": f"host:injury:{m['event_id']}", "event_id": m["event_id"], "event_vt": m["vt"], "minute": m["minute"],
                              "text": line, "priority": 0.86, "risk": "low", "expires_vt": m["vt"] + 240})
        if m["type"] == "goal" and any(x["label"] == "a hat-trick" for x in m["why"]) and m.get("player"):
            line = await voice("the channel's host, delighted", f"That's a hat-trick for {m['player']['name']}: three goals, the third at {m['minute']}. Take a bow.", tag="host", words=24)
            proposals.append({"kind": "host_message", "topic": f"host:hattrick:{m['event_id']}", "event_id": m["event_id"], "event_vt": m["vt"], "minute": m["minute"],
                              "text": line, "priority": 0.7, "risk": "low", "expires_vt": m["vt"] + 240})
    pin = rundown["comments"].get("pin")
    if pin:
        proposals.append({"kind": "pin_comment", "topic": f"pin:{stable_int(pin.get('key') or pin['text']) % 10**9}", "comment": {"id": pin["id"], "author": pin.get("author"), "text": pin["text"]},
                          "text": pin["text"], "duration_s": 90, "priority": pin["score"], "risk": "low", "expires_vt": now + 180, "minute": ms["minute"]})
    proposals = [{**p, "id": f"{wid}:{p['kind']}:{p['topic']}", "window_id": wid, "vt": now} for p in proposals if _topic_open(p["topic"], None, ledger)]

    cands = polls.plan(rundown["moments"], rundown["themes"], ms, w["trigger"], now, prefs=mem["prefs"], used=used, limit=5)
    open_ = [c for c in cands if _topic_open(c["topic"], c["template"], ledger)]
    viable = [c for c in open_ if not c["weak"]][:3]
    if not viable and open_:  # nothing worth the slot: say so in the ledger, with memory's reason
        c = open_[0]
        p = mem["prefs"].get(c["template"]) or {}
        if c["fatigue"] and c["value"] + c["fatigue"] >= polls.VALUE_FLOOR:
            why = "asked this kind of question already: not worth repeating yet"
        elif p.get("n") and c["expected"] < 0.45:
            why = f"memory: this audience rarely votes on this kind of poll ({p['mean']:.0%} of viewers over {p['n']} tries)"
        else:
            why = "not worth interrupting the chat for"
        proposals.append({**polls.baseline_draft(c, "terrace", revision=1), "kind": "poll", "topic": c["topic"], "template": c["template"], "priority": c["priority"],
                          "expected": c["expected"], "risk": c["risk"], "duration_s": c["duration_s"], "expires_vt": c["expires_vt"], "minute": ms["minute"],
                          "id": f"{wid}:poll:{c['topic']}", "window_id": wid, "vt": now, "presuppress": why})
    fx = ms["fixture"]
    brief = None
    if viable:
        brief = {"candidates": viable, "avoid": [e["question"] for e in done if e.get("kind") == "poll" and e.get("question")], "lessons": mem["lessons"],
                 "match": {"fixture": f"{fx['home']['name']} v {fx['away']['name']}", "minute": ms["minute"], "score": ms["score"]}}
    for p in proposals:
        emit("action.proposed", _k(state, "proposed", p["topic"]), agent="desk", kind=p["kind"], topic=p["topic"], title=p.get("title"), text=p.get("text"), priority=p["priority"])
    for c in cands:
        emit("action.proposed", _k(state, "proposed", c["topic"]), agent="desk", kind="poll", topic=c["topic"], template=c["template"], priority=c["priority"],
             expected=c["expected"], value=c["value"], viable=c in viable)
    return {"proposals": proposals, "poll_brief": brief, "poll": None}


def route_after_propose(state: DeskState) -> str:
    return "poll_studio" if state.get("poll_brief") else "screen"


# ------------------------------------------------------------------------------------------------------ screen
async def screen(state: DeskState, runtime: Runtime[Any]) -> dict[str, Any]:
    """Policy on every candidate action: publish, ask, or suppress, each with a reason."""
    st = GraphStore(runtime.store)
    w, ms, mem = state["window"], state["match_state"], state["memory"]
    wid, now, mode = w["id"], w["to_vt"], state.get("mode") or "manual"
    actions = list(state.get("proposals") or [])
    poll = state.get("poll")
    if poll:
        actions.append({**poll, "kind": "poll", "id": f"{wid}:poll:{poll['topic']}", "window_id": wid, "vt": now, "minute": ms["minute"]})
    world = {**ms, "recent_injuries": [{"vt": e["vt"]} for e in w["timeline"] if e["type"] == "injury"]}
    screened: list[dict[str, Any]] = []
    for a in actions:
        similar = await ledger_similar(st, state["match_id"], a["question"]) if a["kind"] == "poll" and not a.get("presuppress") else []
        v = policy.screen(a, mode=mode, now_vt=now, state=world, ledger=mem["ledger"], similar=similar)
        a = {**a, "verdict": v["verdict"], "why": v["reason"], "confidence": v["confidence"], "preempt": bool(v.get("preempt")), "retry_vt": v.get("retry_vt")}
        screened.append(a)
        emit("action.screened", _k(state, "screened", a["topic"]), agent="policy", id=a["id"], kind=a["kind"], topic=a["topic"], verdict=v["verdict"], reason=v["reason"],
             confidence=v["confidence"], question=a.get("question"), text=a.get("text"))
    return {"screened": screened}


def dispatch(state: DeskState) -> list[Send] | str:
    screened = state.get("screened") or []
    if not screened:
        return "finish"
    w = state["window"]
    return [Send("handle", {"action": a, "match_id": state["match_id"], "window_id": w["id"], "now_vt": w["to_vt"]}) for a in screened]


# ------------------------------------------------------------------------------------------------------ handle
def clean_decision(raw: Any, action: dict[str, Any]) -> dict[str, Any]:
    """Validate a resume value. Anything that is not a clear approval is a rejection: garbage never publishes."""
    if isinstance(raw, str):
        raw = {"decision": raw}
    if not isinstance(raw, dict):
        return {"decision": "reject", "by": "human", "note": "unreadable decision"}
    decision = raw.get("decision")
    by = "timeout" if decision == "expire" else str(raw.get("by") or "human")
    if decision not in ("approve", "edit", "reject", "expire"):
        return {"decision": "reject", "by": by, "note": "unreadable decision"}
    out: dict[str, Any] = {"decision": decision, "by": by, "note": str(raw.get("note") or "")[:200]}
    if decision == "edit":
        edits: dict[str, Any] = {}
        if action["kind"] == "poll":
            try:
                edits = polls.validate({"question": raw.get("question") or action["question"], "options": raw.get("options") or action["options"]}, {"allowed_options": None})
            except ValueError:
                edits = {}
        elif isinstance(raw.get("text"), str) and raw["text"].strip():
            edits = {"text": raw["text"].strip()[:240]}
        if not edits or all(action.get(k) == v for k, v in edits.items()):
            out["decision"] = "approve"  # an edit that changes nothing (or is invalid) is an approval of the original
        else:
            out["edits"] = edits
    return out


def _record(a: dict[str, Any], status: str, by: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {**public_action(a), "status": status, "by": by, "reason": reason, **extra}


async def handle(payload: HandleInput, runtime: Runtime[Any]) -> dict[str, Any]:
    """One action, start to finish. The only node that interrupts; everything above the interrupt is idempotent."""
    st = GraphStore(runtime.store)
    a, match_id = payload["action"], payload["match_id"]
    key = f"{payload['window_id']}:desk:{a['kind']}:{a['topic']}"

    if a["verdict"] == "suppress":
        rec = _record(a, "suppressed", "policy", a["why"])
        await ledger_put(st, match_id, {**rec, "id": f"skip:{a['topic']}", "action_id": a["id"]})  # one row per topic, latest reason wins
        retry = a.get("retry_vt") if a.get("retry_vt") and (a.get("expires_vt") is None or a["retry_vt"] < a["expires_vt"]) else None
        emit("action.closed", f"{key}:closed", agent="policy", id=a["id"], kind=a["kind"], topic=a["topic"], status="suppressed", reason=a["why"],
             question=a.get("question"), text=a.get("text"), retry_vt=retry)  # retry_vt: "come back to this when the slot is free"
        return {"actions": [rec]}

    by, edited = "autopilot", False
    if a["verdict"] == "ask":
        await ledger_put(st, match_id, _record(a, "pending", "policy", a["why"]))  # the next window must know one is waiting
        emit("action.pending", f"{key}:pending", agent="desk", action=public_action(a))
        raw = interrupt({"kind": "approve_action", "action": public_action(a), "choices": ["approve", "edit", "reject"], "expires_vt": a.get("expires_vt")})
        d = clean_decision(raw, a)  # ---- nothing above this line may have a non-idempotent effect ----
        by = d["by"]
        if d["decision"] in ("reject", "expire"):
            status = "rejected" if d["decision"] == "reject" else "expired"
            rec = _record(a, status, by, d["note"] or ("rejected by a human" if status == "rejected" else "nobody answered before the moment passed"))
            await ledger_put(st, match_id, rec)
            if a["kind"] == "poll" and status == "rejected":
                await prefs_update(st, a["template"], decision="rejected")
            emit("action.closed", f"{key}:closed", agent="desk", id=a["id"], kind=a["kind"], topic=a["topic"], status=status, reason=rec["reason"], question=a.get("question"), text=a.get("text"))
            return {"actions": [rec]}
        if d["decision"] == "edit":
            a, edited = {**a, **d["edits"]}, True

    rec = _record(a, "published", by, a["why"] if by == "autopilot" else ("edited and approved by a human" if edited else "approved by a human"), edited=edited)
    emit("action.publish", f"{key}:publish", agent="desk", action=public_action(a), by=by, edited=edited)  # the runtime applies this to the broadcast
    await ledger_put(st, match_id, rec)
    if a["kind"] == "poll":
        await prefs_update(st, a["template"], decision="auto" if by == "autopilot" else "edited" if edited else "approved")
    emit("memory.written", f"{key}:written", agent="memory", ns=f"gantry/ledger/{match_id}", key=a["id"], status="published")
    return {"actions": [rec]}


def finish(state: DeskState) -> dict[str, Any]:
    acts = state.get("actions") or []
    counts = {s: sum(1 for a in acts if a["status"] == s) for s in ("published", "rejected", "expired", "suppressed")}
    label = ", ".join(f"{n} {s}" for s, n in counts.items() if n) or "nothing to do"
    emit("progress", _k(state, "progress", "done"), agent="desk", label=f"Action desk: {label}", pct=100)
    return {"actions": []}  # the reducer keeps what the workers wrote; this guarantees the output key exists
