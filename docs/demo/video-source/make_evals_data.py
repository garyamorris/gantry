#!/usr/bin/env python3
"""make_evals_data.py: run the offline eval gate's measurements and write viz/evals_data.json for evals.html.

    uv run python docs/demo/video-source/make_evals_data.py

The same measurements as tests/evals/test_gates.py (and the same FLOORS, imported from it), taken here as numbers instead of assertions.
Offline, deterministic, about three seconds.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

os.environ["GANTRY_PROVIDER"] = "offline"
os.environ["GANTRY_OFFLINE_TOKEN_DELAY_S"] = "0"

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from evals.test_gates import FLOORS  # noqa: E402
from helpers import ndcg  # noqa: E402

from gantry.engine import ranking  # noqa: E402
from gantry.live.demo import play  # noqa: E402
from gantry.live.runner import LocalRunner  # noqa: E402
from gantry.live.windows import sample_window, timeline_of  # noqa: E402
from gantry.sim.chat import chat_for  # noqa: E402
from gantry.sim.chat import public as chat_public  # noqa: E402
from gantry.sim.fixtures import FIXTURE_IDS  # noqa: E402
from gantry.sim.match import public, simulate  # noqa: E402


def ranking_scores(fixture: str) -> float:
    m = simulate(fixture, 7)
    chat = chat_for(m)
    events = [public(e) for e in m["events"]]
    comments = [chat_public(c, now_vt=c["vt"] + 80) for c in chat]
    ranked = ranking.rank_moments(events, timeline_of(events, 1e9), comments, len(chat) / (m["timing"]["end_vt"] / 60))
    truth = {e["id"]: e["truth"]["importance"] for e in m["events"]}
    return ndcg([r["event_id"] for r in ranked], truth, 5)


def curation(fixture: str) -> dict:
    m = simulate(fixture, 7)
    truth = {c["id"]: c["truth"] for c in chat_for(m)}
    good = bad = hit = miss = false = n = 0
    for to_vt in range(240, m["timing"]["end_vt"] + 60, 240):
        w = sample_window(fixture, 7, to_vt=to_vt)
        win, ms = w["window"], w["match_state"]
        moments = ranking.rank_moments(win["events"], win["timeline"], win["comments"], win["chat"]["baseline_per_min"])
        lex = ranking.build_lexicon(moments, ms)
        scored = ranking.dedupe_pass([ranking.score_comment(c, lex, {}) for c in win["comments"]])
        for c in ranking.shortlist(scored, k=3, floor=0.55):
            good, bad = good + (truth[c["id"]]["quality"] >= 0.55), bad + (truth[c["id"]]["quality"] < 0.55)
        for c in scored:
            n += 1
            t = truth[c["id"]]
            hit += t["hide"] and c["label"] == "hide"
            miss += t["hide"] and c["label"] != "hide"
            false += (not t["hide"]) and c["label"] == "hide"
    return {"precision": good / (good + bad), "highlights": good + bad, "hide_recall": hit / (hit + miss), "hidden": hit + miss, "false_hides": false, "comments": n}


async def autopilot(fixture: str, runner=None) -> dict:
    out = await play(fixture, "autopilot", 7, approve=False, quiet=True, runner=runner)
    s, polls = out["session"], out["polls"]
    overlaps = sum(1 for a, b in zip(polls, polls[1:], strict=False) if b["opened_vt"] < a["closed_vt"])
    repeats = len(polls) - len({p["topic"] for p in polls})
    return {"polls": len(polls), "mean": sum(p["participation"] for p in polls) / len(polls), "duds": sum(1 for p in polls if p["participation"] < 0.12), "held_back": s.stats["suppressed"],
            "windows": s.stats["windows"], "errors": s.stats["errors"], "overlaps": overlaps, "repeats": repeats, "templates": sorted({p["template"] for p in polls}), "out": out}


async def main() -> None:
    data: dict = {"floors": FLOORS, "ndcg": {f: ranking_scores(f) for f in FIXTURE_IDS}, "curation": {f: curation(f) for f in FIXTURE_IDS}}
    auto = {}
    for f in FIXTURE_IDS:
        r = await autopilot(f)
        r.pop("out")
        auto[f] = r
    data["autopilot"] = auto
    runner = LocalRunner()
    cold = await autopilot("stalemate", runner)
    cold.pop("out")
    for f in ("derby", "goalfest"):
        (await autopilot(f, runner)).pop("out")
    warm_full = await autopilot("stalemate", runner)
    out = warm_full.pop("out")
    data["memory"] = {"cold": cold, "warm": warm_full, "lessons": [{"id": x["id"], "text": x["text"]} for x in out["lessons"]],
                      "memory_reasons": sum(1 for e in out["ledger"] if "memory:" in (e.get("reason") or ""))}
    path = os.path.join(HERE, "viz", "evals_data.json")
    json.dump(data, open(path, "w"), indent=1)
    print(json.dumps({k: data[k] for k in ("ndcg", "memory")}, indent=1)[:1800])
    print("->", path)


if __name__ == "__main__":
    asyncio.run(main())
