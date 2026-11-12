from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class CuratorInput(TypedDict, total=False):
    match_id: str
    mode: str
    window: dict[str, Any]  # see gantry.live.windows.build_window
    match_state: dict[str, Any]
    # Studio conveniences: leave `window` blank and a sample is cut from the simulator
    fixture: str
    seed: int
    minute: int


class CuratorOutput(TypedDict, total=False):
    rundown: dict[str, Any]


class CuratorState(CuratorInput, CuratorOutput, total=False):
    moments: list[dict[str, Any]]
    lexicon: dict[str, list[str]]
    scored: Annotated[list[dict[str, Any]], operator.add]  # filled by the `score_shard` Send workers
    curation: dict[str, Any]


class ShardInput(TypedDict):
    wid: str
    shard: int
    comments: list[dict[str, Any]]
    lexicon: dict[str, list[str]]
