"""Shared fixtures. Graphs are compiled WITH an in-memory checkpointer + store, exactly like the server does."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

# A developer's .env (real keys, GANTRY_PROVIDER=anthropic) must NEVER leak into the suite: tests are offline, free and
# deterministic. Real environment variables outrank the .env file in pydantic-settings, so pinning them here is enough.
os.environ["GANTRY_PROVIDER"] = "offline"
os.environ["GANTRY_OFFLINE_TOKEN_DELAY_S"] = "0"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from gantry.embeddings import aembed
from gantry.models import offline


@pytest.fixture
def saver() -> InMemorySaver:
    return InMemorySaver()


@pytest.fixture
def store() -> InMemoryStore:
    """A Store with the semantic index the real server configures in langgraph.json (hashed 256-d embedder)."""
    return InMemoryStore(index={"embed": aembed, "dims": 256, "fields": ["text"]})


@pytest.fixture(autouse=True)
def _clean_overrides():
    yield
    offline.SCRIPT_OVERRIDES.clear()
