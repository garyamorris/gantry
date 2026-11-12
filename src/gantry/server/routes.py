"""Custom API (docs/PROTOCOL.md section 2). Everything that is NOT a graph run lives here: the live broadcast.

All routes are `async def`, do no blocking IO, and reach the Agent Server only through the in-process SDK client
(inside `gantry.live.runner.SdkRunner`). Threads, runs, assistants and the Store are the built-in API's job: the
dashboard calls those directly at the same origin.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from gantry import __version__
from gantry.live.runner import SdkRunner, thread_id
from gantry.live.runtime import LiveRuntime
from gantry.live.session import SPEEDS, Session, SessionError
from gantry.memory.ledger import ledger_list, lessons_search, memory_reset, prefs_all
from gantry.models.factory import STATS as model_stats
from gantry.server import links
from gantry.server.catalog import GRAPH_IDS, GRAPHS
from gantry.settings import get_settings
from gantry.sim.fixtures import FIXTURE_IDS, public_fixture

router = APIRouter(prefix="/api")
_runtime: LiveRuntime | None = None


def runtime() -> LiveRuntime:
    global _runtime
    if _runtime is None:
        _runtime = LiveRuntime(SdkRunner)
    return _runtime


async def close_runtime() -> None:
    global _runtime
    if _runtime is not None:
        await _runtime.close()
        _runtime = None


async def session_error_handler(_: Request, exc: SessionError) -> JSONResponse:
    return JSONResponse({"error": exc.message}, status_code=exc.status)


def _session(match_id: str) -> Session:
    return runtime().get(match_id)


@router.get("/boot")
async def boot() -> dict[str, Any]:
    s = get_settings()
    ids = await links.assistant_ids()
    provider = s.resolved_provider()
    return {
        "version": __version__, "provider": provider, "models": {"smart": s.model_name("smart"), "fast": s.model_name("fast")}, "model_calls": dict(model_stats),
        "tracing": {"enabled": links.tracing_enabled(), "project": links.project_name()}, "studio": {"url": s.studio_url, "base_link": links.studio_base()},
        "public_base_url": s.public_base_url, "graphs": [{**g, "assistant_id": ids.get(g["id"])} for g in GRAPHS],
        "fixtures": [public_fixture(f) for f in FIXTURE_IDS], "modes": ["manual", "assisted", "autopilot"], "speeds": list(SPEEDS), "matches": runtime().list(),
    }


@router.get("/fixtures")
async def fixtures() -> list[dict[str, Any]]:
    return [public_fixture(f) for f in FIXTURE_IDS]


class NewMatch(BaseModel):
    fixture: str = "derby"
    seed: int = 7
    mode: str = "autopilot"
    speed: float = Field(10, gt=0, le=60)
    autostart: bool = True


@router.get("/matches")
async def matches() -> list[dict[str, Any]]:
    return runtime().list()


@router.post("/matches")
async def new_match(body: NewMatch | None = None) -> dict[str, Any]:
    b = body or NewMatch()
    s = await runtime().create(fixture=b.fixture, seed=b.seed, mode=b.mode, speed=b.speed, autostart=b.autostart)
    return s.snapshot()


@router.get("/matches/{match_id}")
async def match(match_id: str) -> dict[str, Any]:
    return _session(match_id).snapshot()


@router.get("/matches/{match_id}/stream")
async def stream(match_id: str, since: int | None = None, last_event_id: str | None = Header(default=None)) -> StreamingResponse:
    """The broadcast as Server-Sent Events. Reconnect with `Last-Event-ID` (or `?since=`) and nothing is missed or repeated."""
    s = _session(match_id)
    if since is None and last_event_id and last_event_id.isdigit():
        since = int(last_event_id)
    gen = s.hub.stream(hello={"match_id": match_id, "scoreboard": s.scoreboard()}, since=since)
    return StreamingResponse(gen, media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


class Control(BaseModel):
    op: str  # play | pause | speed | mode | jump
    value: Any = None


@router.post("/matches/{match_id}/control")
async def control(match_id: str, body: Control) -> dict[str, Any]:
    return _session(match_id).control(body.op, body.value)


@router.post("/matches/{match_id}/trigger")
async def trigger(match_id: str) -> dict[str, Any]:
    return _session(match_id).trigger_now()


class ChatIn(BaseModel):
    name: str = "you"
    text: str


@router.post("/matches/{match_id}/chat")
async def chat(match_id: str, body: ChatIn) -> dict[str, Any]:
    return _session(match_id).post_chat(body.name, body.text)


class FeedIn(BaseModel):
    """An external feed event (a provider webhook, normalised). `type` is a feed type such as goal, card.red, var.check."""

    type: str
    team: str | None = None
    player: str | None = None
    text: str | None = None
    urgent: bool = False
    tags: list[str] = []
    id: str | None = None


@router.post("/matches/{match_id}/events")
async def ingest(match_id: str, body: FeedIn, idempotency_key: str | None = Header(default=None)) -> JSONResponse:
    out = _session(match_id).ingest(body.model_dump(), key=idempotency_key or body.id)
    return JSONResponse(out, status_code=202 if out["accepted"] else 200)


class Vote(BaseModel):
    poll_id: str
    option: int


@router.post("/matches/{match_id}/vote")
async def vote(match_id: str, body: Vote) -> dict[str, Any]:
    return _session(match_id).vote(body.poll_id, body.option)


class Decision(BaseModel):
    id: str
    decision: str  # approve | edit | reject
    question: str | None = None
    options: list[str] | None = None
    text: str | None = None
    note: str | None = None


@router.post("/matches/{match_id}/decide")
async def decide(match_id: str, body: Decision) -> dict[str, Any]:
    edits = {k: v for k, v in body.model_dump().items() if k in ("question", "options", "text", "note") and v is not None}
    return await _session(match_id).decide(body.id, body.decision, edits)


@router.get("/matches/{match_id}/ledger")
async def ledger(match_id: str) -> dict[str, Any]:
    """Everything the producer decided this match, and why (the Store, not the session: it survives a restart)."""
    rows = await ledger_list(runtime().runner.store, match_id)
    return {"match_id": match_id, "count": len(rows), "entries": rows}


@router.get("/matches/{match_id}/threads")
async def threads(match_id: str) -> dict[str, Any]:
    """Studio links for this match's most recent windows (each window is its own thread)."""
    s = _session(match_id)
    ids = await links.assistant_ids()
    aid = ids.get("showrunner")
    out = []
    for seq in range(s.window_seq, max(0, s.window_seq - 8), -1):
        wid = f"w{seq:03d}"
        tid = thread_id(match_id, "window", wid)
        out.append({"window_id": wid, "thread_id": tid, "studio": links.studio_thread(tid, "showrunner", aid)})
    return {"match_id": match_id, "graph": "showrunner", "assistant_id": aid, "windows": out, "studio_base": links.studio_base()}


@router.get("/memory")
async def memory() -> dict[str, Any]:
    st = runtime().runner.store
    prefs = await prefs_all(st)
    return {"prefs": sorted(prefs.values(), key=lambda p: (-p.get("mean", 0), p.get("template", ""))), "lessons": await lessons_search(st, None, limit=50)}


@router.post("/memory/reset")
async def reset_memory() -> dict[str, Any]:
    return {"deleted": await memory_reset(runtime().runner.store)}


@router.get("/graphs")
async def graphs() -> list[dict[str, Any]]:
    return GRAPHS


@router.get("/links")
async def get_links(graph: str | None = None, thread_id: str | None = None) -> dict[str, Any]:
    return await links.links(graph if graph in GRAPH_IDS else None, thread_id)
