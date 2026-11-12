from __future__ import annotations

import json

import pytest

from gantry.live.windows import sample_window
from gantry.sim import audience
from gantry.sim.chat import chat_for
from gantry.sim.chat import public as chat_public
from gantry.sim.fixtures import FIXTURE_IDS, FIXTURES
from gantry.sim.match import minute_label, public, simulate, state_at


@pytest.mark.parametrize("fixture", FIXTURE_IDS)
def test_a_match_is_a_pure_function_of_fixture_and_seed(fixture):
    a, b = simulate(fixture, 7), simulate(fixture, 7)
    assert [public(e) for e in a["events"]] == [public(e) for e in b["events"]]
    other = simulate(fixture, 8)
    assert [e["id"] for e in other["events"] if e["truth"]["importance"] >= 0.4] != [] and json.dumps([public(e) for e in other["events"]]) != json.dumps([public(e) for e in a["events"]])
    vts = [e["vt"] for e in a["events"]]
    assert vts == sorted(vts) and a["events"][0]["type"] == "match.kickoff" and a["events"][-1]["type"] == "match.fulltime"


@pytest.mark.parametrize(("fixture", "score"), [("derby", (2, 1)), ("stalemate", (0, 1)), ("goalfest", (4, 3))])
def test_each_fixture_tells_its_story(fixture, score):
    m = simulate(fixture, 7)
    final = state_at(m, m["timing"]["end_vt"] + 1)
    assert (final["score"]["home"], final["score"]["away"]) == score and final["phase"] == "fulltime"
    assert len(final["scorers"]) == sum(score)
    scripted = list(FIXTURES[fixture]["beats"])
    assert sum(1 for e in m["events"] if e["truth"]["importance"] >= 0.4) >= sum(1 for b in scripted if b["imp"] >= 0.4)


def test_ground_truth_never_leaves_the_simulator():
    m = simulate("derby", 7)
    assert all("truth" not in public(e) and not any(k.startswith("_") for k in public(e)) for e in m["events"])
    chat = chat_for(m)
    assert all("truth" not in chat_public(c) and "_side" not in chat_public(c)["author"] for c in chat)
    w = sample_window("derby", 7)
    blob = json.dumps(w)
    assert '"truth"' not in blob and '"_side"' not in blob and '"importance"' not in blob


def test_the_scoreboard_follows_the_feed():
    m = simulate("derby", 7)
    assert state_at(m, 100)["score"] == {"home": 0, "away": 0}
    after_first = state_at(m, 700)
    assert after_first["score"] == {"home": 0, "away": 1} and after_first["scorers"][0]["player"]["name"] == "Tindall"
    red = state_at(m, 4100)
    assert len(red["on_pitch"]["away"]) == 10 and red["sent_off"][0]["player"]["name"] == "Dragovic"
    assert state_at(m, m["timing"]["p1_end_vt"] + 10)["phase"] == "halftime"
    assert minute_label(1, 0) == "1'" and minute_label(1, 46 * 60) == "45+2'" and minute_label(2, 92 * 60 + 40) == "90+3'"


def test_the_chat_bursts_after_a_goal_and_is_a_mixed_crowd():
    m = simulate("derby", 7)
    chat = chat_for(m)
    winner = next(e for e in m["events"] if e["type"] == "goal" and e["truth"]["importance"] == 1.0)
    burst = [c for c in chat if winner["vt"] < c["vt"] <= winner["vt"] + 60]
    quiet = [c for c in chat if winner["vt"] - 260 < c["vt"] <= winner["vt"] - 200]
    assert len(burst) >= 4 * max(len(quiet), 1)
    kinds = {c["truth"]["kind"] for c in chat}
    assert {"insight", "question", "reaction", "spam", "smart_spam", "toxic", "harsh", "flood"} <= kinds
    assert all(c["truth"]["hide"] == (c["truth"]["kind"] in {"spam", "smart_spam", "toxic"}) for c in chat)
    fresh = chat_public(burst[0], now_vt=burst[0]["vt"])
    assert fresh["likes"] == 0, "a message that was just posted has no likes yet"


def test_the_audience_rewards_timely_spaced_clear_polls():
    poll = {"template": "decision", "topic": "decision:e1", "question": "Right call?", "options": ["Yes", "No"]}
    base = audience.participation(poll, seed=1, opened_vt=1000, event_vt=980, last_closed_vt=None, times_used=0)
    late = audience.participation(poll, seed=1, opened_vt=1300, event_vt=980, last_closed_vt=None, times_used=0)
    crowded = audience.participation(poll, seed=1, opened_vt=1000, event_vt=980, last_closed_vt=950, times_used=0)
    repeated = audience.participation(poll, seed=1, opened_vt=1000, event_vt=980, last_closed_vt=None, times_used=2)
    wordy = audience.participation({**poll, "question": "x" * 120}, seed=1, opened_vt=1000, event_vt=980, last_closed_vt=None, times_used=0)
    dull = audience.participation({**poll, "template": "vibe_check"}, seed=1, opened_vt=1000, event_vt=980, last_closed_vt=None, times_used=0)
    assert base > max(late, crowded, repeated, wordy, dull)
    shares = audience.split({**poll, "options": ["a", "b", "c"]}, seed=1, state={"score": {"home": 0, "away": 0}})
    assert abs(sum(shares) - 1) < 1e-9
    assert audience.votes_at(1000, [0.5, 0.5], 0, 60) == [0, 0] and sum(audience.votes_at(1000, [0.5, 0.5], 60, 60)) == 1000
    assert sum(audience.votes_at(1000, [0.5, 0.5], 15, 60)) > 500, "votes are front-loaded"
