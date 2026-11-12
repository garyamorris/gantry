"""Nodes of the `showrunner` graph: THE LOOP. One live window, end to end.

    intake -> triage -> curate (subgraph: flow 1) -> act (subgraph: flow 2) -> wrap
                     \\-> skip

This is what the dispatcher runs on every trigger (a goal, a red card, a penalty, a quiet five minutes, half-time).
Each window is its own thread, so a suggestion waiting for a human never blocks the next window: the broadcast moves
on, and continuity between windows lives in the Store (the ledger), not in thread state.

`triage` is the cheap gate in front of the expensive part: on a live feed most ticks are noise, and the right
response to noise is to do nothing, quickly.
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.types import Command

from gantry.common.stream import emit
from gantry.engine.ranking import BASE
from gantry.flows.defaults import complete_window
from gantry.flows.showrunner.state import ShowState

QUIET_COMMENTS = 6


async def intake(state: ShowState) -> dict[str, Any]:
    update, filled = complete_window(dict(state))
    w = update.get("window") or state["window"]
    ms = update.get("match_state") or state["match_state"]
    if filled:
        emit("note", f"{w['id']}:show:defaults", agent="defaults", text="Filled in what was left blank: " + ", ".join(filled) + ".")
    emit("window.open", f"{w['id']}:show:open", agent="showrunner", window_id=w["id"], trigger=w["trigger"], minute=ms["minute"], score=ms["score"],
         events=len(w["events"]), comments=len(w["comments"]), mode=update.get("mode") or state.get("mode") or "manual", from_vt=w["from_vt"], to_vt=w["to_vt"])
    return update


def triage(state: ShowState) -> Command[Literal["curate", "skip"]]:
    """Is this window worth the full pipeline? Pure and instant: the biggest raw event, and whether the chat is alive."""
    w = state["window"]
    biggest = max((BASE.get(e["type"], 0.05) for e in w["events"]), default=0.0)
    kind = w["trigger"].get("kind")
    alive = len(w["comments"]) >= QUIET_COMMENTS and w["chat"]["per_min"] >= 0.6 * w["chat"]["baseline_per_min"]
    worth = kind in ("event", "urgent", "phase", "manual", "retry") or biggest >= 0.2 or alive
    reason = (f"triggered by {w['trigger'].get('reason') or kind}" if kind != "tick" else "a quiet spell, but the chat is alive") if worth else "nothing happened and the chat has gone quiet"
    return Command(goto="curate" if worth else "skip", update={"triage": {"worth": worth, "biggest": biggest, "reason": reason}})


def skip(state: ShowState) -> dict[str, Any]:
    w = state["window"]
    emit("window.skip", f"{w['id']}:show:skip", agent="showrunner", window_id=w["id"], reason=state["triage"]["reason"])
    return {"summary": {"window_id": w["id"], "skipped": True, "reason": state["triage"]["reason"]}}


def wrap(state: ShowState) -> dict[str, Any]:
    w, rd = state["window"], state.get("rundown") or {}
    acts = state.get("actions") or []
    by_status: dict[str, list[str]] = {}
    for a in acts:
        by_status.setdefault(a["status"], []).append(a.get("question") or a.get("title") or a.get("text") or a["topic"])
    summary = {
        "window_id": w["id"], "skipped": False, "trigger": w["trigger"], "minute": state["match_state"]["minute"],
        "headline": (rd.get("headline") or {}).get("text"), "note": rd.get("note"),
        "highlights": len((rd.get("comments") or {}).get("highlights") or []), "hidden": len((rd.get("comments") or {}).get("hidden") or []),
        "actions": by_status,
    }
    emit("window.done", f"{w['id']}:show:done", agent="showrunner", **summary)
    return {"summary": summary}
