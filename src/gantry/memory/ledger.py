"""What the producer has done, and what it has learned: the LangGraph Store.

Thread state is one window; it ends when the window does. The Store is what carries across windows and across
matches, and it is the whole answer to "it needs to know what it has done in the past so it can run on its own":

    ("gantry", "ledger", match_id) / <action id>   every action decided: published, pending, rejected, expired,
                                                   suppressed (with the reason). Polls are indexed on their text, so
                                                   "have I asked something like this?" is a semantic search.
    ("gantry", "polls", match_id)  / <poll id>     how each poll actually did once it closed
    ("gantry", "prefs")            / <template>    running stats per kind of poll, ACROSS matches: participation,
                                                   and how often a human approved, edited or rejected it
    ("gantry", "lessons")          / <lesson id>   short written lessons, recalled by semantic search

Two adapters, one API: graphs use the `BaseStore` they are given (`runtime.store`); the live runtime and the custom
FastAPI routes use the in-process SDK client. Tests pass an `InMemoryStore`.
"""

from __future__ import annotations

from typing import Any, Protocol

NS_PREFS = ("gantry", "prefs")
NS_LESSONS = ("gantry", "lessons")
ROOT = ("gantry",)


def ns_ledger(match_id: str) -> tuple[str, str, str]:
    return ("gantry", "ledger", match_id)


def ns_polls(match_id: str) -> tuple[str, str, str]:
    return ("gantry", "polls", match_id)


class StoreAdapter(Protocol):
    async def get(self, ns: tuple[str, ...], key: str) -> dict[str, Any] | None: ...
    async def put(self, ns: tuple[str, ...], key: str, value: dict[str, Any], *, index: bool = True) -> None: ...
    async def search(self, ns: tuple[str, ...], query: str | None, limit: int = 5) -> list[dict[str, Any]]: ...
    async def delete(self, ns: tuple[str, ...], key: str) -> None: ...


class GraphStore:
    """Adapter over a langgraph `BaseStore` (inside graph nodes, the local runner and tests). `None` = no memory."""

    def __init__(self, store: Any) -> None:
        self.store = store

    async def get(self, ns: tuple[str, ...], key: str) -> dict[str, Any] | None:
        if self.store is None:
            return None
        item = await self.store.aget(ns, key)
        return dict(item.value) if item else None

    async def put(self, ns: tuple[str, ...], key: str, value: dict[str, Any], *, index: bool = True) -> None:
        if self.store is not None:
            await self.store.aput(ns, key, value, index=None if index else False)

    async def search(self, ns: tuple[str, ...], query: str | None, limit: int = 5) -> list[dict[str, Any]]:
        if self.store is None:
            return []
        items = await self.store.asearch(ns, query=query, limit=limit)
        return [{"key": i.key, "score": getattr(i, "score", None), "value": dict(i.value)} for i in items]

    async def delete(self, ns: tuple[str, ...], key: str) -> None:
        if self.store is not None:
            await self.store.adelete(ns, key)


class SdkStore:
    """Adapter over the in-process langgraph SDK client (the live runtime and the custom routes)."""

    def __init__(self, client: Any) -> None:
        self.client = client

    async def get(self, ns: tuple[str, ...], key: str) -> dict[str, Any] | None:
        try:
            item = await self.client.store.get_item(list(ns), key)
        except Exception:
            return None
        return dict(item["value"]) if item else None

    async def put(self, ns: tuple[str, ...], key: str, value: dict[str, Any], *, index: bool = True) -> None:
        await self.client.store.put_item(list(ns), key, value, index=None if index else False)

    async def search(self, ns: tuple[str, ...], query: str | None, limit: int = 5) -> list[dict[str, Any]]:
        res = await self.client.store.search_items(list(ns), query=query, limit=limit)
        return [{"key": i["key"], "score": i.get("score"), "value": dict(i["value"])} for i in res.get("items", [])]

    async def delete(self, ns: tuple[str, ...], key: str) -> None:
        await self.client.store.delete_item(list(ns), key)


# ------------------------------------------------------------------------------------------------- the ledger
def searchable(entry: dict[str, Any]) -> str:
    """The text a ledger entry is found by: a poll by its question and options, anything else by what it said."""
    if entry.get("kind") == "poll":
        return f"{entry.get('question', '')} {' '.join(entry.get('options') or [])}".strip()
    return str(entry.get("summary") or entry.get("text") or entry.get("topic") or "")


