"""Nodes of the `curator` graph: FLOW 1, rank and curate.

A two-stage ranker over one live window. Stage one is cheap and deterministic (`gantry.engine.ranking`): score every
feed event, score every chat message in parallel shards, find the themes. Stage two is the editor: a model sees only
the shortlist and picks what to highlight and pin. The engine's own pick is the draft and the fallback, and the
editor can only choose from ids the engine shortlisted, so a model can re-order the best of the chat but can never
surface something the first stage hid.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import Send
from pydantic import BaseModel, Field

from gantry.common.stream import emit
from gantry.engine import ranking
from gantry.flows.curator.state import CuratorState, ShardInput
from gantry.flows.defaults import complete_window
from gantry.models.factory import structured, voice

SHARD = 40
TOP_MOMENTS = 6
HIGHLIGHTS = 3
PIN_FLOOR = 0.8


class EditorPicks(BaseModel):
    """The editor's choice from the shortlist."""

    highlight_ids: list[str] = Field(description="Up to three comment ids from the shortlist worth highlighting, best first")
    pin_id: str | None = Field(default=None, description="One id from highlight_ids worth pinning above the chat, or null if none is good enough")


def _k(state: CuratorState, *parts: Any) -> str:
    return ":".join(str(p) for p in (state["window"]["id"], "cur", *parts))


async def studio_defaults(state: CuratorState) -> dict[str, Any]:
    update, filled = complete_window(dict(state))
    if filled:
        emit("note", f"{update['window']['id']}:cur:defaults", agent="defaults", text="Filled in what was left blank: " + ", ".join(filled) + ".")
    return update


def rank(state: CuratorState) -> dict[str, Any]:
    w = state["window"]
    moments = ranking.rank_moments(w["events"], w["timeline"], w["comments"], w["chat"]["baseline_per_min"])
    for i, m in enumerate(moments[:TOP_MOMENTS]):
        if m["tier"] != "routine":
            emit("moment.ranked", _k(state, "moment", m["event_id"]), agent="curator", rank=i + 1, **m)
    emit("progress", _k(state, "progress", "rank"), agent="curator", label=f"Ranked {len(moments)} feed events", pct=25)
    return {"moments": moments, "lexicon": ranking.build_lexicon(moments, state["match_state"])}


def fan_out(state: CuratorState) -> list[Send] | str:
    comments = state["window"]["comments"]
    if not comments:
        return "merge"
    wid = state["window"]["id"]
    shards = [comments[i : i + SHARD] for i in range(0, len(comments), SHARD)]
    return [Send("score_shard", {"wid": wid, "shard": i, "comments": chunk, "lexicon": state["lexicon"]}) for i, chunk in enumerate(shards)]


def score_shard(payload: ShardInput) -> dict[str, Any]:
    """One shard of the chat, scored in isolation (the map step). Duplicates across shards are settled in `merge`."""
    seen: dict[str, int] = {}
    scored = [ranking.score_comment(c, payload["lexicon"], seen) for c in payload["comments"]]
    emit("chat.scored", f"{payload['wid']}:cur:shard:{payload['shard']}", agent="curator", shard=payload["shard"], n=len(scored),
         hidden=sum(1 for c in scored if c["label"] == "hide"))
    return {"scored": scored}


def merge(state: CuratorState) -> dict[str, Any]:
    """The reduce step (deferred until every shard is in): settle duplicates, find the themes, cut the shortlist."""
    scored = ranking.dedupe_pass(state.get("scored") or [])
    themes = ranking.find_themes(scored)
    short = ranking.shortlist(scored)
    hidden = [{"id": c["id"], "reason": c["reasons"][0] if c["reasons"] else "hidden", "author": (c.get("author") or {}).get("name")} for c in scored if c["label"] == "hide"]
    collapsed = [c["id"] for c in scored if c["label"] == "collapse"]
    emit("progress", _k(state, "progress", "merge"), agent="curator", label=f"Scored {len(scored)} comments: {len(short)} shortlisted, {len(hidden)} hidden", pct=60)
    return {"curation": {"shortlist": short, "themes": themes, "hidden": hidden, "collapsed": collapsed, "total": len(scored)}}


def clean_picks(picks: EditorPicks, short: list[dict[str, Any]]) -> tuple[list[str], str | None]:
    """Keep only ids the engine shortlisted (no duplicates, at most three); a pin must be one of the highlights."""
    allowed = {c["id"] for c in short}
    ids: list[str] = []
    for i in picks.highlight_ids:
        if i in allowed and i not in ids:
            ids.append(i)
    ids = ids[:HIGHLIGHTS]
    return ids, picks.pin_id if picks.pin_id in ids else None


