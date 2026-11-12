"""Custom FastAPI app mounted by `langgraph dev` via langgraph.json `http.app`.

Rules learned the hard way in the sibling projects: async only (blockbuster flags blocking calls on the server loop),
never `mount("/")` (it would swallow the built-in /threads /runs /assistants routes), never define /info, keep
`http.cors` unset, mount static under /app (NOT /ui, which belongs to LangGraph's generative-UI routes).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from gantry import __version__
from gantry.live.session import SessionError
from gantry.server import routes
from gantry.settings import get_settings

_STATIC_DIR = Path(__file__).parent / "static"


class StripPrivateNetworkRequest:
    """Drop Chrome's Private-Network-Access preflight header so langgraph-api's own middleware can answer it."""

    _HEADER = b"access-control-request-private-network"

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("method") == "OPTIONS":
            headers = [(k, v) for k, v in scope["headers"] if k.lower() != self._HEADER]
            if len(headers) != len(scope["headers"]):
                scope = {**scope, "headers": headers}
        await self.app(scope, receive, send)


class RevalidateStatic:
    """Make the SPA's files revalidate on every load (`Cache-Control: no-cache`; the ETag keeps it to a cheap 304), so a
    browser never keeps running yesterday's ES modules after an update. Only /app/* is touched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/app"):
            await self.app(scope, receive, send)
            return

        async def send_with_cache_control(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                headers.append((b"cache-control", b"no-cache"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cache_control)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Broadcasts are created on demand (POST /api/matches), by which time the Agent Server is up and the in-process
    # SDK client works; nothing to start here. On shutdown every live clock is stopped.
    try:
        yield
    finally:
        await routes.close_runtime()


def create_app() -> FastAPI:
    app = FastAPI(title="gantry custom routes", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(StripPrivateNetworkRequest)
    app.add_middleware(RevalidateStatic)
    app.add_exception_handler(SessionError, routes.session_error_handler)  # type: ignore[arg-type]

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/app/", status_code=307)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "provider": get_settings().resolved_provider(), "live_matches": len(routes.runtime().sessions)}

    app.include_router(routes.router)
    app.mount("/app", StaticFiles(directory=_STATIC_DIR, html=True, check_dir=False), name="app")
    return app


app = create_app()
