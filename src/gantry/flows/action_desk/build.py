from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from gantry.flows.action_desk import nodes as n
from gantry.flows.action_desk.state import DeskInput, DeskOutput, DeskState
from gantry.flows.poll_studio.build import build as build_studio


def build(checkpointer=None, store=None):  # type: ignore[no-untyped-def]
    """Compile WITHOUT a checkpointer/store for the server (it injects its own); tests pass InMemorySaver/InMemoryStore."""
    g = StateGraph(DeskState, input_schema=DeskInput, output_schema=DeskOutput)
    g.add_node("defaults", n.studio_defaults, metadata={"role": "fills in what a Studio form leaves blank"})
    g.add_node("recall", n.recall, metadata={"store": "read: ledger, preferences, lessons (semantic search)"})
    g.add_node("propose", n.propose)
    g.add_node("poll_studio", build_studio(), metadata={"subgraph": "three writers, a judge, a revise loop"})
    g.add_node("screen", n.screen, metadata={"policy": "publish | ask | suppress", "store": "semantic search for near-duplicate polls"})
    g.add_node("handle", n.handle, metadata={"human": "one interrupt per action, resumed by id", "store": "write"})
    g.add_node("finish", n.finish, defer=True)
    g.add_edge(START, "defaults")
    g.add_edge("defaults", "recall")
    g.add_edge("recall", "propose")
    g.add_conditional_edges("propose", n.route_after_propose, {"poll_studio": "poll_studio", "screen": "screen"})
    g.add_edge("poll_studio", "screen")
    g.add_conditional_edges("screen", n.dispatch, ["handle", "finish"])
    g.add_edge("handle", "finish")
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer, store=store, name="action_desk")
