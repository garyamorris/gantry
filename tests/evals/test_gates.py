"""The offline eval gate. Deterministic, no network, about two seconds.

Three questions, each with a floor that fails the build:
  1. Does flow 1 rank and curate well?       (against the simulator's hidden ground truth)
  2. Is it safe to leave running?            (invariants over whole matches on autopilot)
  3. Does memory make it better?             (the same match, cold and then warm)
"""

from __future__ import annotations

import itertools

import pytest

from gantry.engine import policy, ranking
from gantry.live.demo import play
from gantry.live.runner import LocalRunner
from gantry.live.windows import sample_window, timeline_of
from gantry.sim.chat import chat_for
from gantry.sim.chat import public as chat_public
from gantry.sim.fixtures import FIXTURE_IDS
from gantry.sim.match import public, simulate
from helpers import ndcg

FLOORS = {"ndcg@5": 0.93, "highlight_precision": 0.85, "hide_recall": 0.97, "false_hide_rate": 0.01}


@pytest.mark.parametrize("fixture", FIXTURE_IDS)
def test_moment_ranking_matches_a_human_producer(fixture):
    m = simulate(fixture, 7)
    chat = chat_for(m)
    events = [public(e) for e in m["events"]]
    comments = [chat_public(c, now_vt=c["vt"] + 80) for c in chat]
    ranked = ranking.rank_moments(events, timeline_of(events, 1e9), comments, len(chat) / (m["timing"]["end_vt"] / 60))
    truth = {e["id"]: e["truth"]["importance"] for e in m["events"]}
    score = ndcg([r["event_id"] for r in ranked], truth, 5)
    print(f"\n  {fixture}: NDCG@5 {score:.3f}")
    assert score >= FLOORS["ndcg@5"]
    assert truth[ranked[0]["event_id"]] == 1.0, "the moment of the match is ranked first"


@pytest.mark.parametrize("fixture", FIXTURE_IDS)
def test_chat_curation_against_hidden_labels(fixture):
    m = simulate(fixture, 7)
    truth = {c["id"]: c["truth"] for c in chat_for(m)}
    good = bad = hide_hit = hide_miss = false_hide = n = 0
    for to_vt in range(240, m["timing"]["end_vt"] + 60, 240):
        w = sample_window(fixture, 7, to_vt=to_vt)
        win, ms = w["window"], w["match_state"]
        moments = ranking.rank_moments(win["events"], win["timeline"], win["comments"], win["chat"]["baseline_per_min"])
        lex = ranking.build_lexicon(moments, ms)
        seen: dict[str, int] = {}
        scored = ranking.dedupe_pass([ranking.score_comment(c, lex, seen) for c in win["comments"]])
        for c in ranking.shortlist(scored, k=3, floor=0.55):
            good, bad = good + (truth[c["id"]]["quality"] >= 0.55), bad + (truth[c["id"]]["quality"] < 0.55)
        for c in scored:
            n += 1
            t = truth[c["id"]]
            hide_hit += t["hide"] and c["label"] == "hide"
            hide_miss += t["hide"] and c["label"] != "hide"
            false_hide += (not t["hide"]) and c["label"] == "hide"
    precision, recall, fh = good / (good + bad), hide_hit / (hide_hit + hide_miss), false_hide / n
    print(f"\n  {fixture}: highlight precision {precision:.3f} ({good}/{good + bad}), hide recall {recall:.3f}, false hides {false_hide}/{n}")
    assert precision >= FLOORS["highlight_precision"] and recall >= FLOORS["hide_recall"] and fh <= FLOORS["false_hide_rate"]


@pytest.mark.parametrize("fixture", FIXTURE_IDS)
async def test_autopilot_invariants_over_a_whole_match(fixture):
    out = await play(fixture, "autopilot", 7, approve=False, quiet=True)
    s, polls = out["session"], out["polls"]
    assert s.stats["errors"] == 0 and s.ended
    assert 5 <= len(polls) <= 16
    for a, b in itertools.pairwise(polls):
        assert b["opened_vt"] >= a["closed_vt"]
    assert len({p["topic"] for p in polls}) == len(polls)
    used: dict[str, int] = {}
    for p in polls:
        used[p["template"]] = used.get(p["template"], 0) + 1
    assert all(n <= policy.MAX_PER_TEMPLATE.get(t, policy.DEFAULT_MAX_PER_TEMPLATE) for t, n in used.items())
    assert all(len(p["question"]) <= 90 and 2 <= len(p["options"]) <= 4 for p in polls)
    hidden_published = [a for a in s.actions if a["kind"] == "pin_comment" and a["status"] == "published" and s.labels.get(a["id"], {}).get("label") == "hide"]
    assert hidden_published == [], "nothing the curator hid is ever pinned"
    mean = sum(p["participation"] for p in polls) / len(polls)
    print(f"\n  {fixture}: {len(polls)} polls, mean participation {mean:.1%}, {s.stats['suppressed']} held back, {s.stats['windows']} windows")


async def test_memory_makes_the_second_match_better():
    """Same match twice on one Store. The second time it knows what this audience votes on: fewer duds, more votes per poll."""
    runner = LocalRunner()
    cold = (await play("stalemate", "autopilot", 7, approve=False, quiet=True, runner=runner))["polls"]
    for fixture in ("derby", "goalfest"):
        await play(fixture, "autopilot", 7, approve=False, quiet=True, runner=runner)
    warm_out = await play("stalemate", "autopilot", 7, approve=False, quiet=True, runner=runner)
    warm = warm_out["polls"]
    mean = lambda ps: sum(p["participation"] for p in ps) / len(ps)  # noqa: E731
    duds = lambda ps: sum(1 for p in ps if p["participation"] < 0.12)  # noqa: E731
    print(f"\n  cold: {len(cold)} polls, mean {mean(cold):.1%}, {duds(cold)} duds | warm: {len(warm)} polls, mean {mean(warm):.1%}, {duds(warm)} duds")
    print("  lessons:", [x["id"] for x in warm_out["lessons"]])
    assert mean(warm) > mean(cold) and duds(warm) <= duds(cold)
    assert "vibe_check" not in {p["template"] for p in warm}, "it stopped asking the question this audience ignores"
    assert warm_out["lessons"], "and wrote down why"
    assert any("memory:" in (e.get("reason") or "") for e in warm_out["ledger"]), "the ledger says memory made the call"
