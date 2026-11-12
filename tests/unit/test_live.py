"""The live session, headless: the clock, the dispatcher, polls, approvals, ingest. Whole matches run in about a second."""

from __future__ import annotations

import asyncio
import itertools
import json

import pytest

from gantry.engine import policy
from gantry.live.demo import play
from gantry.live.hub import Hub
from gantry.live.runner import LocalRunner, thread_id
from gantry.live.runtime import LiveRuntime
from gantry.live.session import REACTION_DELAY, Session, SessionError
from gantry.memory.ledger import ledger_list


def session(mode="autopilot", fixture="derby", **kw) -> Session:
    s = Session(LocalRunner(), fixture_id=fixture, mode=mode, match_id=f"t-{fixture}-{mode}", **kw)
    s.paused = False
    return s


async def test_a_whole_match_on_autopilot_obeys_every_rule():
    out = await play("derby", "autopilot", 7, approve=False, quiet=True)
    s, polls = out["session"], out["polls"]
    assert s.ended and s.stats["errors"] == 0 and s.stats["windows"] >= 30 and s.state()["score"] == {"home": 2, "away": 1}
    assert 8 <= len(polls) <= 16, "busy but not spammy"
    for a, b in itertools.pairwise(polls):
        assert b["opened_vt"] >= a["closed_vt"], "never two polls at once"
        if not a.get("closed_early"):
            assert b["opened_vt"] - a["closed_vt"] >= policy.POLL_GAP_BIG - 2, "a breathing gap between polls"
    topics = [p["topic"] for p in polls]
    assert len(topics) == len(set(topics)), "never the same thing twice"
    by_template = {p["template"] for p in polls}
    assert {"penalty_outcome", "red_card", "goal_rating", "potm_half", "motm"} <= by_template
    pen = next(p for p in polls if p["template"] == "penalty_outcome")
    assert pen["opened_vt"] - pen["event_vt"] <= 20, "the penalty poll is out before the kick is taken"
    assert max(polls, key=lambda p: p["participation"])["template"] in ("penalty_outcome", "red_card", "decision")
    assert len(s.highlights) >= 5 and all(a["by"] == "autopilot" for a in s.actions if a["status"] == "published")
    held = [a for a in s.actions if a["status"] == "suppressed"]
    assert held and all(a["reason"] for a in held), "everything held back says why"
    ledger = out["ledger"]
    assert sum(1 for e in ledger if e["status"] == "published" and e["kind"] == "poll") == len(polls)
    assert all("participation" in e for e in ledger if e["kind"] == "poll" and e["status"] == "published"), "every poll's outcome is written back"
    assert out["prefs"]["penalty_outcome"]["n"] == 1 and len(s.recaps) == 2


async def test_manual_mode_with_nobody_at_the_desk_publishes_nothing_and_blocks_nothing():
    out = await play("derby", "manual", 7, approve=False, quiet=True)
    s = out["session"]
    assert s.stats["published"] == 0 and s.stats["expired"] >= 15 and s.polls == [] and not s.suggestions
    assert s.stats["windows"] >= 30 and s.stats["errors"] == 0, "pending suggestions never held up the broadcast"
    assert {e["status"] for e in out["ledger"]} <= {"expired", "suppressed"}


async def test_a_human_approves_edits_and_rejects_in_any_order():
    s = session("manual")
    await s.step(680 + REACTION_DELAY + 12)  # the 12' goal, plus the reaction delay
    kinds = {x["action"]["kind"]: x for x in s.suggestions.values()}
    assert {"poll", "highlight"} <= set(kinds) and s.poll is None
    poll = kinds["poll"]
    with pytest.raises(SessionError) as bad:
        await s.decide(poll["id"], "maybe")
    assert bad.value.status == 400
    await s.decide(kinds["highlight"]["id"], "reject")
    await s.decide(poll["id"], "edit", {"question": "Tindall's opener: your verdict?", "options": ["Class", "Lucky"]})
    await s.drain()
    assert s.poll and s.poll["question"] == "Tindall's opener: your verdict?" and s.poll["options"] == ["Class", "Lucky"] and s.poll["by"] == "human"
    assert s.highlights == [] and poll["id"] not in s.suggestions
    with pytest.raises(SessionError) as gone:
        await s.decide(poll["id"], "approve")
    assert gone.value.status == 404
    s.vote(s.poll["id"], 1)
    s.vote(s.poll["id"], 1)
    assert s.poll["real_votes"] == [0, 2]
    with pytest.raises(SessionError):
        s.vote("nope", 0)
    await s.step(90)
    assert s.poll is None and s.polls[0]["status"] == "closed" and 0 < s.polls[0]["participation"] < 0.6
    assert any(m.get("host") == "poll" and "Poll closed" in m["text"] for m in s.chat_out), "the result is announced in the chat"
    ledger = {e["id"]: e for e in await ledger_list(s.runner.store, s.match_id)}
    assert ledger[poll["id"]]["status"] == "published" and ledger[poll["id"]]["edited"] and ledger[poll["id"]]["participation"] == s.polls[0]["participation"]


