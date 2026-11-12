"""Regression: `langgraph dev` applies the `.env` named in langgraph.json ON TOP of the shell environment (it patches os.environ with the
file's values), so a GANTRY_PROVIDER line in `.env` silently defeats `GANTRY_PROVIDER=openai make dev` (the documented way to pick a
provider). The shipped template must therefore leave the choice to the shell; the Settings default is already offline."""

from __future__ import annotations

from pathlib import Path

from gantry.settings import Settings

ROOT = Path(__file__).resolve().parents[2]


def _active_lines(path: Path) -> list[str]:
    return [ln.strip() for ln in path.read_text().splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def test_env_files_do_not_pin_the_provider():
    for name in (".env.example", ".env"):
        p = ROOT / name
        if p.exists():
            pinned = [ln for ln in _active_lines(p) if ln.startswith("GANTRY_PROVIDER")]
            assert not pinned, f"{name} pins {pinned}: it would override the shell under `langgraph dev`; choose the provider per run"


def test_provider_defaults_to_offline_without_any_env(monkeypatch):
    monkeypatch.delenv("GANTRY_PROVIDER", raising=False)
    assert Settings(_env_file=None).resolved_provider() == "offline"
