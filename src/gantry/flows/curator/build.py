from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from gantry.flows.curator import nodes as n
from gantry.flows.curator.state import CuratorInput, CuratorOutput, CuratorState


def build(checkpointer=None, store=None):  # type: ignore[no-untyped-def]
    """Compile WITHOUT a checkpointer/store for the server (it injects its own); tests pass InMemorySaver/InMemoryStore."""
    g = StateGraph(CuratorState, input_schema=CuratorInput, output_schema=CuratorOutput)
    g.add_node("defaults", n.studio_defaults, metadata={"role": "fills in what a Studio form leaves blank"})
    g.add_node("rank_moments", n.rank, metadata={"stage": "one: deterministic features"})
    g.add_node("score_shard", n.score_shard, metadata={"stage": "one: map over chat shards"})
    g.add_node("merge", n.merge, defer=True, metadata={"stage": "one: reduce"})
    g.add_node("editor", n.editor, metadata={"stage": "two: a model re-ranks the shortlist"})
    g.add_edge(START, "defaults")
    g.add_edge("defaults", "rank_moments")
    g.add_conditional_edges("rank_moments", n.fan_out, ["score_shard", "merge"])
    g.add_edge("score_shard", "merge")
    g.add_edge("merge", "editor")
    g.add_edge("editor", END)
    return g.compile(checkpointer=checkpointer, store=store, name="curator")
