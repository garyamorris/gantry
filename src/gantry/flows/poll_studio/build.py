from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from gantry.flows.poll_studio import nodes as n
from gantry.flows.poll_studio.state import StudioInput, StudioOutput, StudioState


def build(checkpointer=None, store=None):  # type: ignore[no-untyped-def]
    """Compile WITHOUT a checkpointer/store for the server (it injects its own); tests pass InMemorySaver/InMemoryStore."""
    g = StateGraph(StudioState, input_schema=StudioInput, output_schema=StudioOutput)
    g.add_node("defaults", n.studio_defaults, metadata={"role": "fills in what a Studio form leaves blank"})
    g.add_node("brief", n.brief, destinations=("write", "finalize"))
    g.add_node("write", n.write, metadata={"role": "one of three writers (Send)"})
    g.add_node("judge", n.judge, defer=True, destinations=("write", "finalize"), metadata={"role": "rubric; sends failures back with notes"})
    g.add_node("finalize", n.finalize)
    g.add_edge(START, "defaults")
    g.add_edge("defaults", "brief")
    g.add_edge("write", "judge")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer, store=store, name="poll_studio")
