from __future__ import annotations

from typing import Annotated, Any, TypedDict

from gantry.flows.action_desk.state import merge_actions


class ShowInput(TypedDict, total=False):
    match_id: str
    mode: str  # manual | assisted | autopilot
    window: dict[str, Any]  # see gantry.live.windows.build_window
    match_state: dict[str, Any]
    # Studio conveniences: leave `window` blank and a sample is cut from the simulator
    fixture: str
    seed: int
    minute: int


class ShowOutput(TypedDict, total=False):
    summary: dict[str, Any]


class ShowState(ShowInput, ShowOutput, total=False):
    triage: dict[str, Any]
    rundown: dict[str, Any]  # <- curator (subgraph)
    actions: Annotated[list[dict[str, Any]], merge_actions]  # <- action_desk (subgraph)