async def ledger_put(st: StoreAdapter, match_id: str, entry: dict[str, Any]) -> None:
    """Idempotent by `entry["id"]`: a re-run node (resume after an interrupt, a retry) overwrites, never duplicates."""
    value = {**entry, "match_id": match_id, "text": searchable(entry)}
    await st.put(ns_ledger(match_id), entry["id"], value, index=entry.get("kind") == "poll" and entry.get("status") != "suppressed")


async def ledger_patch(st: StoreAdapter, match_id: str, action_id: str, **changes: Any) -> dict[str, Any] | None:
    cur = await st.get(ns_ledger(match_id), action_id)
    if cur is None:
        return None
    new = {**cur, **changes}
    await ledger_put(st, match_id, new)
    return new


async def ledger_list(st: StoreAdapter, match_id: str, limit: int = 400) -> list[dict[str, Any]]:
    """Everything decided this match, oldest first."""
    hits = await st.search(ns_ledger(match_id), None, limit)
    return sorted((h["value"] for h in hits), key=lambda e: (e.get("vt", 0), e.get("id", "")))


async def ledger_similar(st: StoreAdapter, match_id: str, question: str, limit: int = 3) -> list[dict[str, Any]]:
    """Polls of this match whose text is closest to `question` (semantic search; scores are None without an index)."""
    hits = await st.search(ns_ledger(match_id), question, limit)
    return [
        {"id": h["key"], "score": round(h["score"], 3) if h["score"] is not None else None, "status": h["value"].get("status"),
         "topic": h["value"].get("topic"), "question": h["value"].get("question")}
        for h in hits if h["value"].get("kind") == "poll"
    ]


async def ledger_clear(st: StoreAdapter, match_id: str) -> int:
    n = 0
    for ns in (ns_ledger(match_id), ns_polls(match_id)):
        for h in await st.search(ns, None, 500):
            await st.delete(ns, h["key"])
            n += 1
    return n


# ------------------------------------------------------------------------------------------------ poll results
async def poll_result_put(st: StoreAdapter, match_id: str, result: dict[str, Any]) -> None:
    await st.put(ns_polls(match_id), result["id"], result, index=False)


async def poll_results(st: StoreAdapter, match_id: str) -> list[dict[str, Any]]:
    hits = await st.search(ns_polls(match_id), None, 100)
    return sorted((h["value"] for h in hits), key=lambda r: r.get("opened_vt", 0))


# ----------------------------------------------------------------------------------------------- preferences
async def prefs_all(st: StoreAdapter) -> dict[str, dict[str, Any]]:
    return {h["key"]: h["value"] for h in await st.search(NS_PREFS, None, 100)}


def fold_pref(cur: dict[str, Any] | None, *, template: str, participation: float | None = None, decision: str | None = None, sample: str | None = None) -> dict[str, Any]:
    """Pure: fold one observation into a template's running stats. `sample` (a poll id) makes it idempotent."""
    p = dict(cur or {"template": template, "n": 0, "mean": 0.0, "best": 0.0, "approved": 0, "edited": 0, "rejected": 0, "auto": 0, "samples": []})
    if sample is not None:
        if sample in p["samples"]:
            return p
        p["samples"] = [*p["samples"], sample][-50:]
    if participation is not None:
        p["mean"] = round((p["mean"] * p["n"] + participation) / (p["n"] + 1), 4)
        p["n"] += 1
        p["best"] = round(max(p["best"], participation), 4)
    if decision in ("approved", "edited", "rejected", "auto"):
        p[decision] += 1
    return p


async def prefs_update(st: StoreAdapter, template: str, **obs: Any) -> dict[str, Any]:
    new = fold_pref(await st.get(NS_PREFS, template), template=template, **obs)
    await st.put(NS_PREFS, template, new, index=False)
    return new


# --------------------------------------------------------------------------------------------------- lessons
async def lessons_search(st: StoreAdapter, query: str | None, limit: int = 3) -> list[dict[str, Any]]:
    hits = await st.search(NS_LESSONS, query, limit)
    return [{"id": h["key"], "score": round(h["score"], 3) if h["score"] is not None else None, **h["value"]} for h in hits]


async def lesson_put(st: StoreAdapter, lesson: dict[str, Any]) -> None:
    await st.put(NS_LESSONS, lesson["id"], lesson)


async def memory_reset(st: StoreAdapter) -> int:
    """Forget everything learned across matches (prefs and lessons). Ledgers are per match and cleared separately."""
    n = 0
    for ns in (NS_PREFS, NS_LESSONS):
        for h in await st.search(ns, None, 200):
            await st.delete(ns, h["key"])
            n += 1
    return n
