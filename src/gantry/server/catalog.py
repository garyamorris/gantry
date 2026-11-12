"""The seven graphs as the dashboard presents them (the Graphs drawer and /api/boot)."""

from __future__ import annotations

from typing import Any

GRAPHS: list[dict[str, Any]] = [
    {"id": "showrunner", "title": "Showrunner", "blurb": "THE LOOP. One live window end to end: intake, triage, curate, act, wrap. What the dispatcher runs on every trigger.",
     "features": ["subgraphs, three deep", "Command(goto) triage", "interrupts bubbling up", "a thread per window"]},
    {"id": "curator", "title": "Curator", "blurb": "FLOW 1, rank and curate. Scores every feed event and every chat message, finds the themes, and an editor re-ranks the shortlist.",
     "features": ["Send map-reduce over chat shards", "reducers", "defer", "structured output", "token streaming"]},
    {"id": "action_desk", "title": "Action Desk", "blurb": "FLOW 2, generate actions. Recalls what was done, proposes, screens against policy and memory, asks or publishes, remembers.",
     "features": ["Store + semantic search", "parallel interrupts", "resume by id", "Send per action", "idempotent side effects"]},
    {"id": "poll_studio", "title": "Poll Studio", "blurb": "THE FIRST FEATURE. Three writers draft a poll for a live moment; a judge scores; failures get notes and another go.",
     "features": ["Send x3", "evaluator loop", "Command(goto) with Send", "structured output with fallback"]},
    {"id": "memory_keeper", "title": "Memory Keeper", "blurb": "Learns from every closed poll: what this audience votes on, and a written lesson when the evidence is strong.",
     "features": ["Store namespaces", "cross-thread memory", "memory-write interrupt"]},
    {"id": "recap_press", "title": "Recap Press", "blurb": "Half-time and full-time recaps, from the ledger. The full-time one remembers the half-time one.",
     "features": ["Functional API", "@entrypoint / @task", "previous", "entrypoint.final"]},
    {"id": "producer_chat", "title": "Producer Chat", "blurb": "Ask the producer what it did and why. Three tools over the ledger and memory.",
     "features": ["create_agent + middleware", "ToolRuntime", "context_schema", "Studio chat mode"]},
]
GRAPH_IDS = [g["id"] for g in GRAPHS]
