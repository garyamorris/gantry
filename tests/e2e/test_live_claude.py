"""LIVE, opt-in (`make live`, costs cents): one window on real Claude. Proves the provider path, not the content."""

from __future__ import annotations

import httpx
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.live]


def test_one_window_on_a_real_model(anthropic_server):
    with httpx.Client(base_url=anthropic_server, timeout=180) as c:
        boot = c.get("/api/boot").json()
        assert boot["provider"] == "anthropic"
        tid = c.post("/threads", json={}).json()["thread_id"]
        out = c.post(f"/threads/{tid}/runs/wait", json={"assistant_id": "showrunner", "input": {"fixture": "derby", "minute": 63, "mode": "autopilot"}}).json()
        assert out["summary"]["actions"].get("published"), out
        calls = c.get("/api/boot").json()["model_calls"]
        print("\n  model calls:", calls, "| models:", boot["models"])
        assert calls["calls"] >= 3, "the editor, the note and at least one writer reached the model"
        assert calls["fallbacks"] == 0, "no call fell back to the engine's draft"
