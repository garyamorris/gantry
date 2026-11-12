from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class StudioInput(TypedDict, total=False):
    poll_brief: dict[str, Any]  # {"candidates": [engine candidates, best first], "avoid": [questions already asked], "match": {...}, "lessons": [...]}
    # Studio conveniences: leave `poll_brief` blank and one is planned from a sample window
    fixture: str
    seed: int
    minute: int


class StudioOutput(TypedDict, total=False):
    poll: dict[str, Any] | None


class StudioState(StudioInput, StudioOutput, total=False):
    drafts: Annotated[list[dict[str, Any]], operator.add]  # every draft of every round, from the `write` Send workers
    round: int
    verdict: dict[str, Any]


class WriteInput(TypedDict):
    writer: str
    round: int
    candidate: dict[str, Any]
    notes: list[str]
    match: dict[str, Any]
    lessons: list[str]
