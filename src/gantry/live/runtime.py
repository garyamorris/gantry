"""The live runtime: the registry of broadcasts this process is running.

One `LiveRuntime` per process. The custom FastAPI app creates it lazily on the first request (by then the Agent
Server is up, so the in-process SDK client works); tests and `make demo` build one around a `LocalRunner`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from gantry.live.runner import GraphRunner
from gantry.live.session import Session, SessionError
from gantry.sim.fixtures import FIXTURES

MAX_SESSIONS = 6


class LiveRuntime:
    def __init__(self, runner_factory: Callable[[], GraphRunner]) -> None:
        self._factory = runner_factory
        self._runner: GraphRunner | None = None
        self.sessions: dict[str, Session] = {}

    @property
    def runner(self) -> GraphRunner:
        if self._runner is None:
            self._runner = self._factory()
        return self._runner

    async def create(self, *, fixture: str = "derby", seed: int = 7, mode: str = "autopilot", speed: float = 10, autostart: bool = True, match_id: str | None = None) -> Session:
        if fixture not in FIXTURES:
            raise SessionError(404, f"no fixture called {fixture!r}; try one of {', '.join(FIXTURES)}")
        if match_id and match_id in self.sessions:
            await self.remove(match_id)
        live = [s for s in self.sessions.values() if not s.ended]
        if len(live) >= MAX_SESSIONS:  # an old broadcast makes room for a new one
            await self.remove(min(live, key=lambda s: s.vt).match_id)
        s = Session(self.runner, fixture_id=fixture, seed=seed, mode=mode, speed=speed, match_id=match_id)
        self.sessions[s.match_id] = s
        if autostart:
            s.start()
        return s

    def get(self, match_id: str) -> Session:
        s = self.sessions.get(match_id)
        if s is None:
            raise SessionError(404, f"no live match {match_id!r} (the server restarted, or it was replaced): start a new one")
        return s

    async def remove(self, match_id: str) -> None:
        s = self.sessions.pop(match_id, None)
        if s is not None:
            await s.stop()

    async def close(self) -> None:
        for mid in list(self.sessions):
            await self.remove(mid)

    def list(self) -> list[dict[str, Any]]:
        return [{"match_id": s.match_id, "fixture": s.match["fixture"], "scoreboard": s.scoreboard()} for s in self.sessions.values()]
