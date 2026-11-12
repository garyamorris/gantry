"""Studio defaults: LangGraph Studio's form sends a field or two, not the window the live runtime builds.

Every graph starts with a `defaults` node that fills in ONLY what was left blank (a sample window cut from the
simulator, a curated rundown, a poll brief) and says so in a note. It never overrides what was sent and does nothing
when the live runtime sends everything. Try `{}` or `{"fixture": "goalfest", "minute": 88}` on any graph.
"""

from __future__ import annotations

from typing import Any

from gantry.live.windows import sample_window
from gantry.sim.fixtures import FIXTURES


def complete_window(state: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Returns (update, filled): a sample window when none was sent."""
    if state.get("window") and state.get("match_state"):
        return {}, []
    fixture = state.get("fixture") if state.get("fixture") in FIXTURES else "derby"
    seed = int(state.get("seed") or 7)
    minute = state.get("minute")
    w = sample_window(fixture, seed, minute=int(minute) if minute else None, mode=state.get("mode") or "manual")
    update = {"match_id": state.get("match_id") or f"studio-{fixture}-{seed}", "mode": state.get("mode") or "manual", "window": w["window"], "match_state": w["match_state"]}
    return update, [f"a sample window from the {fixture} fixture at {w['match_state']['minute']}"]
