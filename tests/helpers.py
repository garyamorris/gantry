from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from langgraph.types import Command

from gantry.common.stream import capture

Chooser = Callable[[dict[str, Any]], Any]


def cfg(thread: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread}}


async def drive(graph: Any, input_: Any, config: dict[str, Any], chooser: Chooser, max_steps: int = 20) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Run to completion, answering every interrupt with `chooser(payload)`. Returns (final state, events, interrupt payloads)."""
    asked: list[dict[str, Any]] = []
    with capture() as buf:
        cur: Any = input_
        for _ in range(max_steps):
            await graph.ainvoke(cur, config)
            snap = await graph.aget_state(config)
            if not snap.interrupts:
                break
            resume = {}
            for it in snap.interrupts:
                asked.append(it.value)
                resume[it.id] = chooser(it.value)
            cur = Command(resume=resume)
        events = list(buf)
    snap = await graph.aget_state(config)
    return snap.values, events, asked


def kinds(events: list[dict[str, Any]]) -> list[str]:
    return [e["kind"] for e in events]


def ndcg(order: list[str], truth: dict[str, float], k: int) -> float:
    dcg = sum(truth[e] / math.log2(i + 2) for i, e in enumerate(order[:k]))
    ideal = sum(v / math.log2(i + 2) for i, v in enumerate(sorted(truth.values(), reverse=True)[:k]))
    return dcg / ideal
