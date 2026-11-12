from __future__ import annotations

from typing import Any, TypedDict


class KeeperInput(TypedDict, total=False):
    match_id: str
    mode: str  # manual: a human approves what gets remembered; otherwise lessons are written on their own
    poll_result: dict[str, Any]  # {id, topic, template, question, options, votes, total, viewers, participation, opened_vt, closed_vt, minute, by}


class KeeperOutput(TypedDict, total=False):
    learned: dict[str, Any]


class KeeperState(KeeperInput, KeeperOutput, total=False):
    pref: dict[str, Any]
    average: float
    lesson: dict[str, Any] | None
    keep: bool
