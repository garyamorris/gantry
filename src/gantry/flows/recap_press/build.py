from __future__ import annotations

from typing import Any

from langgraph.func import entrypoint
from langgraph.pregel import Pregel

from gantry.flows.recap_press.workflow import recap_press


def build(checkpointer: Any = None, store: Any = None) -> Pregel:
    """The workflow as a Pregel graph. Compile WITHOUT a checkpointer/store for the server (it injects its own);
    tests pass InMemorySaver/InMemoryStore. `previous` only works on a checkpointed thread (one per match)."""
    graph = entrypoint(checkpointer=checkpointer, store=store)(recap_press)
    graph.name = "recap_press"
    return graph
