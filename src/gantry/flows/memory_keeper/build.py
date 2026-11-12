from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from gantry.flows.memory_keeper import nodes as n
from gantry.flows.memory_keeper.state import KeeperInput, KeeperOutput, KeeperState


def build(checkpointer=None, store=None):  # type: ignore[no-untyped-def]
    """Compile WITHOUT a checkpointer/store for the server (it injects its own); tests pass InMemorySaver/InMemoryStore."""
    g = StateGraph(KeeperState, input_schema=KeeperInput, output_schema=KeeperOutput)
    g.add_node("defaults", n.studio_defaults, metadata={"role": "fills in what a Studio form leaves blank"})
    g.add_node("record", n.record, metadata={"store": "write: the poll result, the ledger entry"})
    g.add_node("learn", n.learn, metadata={"store": "write: template stats; read: lessons"})
    g.add_node("curate", n.curate, metadata={"human": "memory-write review (manual mode)"})
    g.add_node("close", n.close, metadata={"store": "write: the lesson, indexed for semantic recall"})
    g.add_edge(START, "defaults")
    g.add_edge("defaults", "record")
    g.add_edge("record", "learn")
    g.add_conditional_edges("learn", n.route_after_learn, {"curate": "curate", "close": "close"})
    g.add_edge("curate", "close")
    g.add_edge("close", END)
    return g.compile(checkpointer=checkpointer, store=store, name="memory_keeper")
