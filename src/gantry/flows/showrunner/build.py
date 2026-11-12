from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from gantry.flows.action_desk.build import build as build_desk
from gantry.flows.curator.build import build as build_curator
from gantry.flows.showrunner import nodes as n
from gantry.flows.showrunner.state import ShowInput, ShowOutput, ShowState


def build(checkpointer=None, store=None):  # type: ignore[no-untyped-def]
    """Compile WITHOUT a checkpointer/store for the server (it injects its own); tests pass InMemorySaver/InMemoryStore."""
    g = StateGraph(ShowState, input_schema=ShowInput, output_schema=ShowOutput)
    g.add_node("intake", n.intake, metadata={"role": "validates the window; fills a Studio form's blanks"})
    g.add_node("triage", n.triage, destinations=("curate", "skip"), metadata={"role": "cheap gate: most ticks are noise"})
    g.add_node("skip", n.skip)
    g.add_node("curate", build_curator(), metadata={"subgraph": "flow 1: rank and curate"})
    g.add_node("act", build_desk(), metadata={"subgraph": "flow 2: generate actions (contains poll_studio and the approval interrupts)"})
    g.add_node("wrap", n.wrap)
    g.add_edge(START, "intake")
    g.add_edge("intake", "triage")
    g.add_edge("skip", END)
    g.add_edge("curate", "act")
    g.add_edge("act", "wrap")
    g.add_edge("wrap", END)
    return g.compile(checkpointer=checkpointer, store=store, name="showrunner")
