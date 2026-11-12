from __future__ import annotations

from typing import Annotated, Any, TypedDict


def merge_actions(left: list[dict[str, Any]] | None, right: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Reducer: one record per action id, newest wins, first-seen order kept (the `handle` Send workers write here)."""
    out: dict[str, dict[str, Any]] = {a["id"]: a for a in left or []}
    for a in right or []:
        out[a["id"]] = {**out.get(a["id"], {}), **a}
    return list(out.values())


class DeskInput(TypedDict, total=False):
    match_id: str
    mode: str  # manual | assisted | autopilot
    window: dict[str, Any]
    match_state: dict[str, Any]
    rundown: dict[str, Any]  # the curator's output
    # Studio conveniences: leave everything blank and a sample window is curated first
    fixture: str
    seed: int
    minute: int


class DeskOutput(TypedDict, total=False):
    actions: Annotated[list[dict[str, Any]], merge_actions]


class DeskState(DeskInput, DeskOutput, total=False):
    memory: dict[str, Any]  # what `recall` read from the Store: ledger, prefs, lessons
    proposals: list[dict[str, Any]]  # non-poll actions the engine proposes
    poll_brief: dict[str, Any] | None  # in -> poll_studio
    poll: dict[str, Any] | None  # out <- poll_studio
    screened: list[dict[str, Any]]  # every action with its verdict


class HandleInput(TypedDict):
    action: dict[str, Any]
    match_id: str
    window_id: str
    now_vt: int
