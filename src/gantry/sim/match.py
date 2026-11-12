"""The live sports data feed: a deterministic match simulator.

`simulate(fixture_id, seed)` returns the whole match as a timeline of feed events (`gantry.feed/1`), in broadcast
order. The live runtime releases them as the virtual clock passes each one, exactly as a provider's push feed would.
Everything is a pure function of `(fixture_id, seed)`: the scripted beats give each fixture its story, the seed
changes the filler in between, and a replayed or rewound match is bit-for-bit the same.

Time:  `vt` is VIRTUAL SECONDS on the broadcast timeline (kick-off = 0, includes the half-time break). `clock` is the
match clock in seconds (second half restarts at 2700). `minute` is what a scoreboard shows ("67'", "90+3'").

Each event carries `truth.importance` (what a human producer would say it is worth). `public()` strips it: graphs,
the API and the browser never see ground truth; only the evals do.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from typing import Any

from gantry.common.rng import pick, roll, weighted
from gantry.sim.fixtures import FIXTURES, TEAMS, public_fixture

HT_BREAK_VT = 180  # the half-time break, compressed to three virtual minutes
FACT_TAGS = frozenset({"penalty", "header", "set_piece", "forced"})  # what a data provider would actually tag

_FILLER = {
    "shot.off_target": (3.0, 0.05, ["{player} drags one wide from distance.", "{player} blazes over from the edge of the area.", "{player} snatches at it; well off target."]),
    "shot.saved": (2.0, 0.08, ["{player} tests the keeper with a low drive; a comfortable save.", "{player}'s header is straight at the goalkeeper.", "{player} cuts inside and shoots; held at the second attempt."]),
    "corner": (3.0, 0.04, ["Corner to {team}; {player} to deliver.", "{team} win a corner on the right.", "{player} swings the corner in; headed clear."]),
    "foul": (3.0, 0.03, ["Free kick: {player} catches his man late.", "{player} gives away a foul in midfield.", "The referee has a quiet word with {player}."]),
    "offside": (1.5, 0.03, ["{player} is flagged offside.", "The flag goes up against {player}."]),
}
_FILLER_POS = {"shot.off_target": ("FW", "MF"), "shot.saved": ("FW", "MF"), "corner": ("MF",), "foul": ("DF", "MF"), "offside": ("FW",)}
_XG = {"goal": 0.35, "chance.big": 0.32, "shot.woodwork": 0.12, "shot.saved": 0.08, "shot.off_target": 0.04, "penalty.saved": 0.76, "goal.disallowed": 0.0}
_SHOT_TYPES = {"goal", "chance.big", "shot.woodwork", "shot.saved", "shot.off_target", "penalty.saved", "penalty.missed"}
_ON_TARGET = {"goal", "shot.saved", "penalty.saved"}


def minute_label(period: int, clock: int) -> str:
    m = clock // 60
    if period == 1:
        return f"{m + 1}'" if m < 45 else f"45+{m - 44}'"
    return f"{m + 1}'" if m < 90 else f"90+{m - 89}'"


def _timing(fixture: dict[str, Any]) -> dict[str, int]:
    add1, add2 = fixture["added"]
    p1_end = (45 + add1) * 60
    p2_start = p1_end + HT_BREAK_VT
    end = p2_start + (45 + add2) * 60
    return {"p1_end_vt": p1_end, "p2_start_vt": p2_start, "end_vt": end, "added1": add1, "added2": add2}


def _vt(tm: dict[str, int], period: int, clock: int) -> int:
    return clock if period == 1 else tm["p2_start_vt"] + (clock - 2700)


def clock_at(tm: dict[str, int], vt: float) -> dict[str, Any]:
    """Where the broadcast is at virtual time `vt`."""
    if vt < 0:
        return {"phase": "pre", "period": 0, "clock": 0, "minute": "0'"}
    if vt < tm["p1_end_vt"]:
        return {"phase": "first_half", "period": 1, "clock": int(vt), "minute": minute_label(1, int(vt))}
    if vt < tm["p2_start_vt"]:
        return {"phase": "halftime", "period": 1, "clock": tm["p1_end_vt"], "minute": "HT"}
    if vt < tm["end_vt"]:
        c = 2700 + int(vt - tm["p2_start_vt"])
        return {"phase": "second_half", "period": 2, "clock": c, "minute": minute_label(2, c)}
    return {"phase": "fulltime", "period": 2, "clock": 2700 + tm["end_vt"] - tm["p2_start_vt"], "minute": "FT"}


class _Side:
    def __init__(self, code: str, side: str) -> None:
        t = TEAMS[code]
        self.code, self.side, self.name, self.strength = code, side, t["name"], t["strength"]
        self.by_num = {p["num"]: {**p, "team": side} for p in t["players"]}
        self.on = [p["num"] for p in t["players"] if p["starting"]]
        self.bench = [p["num"] for p in t["players"] if not p["starting"]]

    def ref(self, num: int | None) -> dict[str, Any] | None:
        if num is None:
            return None
        p = self.by_num[num]
        return {"id": p["id"], "num": p["num"], "name": p["name"], "pos": p["pos"], "team": self.side}

    def pick(self, positions: tuple[str, ...], *labels: Any) -> int:
        pool = [n for n in self.on if self.by_num[n]["pos"] in positions] or self.on
        return pick(pool, *labels)


def _text(type_: str, b: dict[str, Any], me: _Side, other: _Side, score: dict[str, int], home: _Side, away: _Side) -> str:
    def nm(side: _Side, key: str) -> str:
        return side.by_num[b[key]]["name"] if b.get(key) is not None else "?"

    p = me.by_num[b["num"]]["name"] if b.get("num") is not None else ""
    h, a = score["home"], score["away"]
    if type_ == "goal":
        tail = f" {nm(me, 'assist')} with the assist." if b.get("assist") else ""
        return f"GOAL! {home.name} {h}-{a} {away.name}. {p}: {b['how']}.{tail}"
    if type_ == "chance.big":
        return f"Big chance! {p}: {b['how']}."
    if type_ == "save.big":
        return f"What a save! {p} denies {nm(other, 'shooter')}: {b['how']}."
    if type_ == "shot.woodwork":
        return f"Off the woodwork! {p} with {b['how']}."
    if type_ == "card.yellow":
        return f"Yellow card: {p} ({me.name}) for {b['why']} on {nm(other, 'on')}."
    if type_ == "card.red":
        return f"RED CARD! {p} ({me.name}) is sent off for {b['why']} on {nm(other, 'on')}. {me.name} are down to ten."
    if type_ == "var.check":
        return f"VAR check: {b['how']}."
    if type_ == "goal.disallowed":
        return f"NO GOAL. {p}'s strike is ruled out: {b['why']}."
    if type_ == "var.no_penalty":
        return f"VAR says no penalty for {p}: {b['why']}."
    if type_ == "penalty.awarded":
        return f"PENALTY to {me.name}! {nm(other, 'by')} is penalised for {b['why']}. {p} will take it."
    if type_ == "penalty.saved":
        return f"SAVED! {p} {b['how']}. {nm(other, 'taker')} can't believe it."
    if type_ == "substitution":
        return f"Substitution, {me.name}: {p} on for {nm(me, 'off')}."
    if type_ == "injury":
        return f"{p} ({me.name}) {b['why']}."
    return b.get("how", type_)


def _stats_blank() -> dict[str, Any]:
    z = {"home": 0, "away": 0}
    return {"shots": dict(z), "on_target": dict(z), "corners": dict(z), "fouls": dict(z), "yellows": dict(z), "reds": dict(z), "xg": {"home": 0.0, "away": 0.0}, "possession": {"home": 50, "away": 50}}


def _raw_schedule(fixture_id: str, fixture: dict[str, Any], seed: int, tm: dict[str, int]) -> list[dict[str, Any]]:
    """Scripted beats + procedural filler + whistles and stat lines, in broadcast order."""
    items: list[dict[str, Any]] = [dict(b, clock=b["m"] * 60 + b["s"]) for b in fixture["beats"]]
    busy = {(b["p"], b["m"]) for b in fixture["beats"]}
    home_w = TEAMS[fixture["home"]]["strength"] + 0.04
    away_w = TEAMS[fixture["away"]]["strength"]
    kinds = list(_FILLER)
    weights = [_FILLER[k][0] for k in kinds]
    for period, lo, hi in ((1, 0, 45 + tm["added1"]), (2, 45, 90 + tm["added2"])):
        for m in range(lo, hi):
            if (period, m) in busy or (period, m) in {(1, 0), (2, 45)}:
                continue
            if roll(seed, fixture_id, "fill", period, m) < fixture["tempo"]:
                kind = weighted(kinds, weights, seed, fixture_id, "kind", period, m)
                side = "home" if roll(seed, fixture_id, "side", period, m) < home_w / (home_w + away_w) else "away"
                sec = 5 + int(roll(seed, fixture_id, "sec", period, m) * 50)
                items.append({"p": period, "m": m, "s": sec, "clock": m * 60 + sec, "type": kind, "side": side, "num": None, "imp": _FILLER[kind][1], "filler": True})
            if m % 10 == 9 and m not in (lo, hi - 1):
                items.append({"p": period, "m": m, "s": 58, "clock": m * 60 + 58, "type": "stat.update", "side": None, "num": None, "imp": 0.02})
    items.append({"p": 1, "m": 0, "s": 0, "clock": 0, "type": "match.kickoff", "side": None, "num": None, "imp": 0.15})
    items.append({"p": 1, "m": 45 + tm["added1"], "s": 0, "clock": tm["p1_end_vt"], "type": "match.halftime", "side": None, "num": None, "imp": 0.30})
    items.append({"p": 2, "m": 45, "s": 0, "clock": 2700, "type": "match.second_half", "side": None, "num": None, "imp": 0.12})
    end_clock = (90 + tm["added2"]) * 60
    items.append({"p": 2, "m": 90 + tm["added2"], "s": 0, "clock": end_clock, "type": "match.fulltime", "side": None, "num": None, "imp": 0.50})
    order = {"match.halftime": 2, "match.fulltime": 2, "match.kickoff": -1, "match.second_half": -1}
    items.sort(key=lambda b: (_vt(tm, b["p"], b["clock"]), order.get(b["type"], 0)))
    return items


def _apply(stats: dict[str, Any], ratings: dict[str, float], ev: dict[str, Any], b: dict[str, Any], me: _Side | None, other: _Side | None) -> None:
    """Fold one event into the running stats and player ratings (the simulator's own bookkeeping)."""
    t, side = ev["type"], ev["team"]
    if side is None or me is None or other is None:
        return

    def bump(num: int | None, team: _Side, delta: float) -> None:
        if num is not None:
            pid = team.by_num[num]["id"]
            ratings[pid] = round(ratings.get(pid, 6.0) + delta, 2)

    shooter_side = side if t not in {"penalty.saved", "save.big"} else other.side
    if t in _SHOT_TYPES or t == "save.big":
        stats["shots"][shooter_side] += 1
        if t in _ON_TARGET or t == "save.big":
            stats["on_target"][shooter_side] += 1
        xg = 0.76 if "penalty" in ev["tags"] else _XG.get(t, 0.3 if t == "save.big" else 0.05)
        stats["xg"][shooter_side] = round(stats["xg"][shooter_side] + xg, 2)
    if t == "corner":
        stats["corners"][side] += 1
    if t in {"foul", "card.yellow", "card.red"}:
        stats["fouls"][side] += 1
    if t == "card.yellow":
        stats["yellows"][side] += 1
        bump(b["num"], me, -0.4)
    if t == "card.red":
        stats["reds"][side] += 1
        bump(b["num"], me, -1.5)
    if t == "goal":
        bump(b["num"], me, 1.2)
        bump(b.get("assist"), me, 0.7)
    if t == "chance.big":
        bump(b["num"], me, -0.2)
        bump(b.get("keeper"), other, 0.3)
    if t == "save.big":
        bump(b["num"], me, 0.6)
    if t == "shot.woodwork":
        bump(b["num"], me, 0.3)
    if t == "shot.saved":
        bump(b["num"], me, 0.1)
    if t == "penalty.awarded":
        bump(b.get("by"), other, -0.6)
    if t == "penalty.saved":
        bump(b["num"], me, 1.3)
        bump(b.get("taker"), other, -0.7)


@lru_cache(maxsize=32)
def _simulate(fixture_id: str, seed: int) -> dict[str, Any]:
    fixture = FIXTURES[fixture_id]
    tm = _timing(fixture)
    home, away = _Side(fixture["home"], "home"), _Side(fixture["away"], "away")
    sides = {"home": home, "away": away}
    score = {"home": 0, "away": 0}
    stats = _stats_blank()
    ratings: dict[str, float] = {}
    events: list[dict[str, Any]] = []
    for seq, b in enumerate(_raw_schedule(fixture_id, fixture, seed, tm)):
        t = b["type"]
        me = sides.get(b["side"]) if b["side"] else None
        other = sides["away" if b["side"] == "home" else "home"] if b["side"] else None
        if b.get("filler") and me is not None:
            b = {**b, "num": me.pick(_FILLER_POS[t], seed, fixture_id, "who", b["p"], b["m"])}
        if t == "goal" and me is not None:
            score[me.side] += 1
        if t == "substitution" and me is not None:
            me.on = [n for n in me.on if n != b["off"]] + [b["num"]]
            me.bench = [n for n in me.bench if n != b["num"]]
        if t == "card.red" and me is not None:
            me.on = [n for n in me.on if n != b["num"]]
        vt = _vt(tm, b["p"], b["clock"])
        if b.get("filler") and me is not None:
            text = pick(_FILLER[t][2], seed, fixture_id, "txt", b["p"], b["m"]).format(player=me.by_num[b["num"]]["name"], team=me.name)
        elif t == "match.kickoff":
            text = f"We're under way: {home.name} against {away.name}."
        elif t == "match.halftime":
            text = f"Half-time: {home.name} {score['home']}-{score['away']} {away.name}."
        elif t == "match.second_half":
            text = "The second half is under way."
        elif t == "match.fulltime":
            text = f"Full-time: {home.name} {score['home']}-{score['away']} {away.name}."
        elif t == "stat.update":
            lean = (home.strength - away.strength) * 60 + (stats["shots"]["home"] - stats["shots"]["away"]) * 1.5 + (stats["reds"]["away"] - stats["reds"]["home"]) * 8
            ph = int(max(28, min(72, 50 + lean + (roll(seed, fixture_id, "pos", b["m"]) - 0.5) * 6)))
            stats["possession"] = {"home": ph, "away": 100 - ph}
            text = f"{minute_label(b['p'], b['clock'])} stats: possession {ph}%-{100 - ph}%, shots {stats['shots']['home']}-{stats['shots']['away']}, xG {stats['xg']['home']:.1f}-{stats['xg']['away']:.1f}."
        else:
            assert me is not None and other is not None
            text = _text(t, b, me, other, score, home, away)
        second_key = next((k for k in ("assist", "off") if b.get(k) is not None), None)
        other_key = next((k for k in ("on", "shooter", "keeper", "by", "taker") if b.get(k) is not None), None)
        ev: dict[str, Any] = {
            "id": f"e{seq:03d}", "seq": seq, "type": t, "vt": vt, "clock": b["clock"], "period": b["p"], "minute": minute_label(b["p"], b["clock"]),
            "team": b["side"], "player": me.ref(b["num"]) if me else None,
            "player2": me.ref(b[second_key]) if me and second_key else (other.ref(b[other_key]) if other and other_key else None),
            "text": text, "score": dict(score), "tags": sorted(set(b.get("tags", [])) & FACT_TAGS), "urgent": bool(b.get("urgent")),
            "truth": {"importance": b["imp"], "story_tags": sorted(set(b.get("tags", [])) - FACT_TAGS)},
        }
        _apply(stats, ratings, ev, b, me, other)
        ev["_stats"] = deepcopy(stats)
        ev["_ratings"] = dict(ratings)
        ev["_on"] = {"home": list(home.on), "away": list(away.on)}
        ev["_bench"] = {"home": list(home.bench), "away": list(away.bench)}
        events.append(ev)
    return {"fixture_id": fixture_id, "seed": seed, "fixture": public_fixture(fixture_id), "timing": tm, "events": events, "viewers": fixture["viewers"]}


def simulate(fixture_id: str, seed: int = 7) -> dict[str, Any]:
    """The whole match. Cached and shared: treat the result as read-only (use `public()` for copies)."""
    if fixture_id not in FIXTURES:
        raise KeyError(f"unknown fixture {fixture_id!r}; try one of {', '.join(FIXTURES)}")
    return _simulate(fixture_id, int(seed))


def public(ev: dict[str, Any]) -> dict[str, Any]:
    """A feed event as the outside world sees it: no ground truth, no simulator bookkeeping."""
    return {k: v for k, v in ev.items() if k != "truth" and not k.startswith("_")}


def roster(fixture_id: str) -> dict[str, dict[str, Any]]:
    """player id -> {id, num, name, pos, team} for both squads."""
    f = FIXTURES[fixture_id]
    out: dict[str, dict[str, Any]] = {}
    for side, code in (("home", f["home"]), ("away", f["away"])):
        for p in TEAMS[code]["players"]:
            out[p["id"]] = {"id": p["id"], "num": p["num"], "name": p["name"], "pos": p["pos"], "team": side}
    return out


def state_at(match: dict[str, Any], vt: float) -> dict[str, Any]:
    """The scoreboard at virtual time `vt`: what a data feed's match-state endpoint would return."""
    tm, fx = match["timing"], match["fixture"]
    last = None
    scorers: list[dict[str, Any]] = []
    sent_off: list[dict[str, Any]] = []
    last_key_vt = 0
    for ev in match["events"]:
        if ev["vt"] > vt:
            break
        last = ev
        if ev["type"] == "goal":
            scorers.append({"player": ev["player"], "minute": ev["minute"], "team": ev["team"], "event_id": ev["id"], "penalty": "penalty" in ev["tags"]})
        if ev["type"] == "card.red":
            sent_off.append({"player": ev["player"], "minute": ev["minute"], "team": ev["team"]})
        if ev["truth"]["importance"] >= 0.4:
            last_key_vt = ev["vt"]
    ros = roster(match["fixture_id"])
    code = {"home": fx["home"]["id"], "away": fx["away"]["id"]}
    if last is None:
        first = match["events"][0]
        stats, ratings, on, bench, score = _stats_blank(), {}, first["_on"], first["_bench"], {"home": 0, "away": 0}
    else:
        stats, ratings, on, bench, score = last["_stats"], last["_ratings"], last["_on"], last["_bench"], last["score"]

    def people(side: str, nums: list[int]) -> list[dict[str, Any]]:
        return [{**ros[f"{code[side]}{n}"], "rating": ratings.get(f"{code[side]}{n}", 6.0)} for n in nums]

    top = sorted(((pid, r) for pid, r in ratings.items()), key=lambda kv: (-kv[1], kv[0]))[:6]
    return {
        **clock_at(tm, vt), "vt": int(vt), "score": dict(score), "stats": deepcopy(stats), "scorers": scorers, "sent_off": sent_off,
        "top_rated": [{**ros[pid], "rating": r} for pid, r in top],
        "on_pitch": {s: people(s, on[s]) for s in ("home", "away")}, "bench": {s: people(s, bench[s]) for s in ("home", "away")},
        "last_key_vt": last_key_vt, "end_vt": tm["end_vt"],
    }
