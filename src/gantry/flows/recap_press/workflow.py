# NOTE: deliberately NO ``from __future__ import annotations`` in this module. ``@entrypoint`` reads the annotations of the
# entrypoint function at decoration time to build the input schema (Studio's form) and the output / saved-value channel
# types; postponed (string) annotations make that lookup fail.
"""`recap_press`, written as plain Python (the Functional API): the half-time and full-time recap.

    gather (ledger + poll results from the Store)  +  story (the key moments)   ->   write (voiced, token-streamed)

* ``@entrypoint`` makes the whole workflow ONE node in Studio; the value is the task timeline.
* ``previous``: the thread is per MATCH (``recap-<match>``), so what was saved at half-time comes back at full-time. The
  full-time recap picks up where the half-time one left off. What is RETURNED (the recap) differs from what is SAVED
  (a small memo), which is what ``entrypoint.final`` is for.
* The recap is about the PRODUCER'S match as well as the players': which polls ran, how the room voted, what was held back.
"""

from typing import Any, TypedDict

from langgraph.config import get_store
from langgraph.func import entrypoint, task

from gantry.common.stream import emit
from gantry.memory.ledger import GraphStore, ledger_list, poll_results
from gantry.models.factory import voice


class RecapInput(TypedDict, total=False):
    match_id: str
    kind: str  # halftime | fulltime
    match_state: dict[str, Any]
    timeline: list[dict[str, Any]]


class RecapOutput(TypedDict):
    recap: dict[str, Any]


class RecapMemo(TypedDict):
    match_id: str
    recaps: list[dict[str, Any]]


@task
async def gather(match_id: str) -> dict[str, Any]:
    """What the producer did: published actions and how the polls landed (Store reads)."""
    try:
        st = GraphStore(get_store())
    except Exception:
        st = GraphStore(None)
    ledger = await ledger_list(st, match_id)
    results = await poll_results(st, match_id)
    published = [e for e in ledger if e.get("status") == "published"]
    best = max(results, key=lambda r: r["participation"], default=None)
    return {
        "published": {k: sum(1 for e in published if e.get("kind") == k) for k in ("poll", "highlight", "pin_comment", "host_message")},
        "held_back": sum(1 for e in ledger if e.get("status") in ("suppressed", "rejected", "expired")),
        "polls": [{"question": r["question"], "participation": r["participation"], "total": r["total"], "winner": r["options"][max(range(len(r["votes"])), key=lambda i: r["votes"][i])]} for r in results],
        "best_poll": {"question": best["question"], "participation": best["participation"]} if best else None,
    }


@task
def story(timeline: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
    """The match in a few lines: the score and the moments that made it."""
    fx = state.get("fixture") or {"home": {"name": "Home"}, "away": {"name": "Away"}}
    score = state.get("score") or {"home": 0, "away": 0}
    key = [e for e in timeline if e["type"] in ("goal", "card.red", "penalty.saved", "goal.disallowed", "var.no_penalty")]
    return {"scoreline": f"{fx['home']['name']} {score['home']}-{score['away']} {fx['away']['name']}", "moments": [f"{e['minute']} {e['text'].rstrip('.')}" for e in key][-6:]}


async def recap_press(inp: RecapInput, *, previous: RecapMemo | None) -> entrypoint.final[RecapOutput, RecapMemo]:
    match_id = str(inp.get("match_id") or "studio-derby-7")
    kind = "fulltime" if inp.get("kind") == "fulltime" else "halftime"
    state, timeline = inp.get("match_state") or {}, inp.get("timeline") or []
    if not state:  # Studio's blank form: recap the sample derby
        from gantry.live.windows import sample_window

        w = sample_window("derby", 7, to_vt=6060 if kind == "fulltime" else 2900)
        state, timeline = w["match_state"], w["window"]["timeline"]
    desk_f, story_f = gather(match_id), story(timeline, state)  # two independent tasks: start both, then await
    desk, tale = await desk_f, await story_f
    earlier = [r for r in (previous or {}).get("recaps", []) if r.get("kind") != kind]
    label = "Full-time" if kind == "fulltime" else "Half-time"
    lines = [f"{label}: {tale['scoreline']}."]  # the key moments travel in `moments`; the voiced text is what goes in the chat, so it stays short
    if desk["polls"]:
        b = desk["best_poll"]
        lines.append(f"{len(desk['polls'])} polls so far; the room's favourite was “{b['question']}” with {b['participation']:.0%} of viewers voting.")
    if desk["held_back"]:
        lines.append(f"{desk['held_back']} ideas were held back.")
    if earlier and kind == "fulltime":
        lines.append(f"At the break it was {earlier[-1]['scoreline']}.")
    text = await voice("the channel's host, signing off a segment", " ".join(lines), tag="recap", words=80)
    recap = {"kind": kind, "scoreline": tale["scoreline"], "text": text, "moments": tale["moments"], "desk": desk}
    emit("recap.section", f"recap:{match_id}:{kind}", agent="recap_press", **recap)
    memo: RecapMemo = {"match_id": match_id, "recaps": [*earlier, {"kind": kind, "scoreline": tale["scoreline"], "text": text}]}
    return entrypoint.final(value={"recap": recap}, save=memo)
