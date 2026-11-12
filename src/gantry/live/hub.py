"""The live event hub: everything the broadcast emits, as one ordered stream that browsers follow over SSE.

One hub per match. Every message gets a sequence number and goes into a ring buffer, so a client that reconnects
with `Last-Event-ID` is replayed exactly what it missed (no gaps, no duplicates) and a client that falls too far
behind is told so (`gap`) instead of silently losing messages. Scoreboard ticks are `transient`: sent to whoever is
listening, never stored, never replayed.

Frame:  ``id: <seq>\\nevent: <channel>\\ndata: {json}\\n\\n``.  Everything here runs on the event loop.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

CHANNELS = ("feed", "chat", "curated", "state", "poll", "pin", "highlight", "suggestion", "action", "rundown", "agent", "memory", "recap", "control")
HEARTBEAT = ": heartbeat\n\n"


def frame(channel: str, data: Any, seq: int | None = None) -> str:
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}event: {channel}\ndata: {json.dumps(data, separators=(',', ':'), default=str)}\n\n"


class Subscriber:
    def __init__(self, maxsize: int) -> None:
        self.buf: deque[tuple[int | None, str, Any]] = deque()
        self.maxsize = maxsize
        self.wake = asyncio.Event()
        self.dropped = 0
        self.closed = False

    def push(self, item: tuple[int | None, str, Any]) -> None:
        if len(self.buf) >= self.maxsize:
            self.buf.popleft()
            self.dropped += 1
        self.buf.append(item)
        self.wake.set()


class Hub:
    def __init__(self, ring: int = 4000, per_subscriber: int = 2000) -> None:
        self.seq = 0
        self.ring: deque[tuple[int, str, Any]] = deque(maxlen=ring)
        self.per_subscriber = per_subscriber
        self.subs: set[Subscriber] = set()
        self.published = 0

    def publish(self, channel: str, data: Any, *, transient: bool = False) -> int | None:
        self.published += 1
        seq: int | None = None
        if not transient:
            self.seq += 1
            seq = self.seq
            self.ring.append((seq, channel, data))
        for sub in list(self.subs):
            sub.push((seq, channel, data))
        return seq

    def backlog(self, after: int) -> list[tuple[int, str, Any]]:
        return [item for item in self.ring if item[0] > after]

    def close(self) -> None:
        for sub in list(self.subs):
            sub.closed = True
            sub.wake.set()
        self.subs.clear()

    async def stream(self, *, hello: dict[str, Any], since: int | None = None, heartbeat_s: float = 15.0) -> AsyncIterator[str]:
        """SSE frames for one client. Subscribes BEFORE replaying the backlog, then de-duplicates by seq."""
        sub = Subscriber(self.per_subscriber)
        self.subs.add(sub)
        try:
            yield frame("hello", {**hello, "seq": self.seq})
            last = since if since is not None else self.seq
            if since is not None:
                oldest = self.ring[0][0] if self.ring else self.seq + 1
                if since + 1 < oldest:
                    yield frame("gap", {"missed_from": since + 1, "missed_to": oldest - 1})
                for seq, channel, data in self.backlog(since):
                    last = seq
                    yield frame(channel, data, seq)
            while not sub.closed:
                if sub.dropped:
                    yield frame("gap", {"dropped": sub.dropped})
                    sub.dropped = 0
                if not sub.buf:
                    sub.wake.clear()
                    try:
                        await asyncio.wait_for(sub.wake.wait(), timeout=heartbeat_s)
                    except TimeoutError:
                        yield HEARTBEAT
                    continue
                seq, channel, data = sub.buf.popleft()
                if seq is not None:
                    if seq <= last:
                        continue
                    last = seq
                yield frame(channel, data, seq)
        finally:
            sub.closed = True
            self.subs.discard(sub)