async def test_a_second_poll_cannot_be_approved_while_one_is_live_and_a_stale_one_is_dropped():
    s = session("manual")
    await s.step(680 + REACTION_DELAY + 12)
    first = next(x for x in s.suggestions.values() if x["action"]["kind"] == "poll")
    await s.decide(first["id"], "approve")
    await s.drain()
    assert s.poll
    s.suggestions["fake"] = {"id": "fake", "action": {"id": "fake", "kind": "poll", "topic": "t"}, "thread": "x", "graph": "showrunner", "interrupt_id": "i", "deciding": False}
    with pytest.raises(SessionError) as busy:
        await s.decide("fake", "approve")
    assert busy.value.status == 409 and "A poll is live" in busy.value.message
    s.suggestions.pop("fake")
    late = {"id": "late", "kind": "poll", "topic": "late", "question": "Too late?", "options": ["a", "b"], "expires_vt": int(s.vt) - 100}
    s.poll = None
    s._apply(late, "human", False)
    assert s.poll is None and s.actions[-1]["status"] == "dropped" and "passed" in s.actions[-1]["reason"]


async def test_the_dispatcher_is_event_driven():
    s = session("autopilot")
    await s.step(6 + 150)
    assert s.window_seq == 0, "nothing has happened yet, so nothing has run"
    await s.step(100)  # a quiet three minutes fires a tick; then the 4' big chance (vt 220) arms a window one reaction-delay later
    opens = [e[2]["data"] for e in s.hub.ring if e[1] == "agent" and e[2]["kind"] == "window.open"]
    assert [o["trigger"]["kind"] for o in opens] == ["tick", "event"] and opens[1]["trigger"]["reason"] == "chance.big"
    assert 0 <= opens[1]["to_vt"] - (220 + REACTION_DELAY) <= 2 and s.rundowns[-1]["moments"][0]["type"] == "chance.big"
    await s.step(4505 - s.vt + 8)  # the 71' penalty is urgent: the window fires within seconds, not after the usual delay
    pen = next(e for e in s.hub.ring if e[1] == "agent" and e[2]["kind"] == "window.open" and e[2]["data"]["trigger"]["kind"] == "urgent")
    assert pen[2]["data"]["to_vt"] - 4505 <= 10
    await s.step(12)
    assert s.poll and s.poll["template"] == "penalty_outcome"


async def test_chat_is_screened_on_arrival_and_ranked_in_the_window():
    s = session("autopilot")
    await s.step(120)
    spam = s.post_chat("promo", "FREE betting tips 100% WIN rate check my channel")
    assert spam["curation"]["label"] == "hide" and spam["curation"]["by"] == "screen", "safety is inline, not batched"
    s.post_chat("me", "lets go lads")
    again = s.post_chat("me", "LETS GO LADS!!!")
    assert again["curation"]["label"] == "collapse"
    with pytest.raises(SessionError):
        s.post_chat("me", "   ")
    good = s.post_chat("me", "Okafor keeps drifting wide because the full-back is pinned, that is why the cross is always on")
    await s.step(680 + REACTION_DELAY + 12 - s.vt)
    assert good["id"] in s.chat_by_id and s.labels.get(good["id"], {}).get("label") != "hide"
    labelled = {v["label"] for v in s.labels.values()}
    assert {"highlight", "hide", "collapse"} <= labelled
    assert all(not m.get("host") for r in s.rundowns for m in r["highlights"]), "the channel's own messages are never curated"


