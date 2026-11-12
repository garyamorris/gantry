"""Windows: the unit of work of the live loop.

A window is a self-contained JSON snapshot of a stretch of the broadcast: the feed events and chat messages that
arrived, the scoreboard at the end of it, and what set it off. The dispatcher builds one per trigger and hands it to
the `showrunner` graph as its whole input, so a graph run never reaches back into the live runtime: it can be
replayed, forked and inspected in Studio from its input alone.

`sample_window()` builds the same thing straight from the simulator (Studio's blank form, tests, evals).
"""

from __future__ import annotations

from typing import Any

from gantry.engine.ranking import KEY_TYPES
from gantry.sim import chat as chat_sim
from gantry.sim import match as match_sim

MAX_COMMENTS = 160
MAX_EVENTS = 40


def timeline_of(events: list[dict[str, Any]], to_vt: float) -> list[dict[str, Any]]:
    """The match's key events so far, compact: the context a moment is scored against."""
    return [
        {k: e.get(k) for k in ("id", "type", "vt", "clock", "minute", "team", "player", "player2", "score", "tags", "text")}
        for e in events
        if e["vt"] <= to_vt and (e["type"] in KEY_TYPES or e["type"] in ("substitution", "match.halftime"))
    ]


def build_window(
    *, match_id: str, seq: int, fixture: dict[str, Any], state: dict[str, Any], events: list[dict[str, Any]], comments: list[dict[str, Any]],
    all_events: list[dict[str, Any]], from_vt: float, to_vt: float, trigger: dict[str, Any], baseline_per_min: float, mode: str,
    live: dict[str, Any] | None = None, viewers: int = 10000,
) -> dict[str, Any]:
    """Assemble the showrunner's input. Everything passed in must already be public (no ground truth)."""
    comments = comments[-MAX_COMMENTS:]
    span_min = max((to_vt - from_vt) / 60.0, 0.25)
    return {
        "match_id": match_id,
        "mode": mode,
        "window": {
            "id": f"w{seq:03d}", "seq": seq, "from_vt": int(from_vt), "to_vt": int(to_vt), "trigger": trigger,
            "events": events[-MAX_EVENTS:], "comments": comments, "timeline": timeline_of(all_events, to_vt),
            "chat": {"count": len(comments), "per_min": round(len(comments) / span_min, 1), "baseline_per_min": round(baseline_per_min, 1)},
        },
        "match_state": {**state, "fixture": fixture, "viewers": viewers, "live": live or {"active_poll": None, "pinned": None, "last_poll_closed_vt": None}},
    }


def sample_window(fixture_id: str = "derby", seed: int = 7, *, to_vt: int | None = None, minute: int | None = None, span: int = 240,
                  mode: str = "manual", seq: int = 1, trigger: dict[str, Any] | None = None, live: dict[str, Any] | None = None) -> dict[str, Any]:
    """A window cut from the simulator. `minute` is a scoreboard minute (67 means 67'); default is just after the fixture's biggest moment."""
    match = match_sim.simulate(fixture_id, seed)
    tm = match["timing"]
    if to_vt is None and minute is not None:
        clock = max(0, minute - 1) * 60 + 59
        to_vt = clock if minute <= 45 else tm["p2_start_vt"] + (clock - 2700)
    if to_vt is None:
        top = max(match["events"], key=lambda e: (e["truth"]["importance"], e["vt"]))
        to_vt = top["vt"] + 30
    to_vt = int(max(30, min(to_vt, tm["end_vt"] + 60)))
    from_vt = max(0, to_vt - span)
    events = [match_sim.public(e) for e in match["events"] if from_vt < e["vt"] <= to_vt]
    all_events = [match_sim.public(e) for e in match["events"] if e["vt"] <= to_vt]
    chat = chat_sim.chat_for(match)
    comments = [chat_sim.public(c, now_vt=to_vt) for c in chat if from_vt < c["vt"] <= to_vt]
    seen = sum(1 for c in chat if c["vt"] <= to_vt)
    baseline = seen / max(to_vt / 60.0, 1.0)
    key = [e for e in events if e["type"] in KEY_TYPES]
    trig = trigger or ({"kind": "event", "event_id": key[-1]["id"], "reason": key[-1]["type"]} if key else {"kind": "tick", "event_id": None, "reason": "quiet spell"})
    return build_window(
        match_id=f"{fixture_id}-{seed}", seq=seq, fixture=match["fixture"], state=match_sim.state_at(match, to_vt), events=events, comments=comments,
        all_events=all_events, from_vt=from_vt, to_vt=to_vt, trigger=trig, baseline_per_min=baseline, mode=mode, live=live, viewers=match["viewers"],
    )