def note_draft(headline: dict[str, Any] | None, cur: dict[str, Any], heat: float, highlights: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    if headline:
        reasons = [w["label"] for w in headline["why"][1:3]]
        parts.append(f"{headline['minute']}: {headline['text']}" + (f" ({', '.join(reasons)})" if reasons else ""))
    else:
        parts.append("Quiet spell: nothing on the pitch worth a headline.")
    if heat >= 1.5:
        parts.append(f"Chat is running at {heat:.1f}x its normal pace.")
    if cur["themes"]:
        parts.append(f"The talk is about {cur['themes'][0]['label']}.")
    if highlights:
        parts.append(f"Best of the chat is from {(highlights[0].get('author') or {}).get('name', 'a viewer')}.")
    if cur["hidden"]:
        parts.append(f"{len(cur['hidden'])} hidden as spam or abuse.")
    return " ".join(parts)


async def editor(state: CuratorState) -> dict[str, Any]:
    """Stage two: the editor re-ranks the shortlist, then writes the one-line rundown note (token-streamed)."""
    w, cur, moments = state["window"], state["curation"], state.get("moments") or []
    headline = next((m for m in moments if m["tier"] != "routine"), None)
    # what was said AFTER the headline moment comes first: a pinned comment should be about what just happened
    short = sorted(cur["shortlist"], key=lambda c: (bool(headline) and c["vt"] < headline["vt"], -c["score"]))
    heat = round(w["chat"]["per_min"] / max(w["chat"]["baseline_per_min"], 1.0), 2)
    engine_ids = [c["id"] for c in short[:HIGHLIGHTS] if c["score"] >= 0.55]
    pin_worthy = bool(engine_ids) and short[0]["score"] >= PIN_FLOOR and (headline is not None and short[0]["vt"] >= headline["vt"])
    draft = EditorPicks(highlight_ids=engine_ids, pin_id=engine_ids[0] if pin_worthy else None)
    source = "engine"
    ids, pin_id = clean_picks(draft, short)
    if short:
        facts = {
            "moment": headline["text"] if headline else "a quiet spell",
            "themes": [t["label"] for t in cur["themes"]],
            "shortlist": [{"id": c["id"], "text": c["text"], "likes": c["likes"], "engine_score": c["score"]} for c in short],
        }
        picks, source = await structured(
            EditorPicks, role="smart", draft=draft, facts=facts,
            system=("You are the chat editor of a live sports stream. From the shortlist, choose up to three comments worth highlighting for every viewer: "
                    "prefer ones that explain the moment, add information, or ask what everyone is wondering. Skip pure reactions and anything hostile. "
                    "Pin at most one, and only if it is genuinely good AND about the moment that just happened; most windows deserve no pin. "
                    "Use only ids from the shortlist."),
        )
        ids, pin_id = clean_picks(picks, short)
    by_id = {c["id"]: c for c in short}
    highlights = [by_id[i] for i in ids]
    pin = by_id.get(pin_id) if pin_id else None
    emit("editor.pick", _k(state, "editor"), agent="editor", source=source, highlight=ids, pin=pin_id, engine=engine_ids)
    note = await voice("the stream's producer, brisk and precise", note_draft(headline, cur, heat, highlights), tag="rundown", words=50)
    rundown = {
        "window_id": w["id"], "to_vt": w["to_vt"], "moments": moments[:TOP_MOMENTS], "headline": headline,
        "comments": {"highlights": highlights, "pin": pin, "hidden": cur["hidden"], "collapsed": cur["collapsed"], "shortlist": [c["id"] for c in short], "total": cur["total"]},
        "themes": cur["themes"], "heat": heat, "note": note, "editor": source,
    }
    # the one event with a consequence in this graph: the runtime labels the live chat from it (Top chat hides and highlights)
    emit("curation.result", _k(state, "result"), agent="curator", window_id=w["id"], highlight=ids, pin=pin_id, hidden=cur["hidden"], collapsed=cur["collapsed"],
         themes=cur["themes"], heat=heat, note=note, headline=headline["event_id"] if headline else None)
    emit("progress", _k(state, "progress", "done"), agent="curator", label="Rundown ready", pct=100)
    return {"rundown": rundown}