async def test_an_external_feed_event_is_idempotent_and_drives_the_loop():
    s = session("autopilot")
    await s.step(300)
    before = s.window_seq
    ev = {"type": "card.red", "team": "away", "player": "Stone", "text": "RED CARD! Stone is sent off."}
    first = s.ingest(ev, key="provider-123")
    assert first["accepted"] and first["event"]["source"] == "external" and first["event"]["minute"] == s.state()["minute"]
    assert s.ingest(ev, key="provider-123") == {"accepted": False, "duplicate": True, "id": "provider-123"}
    with pytest.raises(SessionError) as bad:
        s.ingest({"type": "streaker"})
    assert bad.value.status == 422
    await s.step(REACTION_DELAY + 45)
    assert s.window_seq > before and any(m["type"] == "card.red" and m["tier"] == "headline" for r in s.rundowns for m in r["moments"])
    assert any(a["kind"] == "poll" and "Stone" in (a["text"] or "").title() for a in s.actions), "a pushed event gets its poll like any other"
    s.ingest({"type": "goal", "team": "home", "player": "Okafor", "text": "GOAL!"}, key="g1")
    assert s.state()["score"]["home"] == 1


async def test_controls_and_snapshot():
    s = session("assisted", speed=6)
    s.paused = True
    s.tick(1.0)
    assert s.vt < 0, "paused means paused"
    s.control("play")
    s.tick(1.0)
    assert s.vt == pytest.approx(0.0)
    assert s.control("speed", 40)["speed"] == 40 and s.control("mode", "manual")["mode"] == "manual"
    with pytest.raises(SessionError):
        s.control("mode", "yolo")
    s.control("jump")
    await s.drain()
    assert 200 <= s.vt <= 220, "jump lands just before the next big moment"
    snap = s.snapshot()
    json.dumps(snap)
    assert snap["match_id"] == s.match_id and snap["scoreboard"]["minute"] == "4'" and "truth" not in json.dumps(snap)
    assert thread_id("m", "window", "w001") == thread_id("m", "window", "w001") != thread_id("m", "window", "w002")


async def test_jumping_the_clock_is_not_reported_as_lag():
    """"Next moment" releases every event it skips at once. Their windows are due NOW: `lag` measures the graphs falling behind, not the skip."""
    s = session("autopilot")
    s.vt = 2300  # the 38' goal has been and gone; half-time (vt 2820) and the second-half kick-off are still ahead
    s._process()
    await s.drain()
    s.control("jump")  # lands just before the 52' VAR check, skipping half-time
    await s.drain()
    assert s.vt > 3290 and s.stats["windows"] >= 2
    assert s.stats["max_lag"] < 30, s.stats


async def test_the_hub_replays_exactly_what_a_reconnecting_client_missed():
    hub = Hub(ring=5)
    for i in range(3):
        hub.publish("chat", {"n": i})
    hub.publish("state", {"tick": 1}, transient=True)

    async def take(gen, n):
        out = []
        async for frame in gen:
            out.append(frame)
            if len(out) == n:
                break
        return out

    frames = await take(hub.stream(hello={"x": 1}, since=1), 3)
    assert frames[0].startswith("event: hello") and '"seq":3' in frames[0]
    assert [f.split("\n")[0] for f in frames[1:]] == ["id: 2", "id: 3"], "only what came after event 1, in order, no transient ticks"
    live = hub.stream(hello={}, since=None)
    task = asyncio.ensure_future(take(live, 2))
    await asyncio.sleep(0.01)
    hub.publish("poll", {"q": 1})
    got = await asyncio.wait_for(task, 1)
    assert got[1].startswith("id: 4\nevent: poll")
    for i in range(10):
        hub.publish("chat", {"n": i})
    gap = await take(hub.stream(hello={}, since=2), 2)
    assert gap[1].startswith("event: gap") and '"missed_from":3' in gap[1], "a client too far behind is told, not silently short-changed"


async def test_the_runtime_registry():
    rt = LiveRuntime(LocalRunner)
    a = await rt.create(fixture="derby", autostart=False, match_id="one")
    assert rt.get("one") is a and [m["match_id"] for m in rt.list()] == ["one"]
    b = await rt.create(fixture="goalfest", autostart=False, match_id="one")
    assert rt.get("one") is b and a.ended, "restarting a match replaces it"
    with pytest.raises(SessionError) as missing:
        rt.get("nope")
    assert missing.value.status == 404
    with pytest.raises(SessionError):
        await rt.create(fixture="cricket")
    await rt.close()
    assert rt.sessions == {}
