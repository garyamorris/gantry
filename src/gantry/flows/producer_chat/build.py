"""`producer_chat`: ask the producer what it did and why (create_agent + tools over the Store).

The autonomous loop explains itself through its ledger; this graph is the conversational way in. Three read-only
tools: what was done (or held back) in a match, how each kind of poll is performing, and what lessons were learned.
Offline the model is scripted (keyword -> tool -> a wrap-up quoting the tool); with a key it is the smart model.
In Studio, open it in chat mode and set the context's `match_id` to a live match (the dashboard shows the id).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.tools import ToolRuntime, tool

from gantry.memory.ledger import GraphStore, ledger_list, lessons_search, prefs_all
from gantry.models import offline as o
from gantry.models.factory import get_chat_model

SYSTEM = (
    "You are Gantry, the autonomous producer of a live sports stream. You rank and curate the live chat and decide which polls, pins and "
    "highlights go out. Answer questions about what you did and why using your tools; quote the ledger's own reasons. Be brief and concrete. "
    "Never invent an action that is not in the ledger."
)


@dataclass
class ProducerContext:
    """Run context: WHICH match the question is about."""

    match_id: str = ""


def _match_id(runtime: Any) -> str:
    ctx = getattr(runtime, "context", None)
    value = ctx.get("match_id") if isinstance(ctx, dict) else getattr(ctx, "match_id", None)
    return str(value or "")


@tool
async def actions_taken(status: str, runtime: ToolRuntime[ProducerContext]) -> str:
    """List what the producer did this match. `status` is one of: published, suppressed, rejected, expired, all."""
    mid = _match_id(runtime)
    if not mid:
        return "No match selected: set match_id in the run context."
    ledger = await ledger_list(GraphStore(runtime.store), mid)
    rows = [e for e in ledger if status == "all" or e.get("status") == status]
    if not rows:
        return f"Nothing {status} in {mid} yet."
    lines = [f"- {e.get('minute', '?')} {e.get('kind')}: {e.get('question') or e.get('title') or e.get('text') or e.get('topic')} ({e.get('status')}; {e.get('reason')})" for e in rows[-12:]]
    return f"{len(rows)} {status} in {mid}; the latest:\n" + "\n".join(lines)


@tool
async def poll_performance(runtime: ToolRuntime[ProducerContext]) -> str:
    """How each kind of poll performs with this audience, across matches (share of viewers who vote)."""
    prefs = await prefs_all(GraphStore(runtime.store))
    rows = sorted((p for p in prefs.values() if p.get("n")), key=lambda p: -p["mean"])
    if not rows:
        return "No poll has closed yet, so there is nothing to compare."
    return "\n".join(f"- {p['template']}: {p['mean']:.0%} of viewers over {p['n']} poll(s); humans approved {p['approved'] + p['edited']}, rejected {p['rejected']}" for p in rows)


@tool
async def lessons_learned(query: str, runtime: ToolRuntime[ProducerContext]) -> str:
    """Search the lessons the producer has written down (semantic search)."""
    hits = await lessons_search(GraphStore(runtime.store), query or None, limit=4)
    return "\n".join(f"- {h['text']}" for h in hits) or "No lessons written down yet: they appear once a kind of poll clearly beats or trails the average."


TOOLS = [actions_taken, poll_performance, lessons_learned]


def build_script() -> o.Script:
    s = o.Script()
    s.when("wrap_up", lambda c: bool(c.tool_results), lambda c: o.say("Here is what the ledger says.\n" + "\n".join(str(m.content) for m in c.tool_results)))
    s.when("lessons", lambda c: bool(re.search(r"\b(lessons?|learn\w*|memory|remember\w*)\b", c.human, re.I)), lambda c: o.call_tool("lessons_learned", query=c.human[:120]))
    s.when("performance", lambda c: bool(re.search(r"\b(perform\w*|working|participation|engag\w*|votes?|best|worst)\b", c.human, re.I)), lambda c: o.call_tool("poll_performance"))
    s.when("held_back", lambda c: bool(re.search(r"\b(why|skip\w*|suppress\w*|held|hold|didn'?t|not)\b", c.human, re.I)), lambda c: o.call_tool("actions_taken", status="suppressed"))
    s.when("done", lambda c: True, lambda c: o.call_tool("actions_taken", status="published"))
    return s


def build(checkpointer: Any = None, store: Any = None, *, model: Any = None):  # type: ignore[no-untyped-def]
    """Compile WITHOUT a checkpointer/store for the server (it injects its own); tests pass InMemorySaver/InMemoryStore."""
    return create_agent(
        model or get_chat_model("smart", script=build_script()), TOOLS, system_prompt=SYSTEM,
        middleware=[ModelCallLimitMiddleware(run_limit=4, exit_behavior="end")],
        context_schema=ProducerContext, checkpointer=checkpointer, store=store, name="producer_chat",
    )
