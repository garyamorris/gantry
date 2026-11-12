"""How the live runtime runs graphs: one seam, two implementations.

* `SdkRunner`   production. The in-process LangGraph SDK client (`get_client(url=None)`): runs are real Agent Server
                runs on real threads, visible in Studio, traced, checkpointed. No network hop.
* `LocalRunner` compiled graphs with an in-memory checkpointer and Store. No server at all: this is what the tests,
                the evals and `make demo` use to play a whole match in a second.

Both stream a run's custom events (`stream_mode="custom"`, subgraphs included) and can list a thread's pending
interrupts. The runtime never touches a graph any other way.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any, Protocol

from gantry.memory.ledger import GraphStore, SdkStore, StoreAdapter

GRAPH_IDS = ("showrunner", "curator", "action_desk", "poll_studio", "memory_keeper", "recap_press", "producer_chat")
_NS = uuid.UUID("6f0e7a1c-5d0b-4a52-9c1e-9a0c5d2b7e11")


def thread_id(match_id: str, kind: str, key: Any) -> str:
    """Deterministic thread id (the Agent Server wants UUIDs): the same window of the same match is the same thread."""
    return str(uuid.uuid5(_NS, f"{match_id}:{kind}:{key}"))


class GraphRunner(Protocol):
    store: StoreAdapter

    def run(self, graph: str, thread: str, *, input: dict[str, Any] | None = None, resume: dict[str, Any] | None = None,
            metadata: dict[str, Any] | None = None) -> AsyncIterator[dict[str, Any]]: ...

    async def interrupts(self, graph: str, thread: str) -> list[dict[str, Any]]: ...


class LocalRunner:
    """Compiled graphs, one shared in-memory checkpointer and Store (with the same hashed embedder the server indexes with)."""

    def __init__(self, store: Any = None, saver: Any = None) -> None:
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.store.memory import InMemoryStore

        from gantry.embeddings import aembed
        from gantry.flows.memory_keeper.build import build as build_keeper
        from gantry.flows.recap_press.build import build as build_recap
        from gantry.flows.showrunner.build import build as build_show

        self.raw_store = store or InMemoryStore(index={"embed": aembed, "dims": 256, "fields": ["text"]})
        self.saver = saver or InMemorySaver()
        self.store: StoreAdapter = GraphStore(self.raw_store)
        self.graphs = {
            "showrunner": build_show(self.saver, self.raw_store), "memory_keeper": build_keeper(self.saver, self.raw_store),
            "recap_press": build_recap(self.saver, self.raw_store),
        }

    async def run(self, graph: str, thread: str, *, input: dict[str, Any] | None = None, resume: dict[str, Any] | None = None,
                  metadata: dict[str, Any] | None = None) -> AsyncIterator[dict[str, Any]]:
        from langgraph.types import Command

        cfg = {"configurable": {"thread_id": thread}, "metadata": metadata or {}}
        payload: Any = Command(resume=resume) if resume is not None else input
        async for _ns, _mode, chunk in self.graphs[graph].astream(payload, cfg, stream_mode=["custom"], subgraphs=True):
            if isinstance(chunk, dict) and "kind" in chunk:
                yield chunk

    async def interrupts(self, graph: str, thread: str) -> list[dict[str, Any]]:
        snap = await self.graphs[graph].aget_state({"configurable": {"thread_id": thread}})
        return [{"id": i.id, "value": i.value} for i in snap.interrupts]


class SdkRunner:
    """The in-process Agent Server client. Only usable inside `langgraph dev` / a deployment (the custom app's process)."""

    def __init__(self, client: Any = None) -> None:
        if client is None:
            from langgraph_sdk import get_client

            client = get_client(url=None)
        self.client = client
        self.store: StoreAdapter = SdkStore(client)

    async def run(self, graph: str, thread: str, *, input: dict[str, Any] | None = None, resume: dict[str, Any] | None = None,
                  metadata: dict[str, Any] | None = None) -> AsyncIterator[dict[str, Any]]:
        await self.client.threads.create(thread_id=thread, if_exists="do_nothing", metadata=metadata or {})
        kwargs: dict[str, Any] = {"command": {"resume": resume}} if resume is not None else {"input": input}
        async for part in self.client.runs.stream(thread, graph, stream_mode=["custom"], stream_subgraphs=True, metadata=metadata or {}, **kwargs):
            if part.event == "error":
                raise RuntimeError(f"{graph} run failed: {part.data}")
            if part.event.split("|")[0] == "custom" and isinstance(part.data, dict) and "kind" in part.data:
                yield part.data

    async def interrupts(self, graph: str, thread: str) -> list[dict[str, Any]]:
        state = await self.client.threads.get_state(thread)
        out: list[dict[str, Any]] = []
        for it in state.get("interrupts") or []:
            if isinstance(it, dict) and it.get("id"):
                out.append({"id": it["id"], "value": it.get("value")})
        if not out:  # older shape: interrupts hang off the tasks
            for t in state.get("tasks") or []:
                for it in t.get("interrupts") or []:
                    if isinstance(it, dict) and it.get("id"):
                        out.append({"id": it["id"], "value": it.get("value")})
        return out
