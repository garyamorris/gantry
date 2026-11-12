"""Flow 2, the pure part: should this action go out, wait for a human, or not happen at all?

`screen()` is the only place that decides. It reads the candidate action, the scoreboard, and what MEMORY says the
producer has already done this match (the ledger), and returns a verdict with a reason a human can read:

    publish   go, without asking (only ever in assisted/autopilot mode, only when confident and low-risk)
    ask       a human approves, edits or rejects (an `interrupt()` in the action_desk graph)
    suppress  do nothing, and say why ("a poll is already live", "you rejected this topic", "asked 3 minutes ago")

The rules are what make it safe to leave running: one poll at a time, a breathing gap between polls, never the same
question twice, back off what the human rejected, stay quiet around an injury, and never act on a stale moment.
"""

from __future__ import annotations

from typing import Any

MODES = ("manual", "assisted", "autopilot")
POLL_GAP = 240  # virtual seconds between one poll closing and the next opening
POLL_GAP_BIG = 75  # ...unless the moment is huge
BIG = 0.66  # priority (moment score x template fit) at which a poll may jump the queue
MAX_PER_TEMPLATE = {"winner_from_here": 3, "goal_rating": 3, "next_goal": 3}
DEFAULT_MAX_PER_TEMPLATE = 2
DUPLICATE_SIM = 0.72  # Store similarity above which a new question counts as "already asked"
AUTOPILOT_CONFIDENCE = 0.58
AUTOPILOT_CONFIDENCE_RISKY = 0.72
INJURY_HUSH = 300
PIN_GAP = 420
PREEMPTABLE = 0.6  # a live poll below this priority may be closed early for a BIG moment
PREEMPT_AFTER = 25  # ...once it has had this long
HOST_GAP = 240


def confidence(action: dict[str, Any]) -> float:
    """How sure the producer is, from the three things it knows: how big the moment is, how good the wording is, and
    whether this audience has liked this kind of thing before."""
    c = 0.45 * action.get("priority", 0.4) + 0.3 * action.get("judge", 0.8) + 0.25 * action.get("expected", 0.5)
    return round(max(0.0, min(1.0, c + 0.12)), 3)


def _verdict(v: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"verdict": v, "reason": reason, **extra}


def _by_mode(mode: str, action: dict[str, Any], conf: float) -> dict[str, Any]:
    kind, risk = action["kind"], action.get("risk", "low")
    if mode == "manual":
        return _verdict("ask", "manual mode: everything is approved by a human", confidence=conf)
    if kind == "poll":
        if mode == "assisted":
            return _verdict("ask", "assisted mode: polls are approved by a human", confidence=conf)
        if risk == "high":
            return _verdict("ask", "high-risk topic: a human decides", confidence=conf)
        need = AUTOPILOT_CONFIDENCE_RISKY if risk == "medium" else AUTOPILOT_CONFIDENCE
        if conf < need:
            return _verdict("ask", f"confidence {conf:.2f} is under the {need:.2f} needed to publish a {risk}-risk poll unattended", confidence=conf)
        return _verdict("publish", f"autopilot: confidence {conf:.2f}, {risk} risk", confidence=conf)
    return _verdict("publish", f"{mode} mode: {kind.replace('_', ' ')}s go out on their own", confidence=conf)


def screen(action: dict[str, Any], *, mode: str, now_vt: int, state: dict[str, Any], ledger: list[dict[str, Any]], similar: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Decide one action. `ledger` = this match's entries so far (newest last); `similar` = Store hits for a poll's question."""
    kind = action["kind"]
    conf = confidence(action)
    live = state.get("live") or {}
    if action.get("presuppress"):
        return _verdict("suppress", action["presuppress"], confidence=conf)
    done = [e for e in ledger if e.get("status") in ("published", "pending")]
    if action.get("expires_vt") is not None and now_vt >= action["expires_vt"]:
        return _verdict("suppress", "the moment has passed", confidence=conf)
    if any(e.get("topic") == action["topic"] for e in done):
        return _verdict("suppress", "already done this match", confidence=conf)
    if any(e.get("topic") == action["topic"] and e.get("status") == "rejected" for e in ledger):
        return _verdict("suppress", "a human rejected this topic earlier", confidence=conf)

    if kind == "poll":
        hush = [e for e in state.get("recent_injuries", []) if now_vt - e["vt"] < INJURY_HUSH]
        if hush and action.get("template") in ("vibe_check", "miss", "sub_verdict", "next_goal"):
            return _verdict("suppress", "a player is being treated: not the moment for a fun poll", confidence=conf)
        polls = [e for e in done if e.get("kind") == "poll"]
        big = action.get("priority", 0) >= BIG
        need = POLL_GAP_BIG if big else POLL_GAP
        active = live.get("active_poll")
        preempt = False
        if active:
            small = float(active.get("priority") or 0.5) < PREEMPTABLE and now_vt - int(active.get("opened_vt") or now_vt) >= PREEMPT_AFTER
            if not (big and small):
                return _verdict("suppress", "a poll is already live", confidence=conf, retry_vt=int(active["closes_vt"]) + need)
            preempt = True  # a big moment does not wait for "rate this half" to finish: close it early
        if any(e.get("status") == "pending" for e in polls) and action.get("priority", 0) < BIG:
            return _verdict("suppress", "another poll is still waiting for approval", confidence=conf)
        last_closed = live.get("last_poll_closed_vt")
        if last_closed is not None and not preempt:
            gap = now_vt - last_closed
            if gap < need:
                return _verdict("suppress", f"only {max(int(gap), 1)}s since the last poll closed (need {need}s)", confidence=conf, retry_vt=int(last_closed) + need)
        used = sum(1 for e in polls if e.get("template") == action.get("template"))
        cap = MAX_PER_TEMPLATE.get(action.get("template", ""), DEFAULT_MAX_PER_TEMPLATE)
        if used >= cap:
            return _verdict("suppress", f"asked this kind of question {used} times already", confidence=conf)
        for hit in similar or []:
            if (hit.get("score") or 0) >= DUPLICATE_SIM and hit.get("status") in ("published", "pending") and hit.get("topic") != action["topic"]:
                return _verdict("suppress", f"too similar to an earlier poll: “{hit.get('question', '')}”", confidence=conf, similar_to=hit.get("id"))
        v = _by_mode(mode, action, conf)
        return {**v, "preempt": True} if preempt and v["verdict"] != "suppress" else v
    elif kind == "pin_comment":
        last = max((e.get("vt", 0) for e in done if e.get("kind") == "pin_comment"), default=None)
        if last is not None and now_vt - last < PIN_GAP:
            return _verdict("suppress", "a comment was pinned moments ago", confidence=conf)
    elif kind == "host_message":
        last = max((e.get("vt", 0) for e in done if e.get("kind") == "host_message"), default=None)
        if last is not None and now_vt - last < HOST_GAP and action.get("priority", 0) < BIG:
            return _verdict("suppress", "the channel spoke recently", confidence=conf)
    return _by_mode(mode, action, conf)
