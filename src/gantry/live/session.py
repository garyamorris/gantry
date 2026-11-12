"""One live broadcast: the clock, the feed, the chat, the dispatcher, and the consequences.

The session is the real-time half of the system. It owns virtual time, releases feed events and chat messages as
the clock passes them, decides WHEN the graphs should look (the dispatcher), and applies WHAT they decide (opens the
poll, pins the comment, labels the chat). Graphs never touch the session and the session never decides content.

The dispatcher is event-driven, not polling:
  * a key feed event (goal, red card, VAR) arms a window `REACTION_DELAY` seconds later, so the chat's reaction is in it
  * an urgent one (a penalty is about to be taken) fires almost at once: that poll is worthless in ninety seconds
  * the half-time and full-time whistles fire their own windows
  * a quiet `TICK_EVERY` seconds fires a tick, because a lull is when a stream needs a producer most
Windows are serialised (one `showrunner` run in flight per match). If the graphs fall behind the clock, due windows
coalesce into the next one instead of queueing: live systems shed load, they do not build a backlog. `lag` says how
far behind. Each window is its own thread, so a suggestion waiting on a human never blocks the broadcast.

`tick(real_dt)` drives it from the server loop; `step(vsec)` drives it headless (tests, evals, `make demo`).
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections.abc import Coroutine
from typing import Any

from gantry.engine import policy, ranking
from gantry.live.hub import Hub
from gantry.live.runner import GraphRunner, thread_id
from gantry.live.windows import build_window, timeline_of
from gantry.memory.ledger import ledger_patch
from gantry.sim import audience
from gantry.sim import chat as chat_sim
from gantry.sim import match as match_sim

log = logging.getLogger("gantry.live")

REACTION_DELAY = 25  # virtual seconds to let the chat react before looking
URGENT_DELAY = 6
PHASE_DELAY = 12
TICK_EVERY = 180
MIN_SPACING = 40
MAX_WAIT = 60  # never hold a window longer than this past its first trigger
PREROLL = -6.0
POSTROLL = 210  # keep the broadcast open after the whistle for the last poll
STALE_GRACE = 15
REPEAT_WINDOW = 150  # a line repeated within this many seconds is folded on arrival
CARRY = 200  # how far back a key moment is carried into later windows
SPEEDS = (1, 3, 6, 10, 20, 40)
HOST = {"id": "host", "name": "Gantry", "badge": "host", "color": "#ffffff"}


class SessionError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


class Session:
    def __init__(self, runner: GraphRunner, *, fixture_id: str = "derby", seed: int = 7, mode: str = "autopilot", speed: float = 10, match_id: str | None = None) -> None:
        if mode not in policy.MODES:
            raise SessionError(400, f"mode must be one of {', '.join(policy.MODES)}")
        self.runner = runner
        self.match = match_sim.simulate(fixture_id, seed)
        self.fixture_id, self.seed = fixture_id, int(seed)
        self.match_id = match_id or f"{fixture_id}-{seed}-{uuid.uuid4().hex[:6]}"
        self.tm = self.match["timing"]
        self.chat = chat_sim.chat_for(self.match)
        self.hub = Hub()
        self.mode, self.speed, self.paused, self.ended = mode, float(speed), True, False
        self.vt = PREROLL
        self.feed_i = self.chat_i = 0
        self.events_out: list[dict[str, Any]] = []
        self.chat_out: list[dict[str, Any]] = []
        self.chat_by_id: dict[str, dict[str, Any]] = {}
        self.labels: dict[str, dict[str, Any]] = {}
        self.real_n = 0
        self.overlay = {"home": 0, "away": 0}
        self.ingested: set[str] = set()
        # consequences
        self.poll: dict[str, Any] | None = None
        self.polls: list[dict[str, Any]] = []
        self.last_poll_closed_vt: int | None = None
        self.pinned: dict[str, Any] | None = None
        self.highlights: list[dict[str, Any]] = []
        self.suggestions: dict[str, dict[str, Any]] = {}
        self.actions: list[dict[str, Any]] = []
        self.rundowns: list[dict[str, Any]] = []
        self.lessons: list[dict[str, Any]] = []
        self.recaps: list[dict[str, Any]] = []
        # dispatcher
        self.window_seq = 0
        self.last_window_to = 0
        self.last_fire_vt = -999.0
        self.due: dict[str, Any] | None = None
        self.window_running = False
        self.agent: dict[str, Any] = {"running": False, "window_id": None, "label": "Waiting for kick-off"}
        self.stats = {"windows": 0, "skipped": 0, "errors": 0, "lag": 0, "max_lag": 0, "coalesced": 0, "run_ms": 0, "published": 0, "suppressed": 0, "expired": 0, "dropped": 0}
        self._moments: dict[str, list[dict[str, Any]]] = {}
        self._seen: set[str] = set()
        self._recent_keys: dict[str, float] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._loop_task: asyncio.Task[Any] | None = None
        self._state_pub = 0.0

    # ------------------------------------------------------------------------------------------------- views
    def state(self) -> dict[str, Any]:
        st = match_sim.state_at(self.match, max(self.vt, 0))
        if self.vt < 0:
            st.update(phase="pre", minute="KO")
        st["score"] = {s: st["score"][s] + self.overlay[s] for s in ("home", "away")}
        return st

    def viewers(self) -> int:
        t = max(self.vt, 0) / self.tm["end_vt"]
        return int(self.match["viewers"] * (0.82 + 0.18 * min(1.0, t)) * (1 + 0.03 * math.sin(self.vt / 97)))

    def scoreboard(self) -> dict[str, Any]:
        st = self.state()
        return {"vt": round(self.vt, 1), "minute": st["minute"], "phase": "ended" if self.ended else st["phase"], "period": st["period"], "score": st["score"],
                "stats": st["stats"], "viewers": self.viewers(), "speed": self.speed, "paused": self.paused, "mode": self.mode, "end_vt": self.tm["end_vt"],
                "agent": self.agent, "lag": self.stats["lag"], "pending": len(self.suggestions), "ended": self.ended}

    def _chat_view(self, m: dict[str, Any]) -> dict[str, Any]:
        out = chat_sim.public(m, now_vt=self.vt) if "truth" in m else dict(m)
        lab = self.labels.get(m["id"])
        if lab:
            out["curation"] = lab
        return out

    def snapshot(self) -> dict[str, Any]:
        key = [{"id": e["id"], "vt": e["vt"], "type": e["type"], "minute": e["minute"], "team": e["team"], "text": e["text"]} for e in self.events_out
               if ranking.BASE.get(e["type"], 0) >= 0.4 and e["type"] != "match.fulltime"]
        return {
            "match_id": self.match_id, "fixture": self.match["fixture"], "seed": self.seed, "scoreboard": self.scoreboard(), "timing": self.tm, "speeds": list(SPEEDS),
            "feed": self.events_out[-80:], "chat": [self._chat_view(m) for m in self.chat_out[-150:]], "markers": key, "poll": self.poll, "polls": self.polls[-12:],
            "pinned": self.pinned, "highlights": self.highlights[-20:], "suggestions": [self._suggestion_view(s) for s in self.suggestions.values()],
            "actions": self.actions[-80:], "rundowns": self.rundowns[-12:], "lessons": self.lessons[-10:], "recaps": self.recaps[-2:], "stats": self.stats, "seq": self.hub.seq,
        }

    @staticmethod
    def _suggestion_view(s: dict[str, Any]) -> dict[str, Any]:
        return {k: s[k] for k in ("id", "action", "window_id", "created_vt", "expires_vt", "deciding") if k in s}

    # ------------------------------------------------------------------------------------------------- clock
    def start(self) -> None:
        """Run on the server's event loop in real time."""
        if self._loop_task is None:
            self.paused = False
            self._loop_task = asyncio.get_running_loop().create_task(self._run(), name=f"gantry-{self.match_id}")

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        last = loop.time()
        while not self.ended:
            await asyncio.sleep(0.1)
            now = loop.time()
            try:
                self.tick(min(now - last, 0.5))
            except Exception:  # the clock must survive anything a single tick throws
                log.exception("tick failed")
                self.stats["errors"] += 1
            last = now
            if now - self._state_pub >= 0.5:
                self._state_pub = now
                self.hub.publish("state", self.scoreboard(), transient=True)

    def tick(self, real_dt: float) -> None:
        if not self.paused and not self.ended:
            self.vt += real_dt * self.speed
            self._process()

    async def step(self, vsec: float, dt: float = 2.0) -> None:
        """Headless: advance virtual time in small steps, finishing every graph run before the next step (deterministic)."""
        target = self.vt + vsec
        while self.vt < target and not self.ended:
            self.vt = min(target, self.vt + dt)
            self._process()
            await self.drain()

    async def play_to_end(self) -> None:
        self.paused = False
        while not self.ended:
            await self.step(60)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def stop(self) -> None:
        self.ended = True
        if self._loop_task:
            self._loop_task.cancel()
        for t in list(self._tasks):
            t.cancel()
        self.hub.close()

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def control(self, op: str, value: Any = None) -> dict[str, Any]:
        if op == "play":
            self.paused = False
        elif op == "pause":
            self.paused = True
        elif op == "speed":
            self.speed = max(0.5, min(60.0, float(value)))
        elif op == "mode":
            if value not in policy.MODES:
                raise SessionError(400, f"mode must be one of {', '.join(policy.MODES)}")
            self.mode = value
        elif op == "jump":  # to just before the next key moment
            nxt = next((e for e in self.match["events"][self.feed_i:] if ranking.BASE.get(e["type"], 0) >= 0.4 and e["vt"] > self.vt + 30), None)
            if nxt:
                self.vt = max(self.vt, nxt["vt"] - 12)
                self.last_window_to = max(self.last_window_to, int(self.vt) - 150)
                self._process()
        else:
            raise SessionError(400, f"unknown control {op!r}")
        self.hub.publish("control", {"op": op, "value": value, "scoreboard": self.scoreboard()})
        return self.scoreboard()

    # ---------------------------------------------------------------------------------------------- the tick
    def _process(self) -> None:
        self._release_feed()
        self._release_chat()
        self._poll_tick()
        if self.pinned and self.vt >= self.pinned["until_vt"]:
            self.pinned = None
            self.hub.publish("pin", None)
        for s in list(self.suggestions.values()):
            if s.get("expires_vt") is not None and self.vt >= s["expires_vt"] and not s.get("deciding"):
                s["deciding"] = True
                self._spawn(self._resume(s, {"decision": "expire"}))
        self._dispatch()
        if self.vt >= self.tm["end_vt"] + POSTROLL and not self.poll and not self.window_running and not self.ended:
            for s in list(self.suggestions.values()):  # nobody answered before the broadcast ended: close them out, with a record
                if not s.get("deciding"):
                    s["deciding"] = True
                    self._spawn(self._resume(s, {"decision": "expire"} if s["action"]["kind"] != "memory_write" else {"decision": "discard"}))
            self.ended = True
            self.agent = {"running": False, "window_id": None, "label": "Broadcast over"}
            self.hub.publish("control", {"op": "ended", "scoreboard": self.scoreboard()})

    def _release_feed(self) -> None:
        evs = self.match["events"]
        while self.feed_i < len(evs) and evs[self.feed_i]["vt"] <= self.vt:
            ev = match_sim.public(evs[self.feed_i])
            ev["score"] = {s: ev["score"][s] + self.overlay[s] for s in ("home", "away")}
            self.feed_i += 1
            self._emit_feed(ev)

    def _emit_feed(self, ev: dict[str, Any]) -> None:
        self.events_out.append(ev)
        self.hub.publish("feed", ev)
        t = ev["type"]
        if t in ("match.halftime", "match.fulltime"):
            self._arm(ev["vt"] + PHASE_DELAY, "phase", ev, force=True)
            self._spawn(self._recap("halftime" if t == "match.halftime" else "fulltime"))
        elif ev.get("urgent"):
            self._arm(ev["vt"] + URGENT_DELAY, "urgent", ev, force=True)
        elif ranking.BASE.get(t, 0.05) >= 0.4:
            self._arm(ev["vt"] + REACTION_DELAY, "event", ev)

    def _release_chat(self) -> None:
        while self.chat_i < len(self.chat) and self.chat[self.chat_i]["vt"] <= self.vt:
            self._emit_chat(self.chat[self.chat_i])
            self.chat_i += 1

    def _emit_chat(self, m: dict[str, Any]) -> None:
        """Release one message. Safety is inline (the cheap screen holds spam and abuse at once); ranking comes later, in the window."""
        self.chat_out.append(m)
        self.chat_by_id[m["id"]] = m
        quick = ranking.score_comment(chat_sim.public(m) if "truth" in m else m, {"players": [], "teams": []}, {})
        last = self._recent_keys.get(quick["key"])
        self._recent_keys[quick["key"]] = m["vt"]
        if quick["label"] == "hide":
            self.labels[m["id"]] = {"label": "hide", "reason": quick["reasons"][0], "by": "screen"}
        elif last is not None and m["vt"] - last <= REPEAT_WINDOW and quick["key"]:
            self.labels[m["id"]] = {"label": "collapse", "reason": "repeat", "by": "screen"}  # the same line again within minutes: folded at once
        self.hub.publish("chat", self._chat_view(m))

    # ------------------------------------------------------------------------------------------ the dispatcher
    def _arm(self, at: float, kind: str, ev: dict[str, Any], force: bool = False) -> None:
        at = max(at, self.vt)  # a trigger for an event the clock jumped over fires now; `lag` then measures the graphs, not the skip
        cur = self.due
        if cur is None:
            self.due = {"vt": at, "first_vt": at, "kind": kind, "event_id": ev["id"], "reason": ev["type"]}
        elif force:
            cur.update(vt=min(cur["vt"], at), kind=kind, event_id=ev["id"], reason=ev["type"])
        else:  # another key event while armed: wait for ITS reaction too, within reason
            cur.update(vt=min(max(cur["vt"], at), cur["first_vt"] + MAX_WAIT), event_id=ev["id"], reason=ev["type"])
            if cur["kind"] == "tick":
                cur["kind"] = "event"

    def _dispatch(self) -> None:
        phase = match_sim.clock_at(self.tm, self.vt)["phase"]
        if self.due is None and phase in ("first_half", "second_half") and self.vt - max(self.last_window_to, self.last_fire_vt) >= TICK_EVERY:
            self.due = {"vt": self.vt, "first_vt": self.vt, "kind": "tick", "event_id": None, "reason": "quiet spell"}
        due = self.due
        if due is None or self.vt < due["vt"]:
            return
        if self.window_running:
            if not due.get("counted"):
                due["counted"] = True
                self.stats["coalesced"] += 1  # the graphs are behind the clock: this trigger rides along with the next window
            return
        if due["kind"] in ("event", "tick", "retry") and self.vt - self.last_fire_vt < MIN_SPACING:
            return
        self._fire(due)

    def _live(self) -> dict[str, Any]:
        p = self.poll
        return {"active_poll": {"id": p["id"], "closes_vt": p["closes_vt"], "opened_vt": p["opened_vt"], "priority": p.get("priority", 0.5)} if p else None,
                "pinned": {"id": self.pinned["comment"]["id"]} if self.pinned else None, "last_poll_closed_vt": self.last_poll_closed_vt}

    def _fire(self, due: dict[str, Any]) -> None:
        self.due = None
        self.window_seq += 1
        now = int(self.vt)
        lag = max(0, now - int(due["vt"]))
        self.stats["lag"], self.stats["max_lag"] = lag, max(self.stats["max_lag"], lag)
        frm = self.last_window_to
        # new events, plus recent key moments that may still be owed something (a poll that lost its slot is retried while fresh)
        events = [e for e in self.events_out if frm < e["vt"] <= now or (now - CARRY < e["vt"] <= frm and ranking.BASE.get(e["type"], 0) >= 0.4)]
        comments = [chat_sim.public(m, now_vt=now) if "truth" in m else m for m in self.chat_out if frm < m["vt"] <= now and not m.get("host")]
        baseline = len(self.chat_out) / max(now / 60.0, 1.0)
        inp = build_window(
            match_id=self.match_id, seq=self.window_seq, fixture=self.match["fixture"], state=self.state(), events=events, comments=comments,
            all_events=self.events_out, from_vt=frm, to_vt=now, baseline_per_min=baseline, mode=self.mode, live=self._live(), viewers=self.viewers(),
            trigger={"kind": due["kind"], "event_id": due["event_id"], "reason": due["reason"], "lag": lag},
        )
        self.last_window_to, self.last_fire_vt, self.window_running = now, self.vt, True
        self.agent = {"running": True, "window_id": inp["window"]["id"], "label": "Looking", "trigger": inp["window"]["trigger"]}
        self._spawn(self._run_window(inp))

    def trigger_now(self) -> dict[str, Any]:
        """A human asks for a look right now."""
        if self.vt < 0 or self.ended:
            raise SessionError(409, "the broadcast is not live")
        self.due = {"vt": self.vt, "first_vt": self.vt, "kind": "manual", "event_id": None, "reason": "asked by the producer"}
        if not self.window_running:
            self._fire(self.due)
        return {"queued": self.window_running, "window": self.window_seq}

    def _lock(self, thread: str) -> asyncio.Lock:
        return self._locks.setdefault(thread, asyncio.Lock())

    async def _run_window(self, inp: dict[str, Any]) -> None:
        wid = inp["window"]["id"]
        thread = thread_id(self.match_id, "window", wid)
        t0 = asyncio.get_running_loop().time()
        try:
            async with self._lock(thread):
                meta = {"match_id": self.match_id, "window": wid, "trigger": inp["window"]["trigger"]["kind"], "app": "gantry"}
                async for payload in self.runner.run("showrunner", thread, input=inp, metadata=meta):
                    self._on_agent(payload, thread, "showrunner", wid)
                await self._collect_interrupts("showrunner", thread, wid)
            self.stats["windows"] += 1
        except Exception as exc:
            log.exception("window %s failed", wid)
            self.stats["errors"] += 1
            self.hub.publish("agent", {"kind": "error", "key": f"{wid}:error", "agent": "runtime", "data": {"text": f"{type(exc).__name__}: {str(exc)[:200]}"}, "window_id": wid, "vt": int(self.vt)})
        finally:
            self.window_running = False
            self.stats["run_ms"] = int((asyncio.get_running_loop().time() - t0) * 1000)
            self.agent = {"running": False, "window_id": wid, "label": "Watching"}
            if not self.ended:
                self._dispatch()

    async def _collect_interrupts(self, graph: str, thread: str, wid: str | None) -> None:
        """Pending interrupts of a thread become suggestions in the inbox (and learn their interrupt id, needed to resume by id)."""
        for it in await self.runner.interrupts(graph, thread):
            val = it.get("value") or {}
            if val.get("kind") == "approve_action":
                a = val["action"]
                s = self.suggestions.get(a["id"])
                if s is None:
                    s = self._add_suggestion(a, thread, graph, wid)
                s["interrupt_id"] = it["id"]
            elif val.get("kind") == "memory_write":
                lesson = val["lesson"]
                sid = f"lesson:{lesson['id']}:{thread[:8]}"
                if sid not in self.suggestions:
                    action = {"id": sid, "kind": "memory_write", "topic": lesson["id"], "title": "Remember this?", "text": lesson["text"], "evidence": lesson["evidence"]}
                    self._add_suggestion(action, thread, graph, wid)["interrupt_id"] = it["id"]

    def _add_suggestion(self, action: dict[str, Any], thread: str, graph: str, wid: str | None) -> dict[str, Any]:
        s = {"id": action["id"], "action": action, "thread": thread, "graph": graph, "interrupt_id": None, "window_id": wid, "created_vt": int(self.vt),
             "expires_vt": action.get("expires_vt"), "deciding": False}
        self.suggestions[action["id"]] = s
        self.hub.publish("suggestion", {"op": "add", "suggestion": self._suggestion_view(s)})
        return s

    def _drop_suggestion(self, sid: str) -> None:
        if self.suggestions.pop(sid, None) is not None:
            self.hub.publish("suggestion", {"op": "remove", "id": sid})

    # ------------------------------------------------------------------------- what the graphs say, applied
    def _on_agent(self, payload: dict[str, Any], thread: str, graph: str, wid: str | None) -> None:
        key = payload.get("key")
        if key in self._seen:  # a resumed node re-emits everything above its interrupt: deterministic keys make that harmless
            return
        self._seen.add(key)
        kind, d = payload["kind"], payload.get("data") or {}
        self.hub.publish("agent", {**payload, "window_id": wid, "graph": graph, "vt": int(self.vt), "minute": self.state()["minute"]})
        if kind == "moment.ranked" and wid:
            self._moments.setdefault(wid, []).append(d)
        elif kind == "progress":
            self.agent = {**self.agent, "label": d.get("label", "Working")}
        elif kind == "curation.result":
            self._apply_curation(d, wid)
        elif kind == "action.pending":
            a = d["action"]
            if a["id"] not in self.suggestions:
                self._add_suggestion(a, thread, graph, wid)
        elif kind == "action.publish":
            self._apply(d["action"], d.get("by", "autopilot"), bool(d.get("edited")))
        elif kind == "action.closed":
            self._drop_suggestion(d["id"])
            self._log_action({"id": d["id"], "kind": d["kind"], "topic": d["topic"], "status": d["status"], "reason": d["reason"], "text": d.get("question") or d.get("text")})
            if d.get("retry_vt") and not self.ended:  # the policy said "not now": come back when the slot is free
                at = max(float(d["retry_vt"]), self.vt + 5)
                if self.due is None or at < self.due["vt"]:
                    self.due = {"vt": at, "first_vt": at, "kind": "retry", "event_id": None, "reason": f"retry: {d['topic']}"}
        elif kind in ("window.skip", "window.done"):
            self.stats["skipped"] += kind == "window.skip"
        elif kind in ("memory.outcome", "memory.pref", "memory.lesson"):
            if kind == "memory.lesson":
                self.lessons.append(d)
            self.hub.publish("memory", {"kind": kind, **d})
        elif kind == "recap.section":
            self.recaps.append(d)
            self.hub.publish("recap", d)
            self._host_say(d["text"], tag="recap")

    def _apply_curation(self, d: dict[str, Any], wid: str | None) -> None:
        changed: dict[str, dict[str, Any]] = {}
        for cid in d.get("highlight") or []:
            changed[cid] = {"label": "highlight", "by": "editor"}
        for h in d.get("hidden") or []:
            changed[h["id"]] = {"label": "hide", "reason": h["reason"], "by": "curator"}
        for cid in d.get("collapsed") or []:
            changed.setdefault(cid, {"label": "collapse", "reason": "repeated chant", "by": "curator"})
        self.labels.update(changed)
        self.hub.publish("curated", {"window_id": wid, "labels": changed})
        st = self.state()
        rundown = {
            "window_id": wid, "vt": int(self.vt), "minute": st["minute"], "note": d.get("note"), "heat": d.get("heat"), "themes": d.get("themes") or [],
            "moments": self._moments.pop(wid or "", []), "highlights": [self._chat_view(self.chat_by_id[c]) for c in d.get("highlight") or [] if c in self.chat_by_id],
            "hidden": d.get("hidden") or [], "collapsed": len(d.get("collapsed") or []),
        }
        self.rundowns.append(rundown)
        self.hub.publish("rundown", rundown)

    def _log_action(self, rec: dict[str, Any]) -> None:
        rec = {**rec, "vt": int(self.vt), "minute": self.state()["minute"]}
        if rec["status"] in self.stats:
            self.stats[rec["status"]] += 1
        self.actions.append(rec)
        self.hub.publish("action", rec)

    def _apply(self, a: dict[str, Any], by: str, edited: bool) -> None:
        """Make a decided action real. The runtime has the last word on timing: a poll that arrives late or collides is dropped."""
        self._drop_suggestion(a["id"])
        now, kind = int(self.vt), a["kind"]
        base = {"id": a["id"], "kind": kind, "topic": a["topic"], "by": by, "edited": edited, "text": a.get("question") or a.get("title") or a.get("text"), "why": a.get("why")}
        if kind == "poll":
            reason = None
            if self.poll and a.get("preempt"):
                self._close_poll(early="closed early for a bigger moment")
            if self.poll:
                reason = "another poll was already live"
            elif a.get("expires_vt") is not None and now > a["expires_vt"] + STALE_GRACE:
                reason = "the moment had passed by the time it was ready"
            if reason:
                self._spawn(ledger_patch(self.runner.store, self.match_id, a["id"], status="dropped", reason=reason))
                self._log_action({**base, "status": "dropped", "reason": reason})
                return
            self._open_poll(a, by)
        elif kind == "pin_comment":
            self.pinned = {"comment": a["comment"], "until_vt": now + int(a.get("duration_s", 90)), "action_id": a["id"]}
            self.hub.publish("pin", self.pinned)
        elif kind == "highlight":
            card = {"id": a["id"], "title": a["title"], "text": a["text"], "event_id": a.get("event_id"), "vt": a.get("event_vt", now), "minute": a.get("minute")}
            self.highlights.append(card)
            self.hub.publish("highlight", card)
        elif kind == "host_message":
            self._host_say(a["text"])
        self._log_action({**base, "status": "published", "reason": a.get("why")})

    def _host_say(self, text: str, tag: str = "host") -> None:
        self.real_n += 1
        m = {"id": f"h{self.real_n:04d}", "vt": int(self.vt), "author": HOST, "text": text, "likes": 0, "host": tag}
        self.chat_out.append(m)
        self.chat_by_id[m["id"]] = m
        self.hub.publish("chat", m)

    # ---------------------------------------------------------------------------------------------------- polls
    def _open_poll(self, a: dict[str, Any], by: str) -> None:
        now = int(self.vt)
        used = sum(1 for p in self.polls if p["template"] == a.get("template"))
        heat = self.rundowns[-1]["heat"] if self.rundowns else 1.0
        viewers = self.viewers()
        part = audience.participation(a, seed=self.seed, opened_vt=now, event_vt=a.get("event_vt"), last_closed_vt=self.last_poll_closed_vt, times_used=used, heat=heat or 1.0)
        self.poll = {
            "id": a["id"], "question": a["question"], "options": a["options"], "template": a.get("template"), "topic": a["topic"], "by": by, "status": "open",
            "priority": a.get("priority", 0.5),
            "opened_vt": now, "closes_vt": now + int(a.get("duration_s", 75)), "duration_s": int(a.get("duration_s", 75)), "event_vt": a.get("event_vt"),
            "minute": self.state()["minute"], "viewers": viewers, "votes": [0] * len(a["options"]), "total": 0, "real_votes": [0] * len(a["options"]),
            "_target": int(viewers * part), "_shares": audience.split(a, seed=self.seed, state=self.state()), "_pub": -1,
        }
        self.hub.publish("poll", self._poll_view(self.poll))

    @staticmethod
    def _poll_view(p: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in p.items() if not k.startswith("_")}

    def _poll_tick(self) -> None:
        p = self.poll
        if p is None:
            return
        elapsed = min(self.vt - p["opened_vt"], p["duration_s"])
        sim = audience.votes_at(p["_target"], p["_shares"], elapsed, p["duration_s"])
        p["votes"] = [s + r for s, r in zip(sim, p["real_votes"], strict=True)]
        p["total"] = sum(p["votes"])
        closing = self.vt >= p["closes_vt"]
        if closing or p["total"] - p["_pub"] >= max(1, p["_target"] // 50):
            p["_pub"] = p["total"]
            if closing:
                self._close_poll()
            else:
                self.hub.publish("poll", self._poll_view(p))

    def _close_poll(self, early: str | None = None) -> None:
        p = self.poll
        assert p is not None
        p["status"], p["closed_vt"], p["closed_early"] = "closed", int(self.vt), early
        p["participation"] = round(p["total"] / max(p["viewers"], 1), 4)
        self.poll, self.last_poll_closed_vt = None, int(self.vt)
        view = self._poll_view(p)
        self.polls.append(view)
        self.hub.publish("poll", view)
        total = max(p["total"], 1)
        parts = " · ".join(f"{o} {round(100 * v / total)}%" for o, v in zip(p["options"], p["votes"], strict=True))
        self._host_say(f"📊 Poll closed: “{p['question']}” {parts} ({p['total']:,} votes)", tag="poll")
        result = {k: view[k] for k in ("id", "topic", "template", "question", "options", "votes", "total", "viewers", "participation", "opened_vt", "closed_vt", "event_vt", "minute", "by")}
        self._spawn(self._learn(result))

    def vote(self, poll_id: str, option: int) -> dict[str, Any]:
        p = self.poll
        if p is None or p["id"] != poll_id:
            raise SessionError(409, "that poll is not open")
        if not 0 <= option < len(p["options"]):
            raise SessionError(400, "no such option")
        p["real_votes"][option] += 1
        self._poll_tick()
        if self.poll is not None:
            self.hub.publish("poll", self._poll_view(self.poll))
        return self._poll_view(p)

    async def _learn(self, result: dict[str, Any]) -> None:
        """A poll closed: `memory_keeper` files the result and updates what the producer knows about this audience."""
        thread = thread_id(self.match_id, "keeper", result["id"])
        try:
            async with self._lock(thread):
                inp = {"match_id": self.match_id, "mode": self.mode, "poll_result": result}
                async for payload in self.runner.run("memory_keeper", thread, input=inp, metadata={"match_id": self.match_id, "app": "gantry", "poll": result["id"]}):
                    self._on_agent(payload, thread, "memory_keeper", None)
                await self._collect_interrupts("memory_keeper", thread, None)
        except Exception:
            log.exception("memory_keeper failed for %s", result["id"])
            self.stats["errors"] += 1

    async def _recap(self, kind: str) -> None:
        thread = thread_id(self.match_id, "recap", "match")
        try:
            async with self._lock(thread):
                inp = {"match_id": self.match_id, "kind": kind, "match_state": {**self.state(), "fixture": self.match["fixture"]}, "timeline": timeline_of(self.events_out, self.vt)}
                async for payload in self.runner.run("recap_press", thread, input=inp, metadata={"match_id": self.match_id, "app": "gantry", "recap": kind}):
                    self._on_agent(payload, thread, "recap_press", None)
        except Exception:
            log.exception("recap_press failed")
            self.stats["errors"] += 1

    # ------------------------------------------------------------------------------------------------ humans
    async def decide(self, sid: str, decision: str, edits: dict[str, Any] | None = None) -> dict[str, Any]:
        """A human answers a suggestion: approve, edit or reject. Resumes that action's interrupt, by id."""
        s = self.suggestions.get(sid)
        if s is None:
            raise SessionError(404, "that suggestion is gone (answered, expired or superseded)")
        if decision not in ("approve", "edit", "reject"):
            raise SessionError(400, "decision must be approve, edit or reject")
        if s.get("deciding"):
            raise SessionError(409, "already being decided")
        a = s["action"]
        if a["kind"] == "poll" and decision != "reject" and self.poll and not a.get("preempt"):
            raise SessionError(409, "A poll is live. Approve this one when it closes, or reject it.")
        if s.get("interrupt_id") is None:
            await self._collect_interrupts(s["graph"], s["thread"], s.get("window_id"))
            if s.get("interrupt_id") is None:
                raise SessionError(409, "not ready yet: the graph is still getting to it")
        if a["kind"] == "memory_write":
            value: dict[str, Any] = {"decision": "keep" if decision != "reject" else "discard"}
        else:
            value = {"decision": decision, "by": "human", **{k: v for k, v in (edits or {}).items() if k in ("question", "options", "text", "note")}}
        s["deciding"] = True
        self.hub.publish("suggestion", {"op": "update", "suggestion": self._suggestion_view(s)})
        self._spawn(self._resume(s, value))
        return {"ok": True, "id": sid, "decision": decision}

    async def _resume(self, s: dict[str, Any], value: dict[str, Any]) -> None:
        try:
            async with self._lock(s["thread"]):
                if s.get("interrupt_id") is None:
                    await self._collect_interrupts(s["graph"], s["thread"], s.get("window_id"))
                if s.get("interrupt_id") is None:
                    self._drop_suggestion(s["id"])
                    return
                meta = {"match_id": self.match_id, "app": "gantry", "resume": value.get("decision")}
                async for payload in self.runner.run(s["graph"], s["thread"], resume={s["interrupt_id"]: value}, metadata=meta):
                    self._on_agent(payload, s["thread"], s["graph"], s.get("window_id"))
                self._drop_suggestion(s["id"])
        except Exception as exc:
            log.exception("resume failed for %s", s["id"])
            self.stats["errors"] += 1
            s["deciding"] = False
            self.hub.publish("agent", {"kind": "error", "key": f"{s['id']}:resume-error:{int(self.vt)}", "agent": "runtime", "data": {"text": f"{type(exc).__name__}: {str(exc)[:200]}"}, "vt": int(self.vt)})

    def post_chat(self, name: str, text: str) -> dict[str, Any]:
        """A real viewer types in the chat. It is screened, ranked and curated exactly like everyone else's."""
        text = " ".join(str(text).split())[:200]
        if not text:
            raise SessionError(400, "say something")
        self.real_n += 1
        m = {"id": f"r{self.real_n:04d}", "vt": int(self.vt), "author": {"id": "you", "name": (str(name).strip() or "you")[:24], "badge": None, "color": "#22d3ee"}, "text": text, "likes": 0, "real": True}
        self._emit_chat(m)
        return self._chat_view(m)

    def ingest(self, ev: dict[str, Any], key: str | None = None) -> dict[str, Any]:
        """An external feed event arrives (a provider's webhook). Idempotent by `key`; from here on it is a feed event like any other."""
        key = key or ev.get("id")
        if key and key in self.ingested:
            return {"accepted": False, "duplicate": True, "id": key}
        t = str(ev.get("type") or "")
        if t not in ranking.BASE:
            raise SessionError(422, f"unknown event type {t!r}; one of {', '.join(sorted(ranking.BASE))}")
        team = ev.get("team") if ev.get("team") in ("home", "away") else None
        st = self.state()
        if t == "goal" and team:
            self.overlay[team] += 1
        seq = len(self.match["events"]) + len(self.ingested)
        out = {
            "id": f"x{len(self.ingested):03d}", "seq": seq, "type": t, "vt": int(self.vt), "clock": st["clock"], "period": st["period"] or 1, "minute": st["minute"], "team": team,
            "player": {"id": f"x-{ev['player']}", "name": str(ev["player"]), "num": 0, "pos": "?", "team": team} if ev.get("player") else None, "player2": None,
            "text": str(ev.get("text") or t)[:240], "score": self.state()["score"], "tags": [x for x in ev.get("tags", []) if isinstance(x, str)][:4],
            "urgent": bool(ev.get("urgent")), "source": "external",
        }
        self.ingested.add(key or out["id"])
        self._emit_feed(out)
        return {"accepted": True, "duplicate": False, "id": out["id"], "event": out}
