"""The audience: how the simulated crowd votes in a poll. THIS IS THE HIDDEN WORLD the producer has to learn.

Nothing in `gantry.engine` or the graphs imports this module. The live runtime calls it to turn a published poll
into votes over time; the only way the producer finds out what works is by watching polls close (`memory_keeper`).

Participation = how many viewers vote. It depends on things the producer controls:
  * the kind of question (each template has a hidden APPEAL: a penalty call beats "rate this half")
  * timeliness (a poll that opens two minutes after the moment has lost half its crowd)
  * spacing (polls back to back get ignored) and repetition (the third "who wins from here?" is wallpaper)
  * clarity (a long question on a phone, mid-match, does not get read)
"""

from __future__ import annotations

import math
from typing import Any

from gantry.common.rng import roll

APPEAL: dict[str, float] = {
    "penalty_outcome": 0.95, "decision": 0.90, "red_card": 0.86, "hattrick_watch": 0.80, "motm": 0.76, "winner_from_here": 0.70,
    "chat_split": 0.66, "keeper_save": 0.60, "potm_half": 0.60, "goal_rating": 0.56, "sub_wish": 0.54, "next_goal": 0.50,
    "second_half": 0.44, "miss": 0.40, "vibe_check": 0.34, "sub_verdict": 0.30,
}
CEILING = 0.36  # the best possible poll gets about a third of the room


def participation(poll: dict[str, Any], *, seed: int, opened_vt: int, event_vt: int | None, last_closed_vt: int | None, times_used: int, heat: float = 1.0) -> float:
    """Fraction of viewers who will vote by the time the poll closes."""
    appeal = APPEAL.get(poll.get("template", ""), 0.45)
    delay = max(0, opened_vt - event_vt) if event_vt is not None else 0
    timeliness = 1.0 if delay <= 45 else max(0.5, 1.0 - (delay - 45) / 390)
    gap = None if last_closed_vt is None else opened_vt - last_closed_vt
    spacing = 1.0 if gap is None or gap >= 300 else 0.8 if gap >= 120 else 0.55
    repetition = 0.8 ** max(0, times_used)
    q, opts = poll.get("question", ""), poll.get("options", [])
    clarity = (1.0 if len(q) <= 70 else 0.9 if len(q) <= 100 else 0.72) * (1.0 if len(opts) <= 4 else 0.8) * (0.9 if any(len(o) > 28 for o in opts) else 1.0)
    crowd = max(0.75, min(1.25, 0.85 + 0.1 * heat))
    noise = 0.94 + 0.12 * roll(seed, "poll-noise", poll.get("topic"), opened_vt)
    return round(max(0.01, min(0.6, CEILING * appeal * timeliness * spacing * repetition * clarity * crowd * noise)), 4)


def split(poll: dict[str, Any], *, seed: int, state: dict[str, Any]) -> list[float]:
    """How the votes divide between the options (sums to 1)."""
    n = len(poll.get("options", []))
    t = poll.get("template")
    if t == "penalty_outcome" and n == 3:
        w = [0.56, 0.33, 0.11]
    elif t == "winner_from_here" and n == 3:
        h, a = state["score"]["home"], state["score"]["away"]
        reds = {"home": 0, "away": 0}
        for s in state.get("sent_off", []):
            reds[s["team"]] += 1
        lean = (h - a) * 0.22 + (reds["away"] - reds["home"]) * 0.12 + 0.04
        w = [max(0.06, 0.36 + lean), max(0.08, 0.28 - abs(lean) * 0.5), max(0.06, 0.36 - lean)]
    elif t in ("decision", "red_card", "chat_split") and n == 3:
        w = [0.44, 0.40, 0.16]
    else:
        w = [0.35 + roll(seed, "split", poll.get("topic"), i) ** 1.4 * 1.6 for i in range(n)]
    w = [x * (0.9 + 0.2 * roll(seed, "split-noise", poll.get("topic"), i)) for i, x in enumerate(w)]
    total = sum(w) or 1.0
    return [x / total for x in w]


def progress(elapsed: float, duration: float) -> float:
    """Cumulative share of the eventual votes cast after `elapsed` seconds: front-loaded, complete at close."""
    if duration <= 0 or elapsed >= duration:
        return 1.0
    if elapsed <= 0:
        return 0.0
    return (1 - math.exp(-4 * elapsed / duration)) / (1 - math.exp(-4))


def votes_at(total: int, shares: list[float], elapsed: float, duration: float) -> list[int]:
    cast = total * progress(elapsed, duration)
    return [int(round(cast * s)) for s in shares]
