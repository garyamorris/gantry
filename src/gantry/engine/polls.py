"""The first feature, the pure part: which polls a live moment deserves, and what a good poll looks like.

`plan()` turns a curated rundown into ranked poll candidates. A candidate is a TEMPLATE applied to the moment: a
topic key (what it is about, used by memory so the same thing is never asked twice), the facts a writer may use, and
baseline wording in three voices. The `poll_studio` graph's writers start from that wording; a model may improve it,
`validate()` keeps whatever comes back inside the rules, and `rubric()` is the judge.

Candidates are ranked on what is knowable now: how big the moment is, how well the template fits, how fresh it still
is, and what MEMORY says this audience actually votes on (`prefs`, learned by `memory_keeper`).
"""

from __future__ import annotations

import re
from typing import Any

WRITERS = ("analyst", "hype", "terrace")
MAX_QUESTION = 90
MAX_OPTION = 26
PASS_MARK = 0.78
VALUE_FLOOR = 0.52  # below this a poll is not worth interrupting the chat for
_LEADING = ("obviously", "surely", "disgrace", "robbed", "cheat", "clearly wrong", "joke of a")

Wording = dict[str, dict[str, Any]]


def _w(analyst: tuple[str, list[str]], hype: tuple[str, list[str]], terrace: tuple[str, list[str]], long: str | None = None) -> Wording:
    out: Wording = {k: {"question": q, "options": o} for k, (q, o) in (("analyst", analyst), ("hype", hype), ("terrace", terrace))}
    if long:
        out["analyst"]["long"] = long  # the analyst's wordy first draft: what the judge sends back
    return out


def _cand(template: str, topic: str, *, fit: float, wording: Wording, event_id: str | None = None, risk: str = "low", duration_s: int = 75,
          ttl_s: int = 180, facts: dict[str, Any] | None = None, allowed: list[str] | None = None, mentions: list[str] | None = None) -> dict[str, Any]:
    return {"template": template, "topic": topic, "event_id": event_id, "fit": fit, "risk": risk, "duration_s": duration_s, "ttl_s": ttl_s,
            "facts": facts or {}, "wording": wording, "allowed_options": allowed, "mentions": mentions or []}


def _name(p: dict[str, Any] | None) -> str:
    return p["name"] if p else "He"


def _teams(state: dict[str, Any]) -> tuple[str, str]:
    fx = state["fixture"]
    return fx["home"]["name"], fx["away"]["name"]


def _side_name(state: dict[str, Any], side: str | None) -> str:
    return state["fixture"][side or "home"]["name"]


# ------------------------------------------------------------------------------------- templates for a moment
def _for_moment(m: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    t, eid, player = m["type"], m["event_id"], _name(m.get("player"))
    home, away = _teams(state)
    h, a = m["score_line"]["home"], m["score_line"]["away"]
    team = _side_name(state, m.get("team"))
    facts = {"minute": m["minute"], "player": player, "team": team, "score": f"{home} {h}-{a} {away}", "what": m["text"]}
    out: list[dict[str, Any]] = []
    if t == "goal":
        out.append(_cand("goal_rating", f"goal_rating:{eid}", fit=0.8, event_id=eid, facts=facts, mentions=[player], ttl_s=170, wording=_w(
            (f"How good was that {player} finish?", ["World class", "Well taken", "Defensive error"]),
            (f"RATE THAT {player.upper()} GOAL 🔥", ["🔥 Worldie", "👏 Tidy", "🤷 Lucky"]),
            (f"{player}'s goal: how are we rating it?", ["Absolute worldie", "Decent finish", "Keeper's fault"]),
            long=f"{player} has scored for {team} to make it {h}-{a}. Taking everything into account, how would you rate the quality of that goal?",
        )))
        goals_by = sum(1 for s in state.get("scorers", []) if s["player"]["id"] == (m.get("player") or {}).get("id"))
        if goals_by == 2 and m["vt"] < state["end_vt"] - 420:
            out.append(_cand("hattrick_watch", f"hattrick:{(m.get('player') or {}).get('id')}", fit=0.85, event_id=eid, facts=facts, mentions=[player], ttl_s=240, wording=_w(
                (f"Will {player} complete his hat-trick?", ["Yes", "No"]),
                (f"{player.upper()} HAS TWO. HAT-TRICK? 🎩", ["🎩 He gets it", "❌ Not today"]),
                (f"Two for {player}. Does he get the match ball?", ["He gets his third", "Stays on two"]),
            )))
    if t in ("goal", "card.red", "penalty.saved", "goal.disallowed") and m["vt"] > 900 and m["vt"] < state["end_vt"] - 240:
        reds = len(state.get("sent_off", []))
        out.append(_cand("winner_from_here", f"winner:{h}-{a}:{reds}", fit=0.7 if t == "goal" else 0.6, event_id=eid, facts=facts, ttl_s=300, duration_s=90, wording=_w(
            ("Who wins from here?", [home, "Draw", away]),
            (f"{h}-{a}! WHO TAKES IT? 👀", [home, "Draw", away]),
            ("Be honest: how does this one finish?", [f"{home} win", "Draw", f"{away} win"]),
            long=f"With the score now at {home} {h}-{a} {away} and plenty of time still left on the clock, which of these do you think is the most likely final result?",
        )))
    if t == "goal.disallowed":
        out.append(_cand("decision", f"decision:{eid}", fit=0.92, event_id=eid, risk="medium", facts=facts, mentions=[player], ttl_s=220, duration_s=90, wording=_w(
            (f"{player}'s goal ruled out for offside. Right call?", ["✅ Correct", "❌ Wrong", "🤷 Too tight to call"]),
            ("VAR SAYS NO GOAL 😱 RIGHT CALL?", ["✅ Correct", "❌ Wrong", "🤷 Too tight"]),
            (f"{player}'s goal chalked off. Fair enough?", ["Offside is offside", "Never offside", "Toss of a coin"]),
            long=f"{player} thought he had scored for {team} but the goal was ruled out for offside after a VAR review. Do you agree with the decision that was made?",
        )))
    if t == "var.no_penalty":
        out.append(_cand("decision", f"decision:{eid}", fit=0.9, event_id=eid, risk="medium", facts=facts, mentions=[player], ttl_s=220, duration_s=90, wording=_w(
            (f"No penalty for the challenge on {player}. Agree?", ["Correct, play on", "Clear penalty", "Seen them given"]),
            ("NO PENALTY! 👀 GOOD CALL?", ["✅ Play on", "❌ Penalty", "🤷 50/50"]),
            (f"{player} goes down, VAR says no pen. Your verdict?", ["Never a penalty", "Stonewall", "Seen them given"]),
        )))
    if t == "card.red":
        out.append(_cand("red_card", f"red:{eid}", fit=0.9, event_id=eid, risk="medium", facts=facts, mentions=[player], ttl_s=220, duration_s=90, wording=_w(
            (f"Red card for {player}: the right decision?", ["Deserved", "Harsh", "Never a foul"]),
            (f"{player.upper()} SEES RED 🟥 DESERVED?", ["🟥 Off he goes", "😤 Harsh", "🤷 Not even a foul"]),
            (f"{player} sent off. Did he have to go?", ["Had to go", "Harsh that", "Never a foul"]),
        )))
    if t == "penalty.awarded":
        out.append(_cand("penalty_outcome", f"pen:{eid}", fit=0.95, event_id=eid, facts=facts, mentions=[player], ttl_s=75, duration_s=45, wording=_w(
            (f"{player} from the spot. What happens?", ["⚽ Scores", "🧤 Saved", "🚀 Misses"]),
            (f"PENALTY! {player.upper()} STEPS UP 😬", ["⚽ Scores", "🧤 Saved", "🚀 Misses"]),
            (f"{player} to take it. Call it!", ["Back of the net", "Keeper saves it", "Row Z"]),
        )))
    if t in ("penalty.saved", "save.big"):
        keeper = player
        out.append(_cand("keeper_save", f"save:{eid}", fit=0.75 if t == "penalty.saved" else 0.55, event_id=eid, facts=facts, mentions=[keeper], ttl_s=180, wording=_w(
            (f"How good was that {keeper} save?", ["Save of the season", "Great stop", "Should be scored"]),
            (f"{keeper.upper()} SAYS NO 🧤 RATE IT", ["🧤 Save of the season", "👏 Great stop", "🤷 Poor shot"]),
            (f"That {keeper} save: brilliant or routine?", ["Brilliant", "Good save", "Bread and butter"]),
        )))
    if t in ("chance.big", "shot.woodwork") and m["score"] >= 0.5:
        out.append(_cand("miss", f"miss:{eid}", fit=0.5, event_id=eid, facts=facts, mentions=[player], ttl_s=150, wording=_w(
            (f"How has {player} not scored there?", ["Has to score", "Unlucky", "Great defending"]),
            (f"HOW HAS {player.upper()} MISSED?! 😱", ["😱 Sitter", "🍀 Unlucky", "🧱 Great defending"]),
            (f"{player} so close. Unlucky or wasteful?", ["Wasteful", "Unlucky", "Credit the defence"]),
        )))
    if t == "substitution" and "forced" not in m.get("tags", []):
        off = _name(m.get("player2"))
        out.append(_cand("sub_verdict", f"sub:{eid}", fit=0.4, event_id=eid, facts=facts, mentions=[player, off], ttl_s=200, wording=_w(
            (f"{player} on for {off}: the right change?", ["Good change", "Wrong man off", "Too late"]),
            (f"{player.upper()} IS ON! 🔄 GOOD MOVE?", ["✅ Good change", "❌ Wrong man off", "⏰ Too late"]),
            (f"{off} off, {player} on. Happy with that?", ["Good call", "Wrong man off", "Should be earlier"]),
        )))
    return out


# ----------------------------------------------------------------------------- templates for the state of play
def _for_state(state: dict[str, Any], themes: list[dict[str, Any]], trigger: dict[str, Any], to_vt: int) -> list[dict[str, Any]]:
    home, away = _teams(state)
    phase = state["phase"]
    out: list[dict[str, Any]] = []
    top = [p["name"] for p in state.get("top_rated", [])[:4]]
    bucket = to_vt // 900
    if phase == "halftime" or trigger.get("reason") == "match.halftime":
        if len(top) >= 3:
            out.append(_cand("potm_half", "potm:first_half", fit=0.62, facts={"candidates": top}, allowed=top, ttl_s=170, duration_s=100, wording=_w(
                ("Best player of the first half?", top), ("FIRST-HALF MVP? 🏅", top), ("Who's been the best player so far?", top),
            )))
        out.append(_cand("second_half", "second_half", fit=0.5, ttl_s=170, duration_s=100, wording=_w(
            ("What does the second half bring?", [f"{home} on top", f"{away} on top", "More of the same"]),
            ("SECOND HALF PREDICTIONS 🔮", [f"{home} on top", f"{away} on top", "More of the same"]),
            ("Second half: who comes out stronger?", [home, away, "Nothing changes"]),
        )))
        return out
    if phase == "fulltime" or trigger.get("reason") == "match.fulltime":
        if len(top) >= 3:
            out.append(_cand("motm", "motm", fit=0.8, facts={"candidates": top}, allowed=top, ttl_s=400, duration_s=120, wording=_w(
                ("Who was the player of the match?", top), ("PLAYER OF THE MATCH? 🏆", top), ("Your player of the match?", top),
            )))
        return out
    h, a = state["score"]["home"], state["score"]["away"]
    since_key = to_vt - state.get("last_key_vt", 0)
    if since_key >= 420:
        out.append(_cand("next_goal", f"next_goal:{bucket}", fit=0.45, ttl_s=300, duration_s=90, wording=_w(
            ("Who scores next?", [home, away, "No more goals"]), ("NEXT GOAL: WHO GETS IT? ⚽", [home, away, "No more goals"]),
            ("Next goal goes to...", [home, away, "Nobody"]),
        )))
        if since_key >= 780:
            half = "first" if state["period"] == 1 else "second"
            out.append(_cand("vibe_check", f"vibe:{state['period']}", fit=0.4, ttl_s=300, wording=_w(
                (f"How are you rating this {half} half?", ["Great watch", "It's fine", "Wake me up"]),
                ("RATE THIS HALF SO FAR 👇", ["🔥 Great watch", "😐 It's fine", "💤 Wake me up"]),
                (f"Honest review of this {half} half?", ["Loving it", "Seen worse", "Wake me up"]),
            )))
    minute = state["clock"] // 60
    if 55 <= minute <= 80 and since_key >= 240:
        side = "home" if h <= a else "away"
        bench = [p["name"] for p in state["bench"][side] if p["pos"] != "GK"][:3]
        if len(bench) >= 2:
            team = _side_name(state, side)
            opts = bench + ["No changes"]
            out.append(_cand("sub_wish", f"sub_wish:{side}:{bucket}", fit=0.5, facts={"team": team, "candidates": bench}, allowed=opts, ttl_s=300, duration_s=90, wording=_w(
                (f"Who should {team} bring on?", opts), (f"{team.upper()} NEED A SPARK ⚡ WHO COMES ON?", opts), (f"{team} need a change. Who's coming on?", opts),
            )))
    split = {"var": ["It was the right call", "They got it wrong", "Bin VAR"], "penalty": ["Penalty", "Never a penalty", "Seen them given"],
             "referee": ["Ref's had a good game", "Ref's had a shocker", "No strong view"], "subs": ["Changes needed now", "Leave it as it is", "Too late already"]}
    for th in themes:
        if th["key"] in split and th["share"] >= 0.2 and th["count"] >= 6:
            out.append(_cand("chat_split", f"split:{th['key']}:{bucket}", fit=0.58, risk="medium" if th["key"] in ("var", "referee", "penalty") else "low",
                             facts={"theme": th["label"], "share": th["share"], "question_from_chat": th.get("question")}, ttl_s=240, wording=_w(
                (f"Chat is split on {th['label']}. Where do you stand?", split[th["key"]]),
                (f"CHAT CAN'T AGREE ON {th['label'].upper()} 🥊", split[th["key"]]),
                (f"Everyone's arguing about {th['label']}. You?", split[th["key"]]),
            )))
            break
    return out


# ------------------------------------------------------------------------------------------------------ planning
def expected_engagement(template: str, prefs: dict[str, dict[str, Any]]) -> float:
    """What memory says this audience does with this kind of poll: 0.5 with no history, then the running mean takes over."""
    p = prefs.get(template)
    if not p:
        return 0.5
    out = 0.5
    if p.get("n"):
        learned = max(0.0, min(1.0, float(p.get("mean", 0.0)) / 0.30))  # 30% of viewers voting is a great poll
        n = 1.5 * float(p["n"])  # evidence outweighs the prior quickly: two polls are worth three priors
        out = (0.5 + learned * n) / (1 + n)
    rejected, accepted = int(p.get("rejected", 0)), int(p.get("approved", 0)) + int(p.get("edited", 0))
    if rejected >= 2 and rejected > accepted:
        out *= 0.75  # the humans keep saying no to this kind of poll: that is a preference too
    return round(out, 3)


def plan(moments: list[dict[str, Any]], themes: list[dict[str, Any]], state: dict[str, Any], trigger: dict[str, Any], to_vt: int,
         *, prefs: dict[str, dict[str, Any]] | None = None, used: dict[str, int] | None = None, limit: int = 3) -> list[dict[str, Any]]:
    """Ranked poll candidates for this window (possibly none). `used` = how often each template has run this match."""
    prefs, used = prefs or {}, used or {}
    cands: list[dict[str, Any]] = []
    for m in moments[:4]:
        if m["tier"] == "routine":
            continue
        for c in _for_moment(m, state):
            age = max(0, to_vt - m["vt"])
            c["priority"] = round(m["score"] * c["fit"], 3)
            c["freshness"] = round(max(0.0, 1 - age / c["ttl_s"]), 3)
            c["expires_vt"] = m["vt"] + c["ttl_s"]
            c["event_vt"] = m["vt"]
            cands.append(c)
    pending_call = any(m["type"] in ("var.check", "penalty.awarded") for m in moments[:4])
    for c in _for_state(state, themes, trigger, to_vt):
        if pending_call and c["template"] == "chat_split":
            continue  # a call is still being made: ask about the decision, not about the argument
        c["priority"] = round(c["fit"], 3)
        c["freshness"] = 1.0
        c["expires_vt"] = to_vt + c["ttl_s"]
        c["event_vt"] = to_vt
        cands.append(c)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for c in cands:
        if c["topic"] in seen or c["freshness"] <= 0:
            continue
        seen.add(c["topic"])
        c["expected"] = expected_engagement(c["template"], prefs)
        c["fatigue"] = round(0.12 * used.get(c["template"], 0), 3)
        c["value"] = round(0.45 * c["priority"] + 0.40 * c["expected"] + 0.15 * c["freshness"] - c["fatigue"], 3)
        c["weak"] = c["value"] < VALUE_FLOOR
        out.append(c)
    return sorted(out, key=lambda c: (-c["value"], c["topic"]))[:limit]


# --------------------------------------------------------------------------------------- drafting and judging
def baseline_draft(cand: dict[str, Any], writer: str, revision: int = 0) -> dict[str, Any]:
    """The deterministic draft a writer starts from (and what the offline model returns)."""
    w = cand["wording"][writer if writer in cand["wording"] else "analyst"]
    question = w["long"] if (revision == 0 and w.get("long")) else w["question"]
    return {"question": question, "options": list(w["options"])}


def validate(draft: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any]:
    """Whatever a model wrote, make it a legal poll or raise ValueError: 2-4 distinct short options, a real question."""
    question = re.sub(r"\s+", " ", str(draft.get("question") or "")).strip()
    raw = draft.get("options")
    if not question or not isinstance(raw, list):
        raise ValueError("a poll needs a question and a list of options")
    options: list[str] = []
    for o in raw:
        text = re.sub(r"\s+", " ", str(o)).strip()
        if text and text.lower() not in {x.lower() for x in options}:
            options.append(text[:40])
    allowed = cand.get("allowed_options")
    if allowed:  # options are names from the feed: a model may reorder or drop them, never invent one
        options = [o for o in options if o in allowed]
    if len(options) < 2:
        raise ValueError("fewer than two usable options")
    return {"question": question[:140], "options": options[:4]}


def _jaccard(a: str, b: str) -> float:
    ta, tb = set(re.findall(r"[a-z0-9]+", a.lower())), set(re.findall(r"[a-z0-9]+", b.lower()))
    return len(ta & tb) / max(len(ta | tb), 1)


def rubric(draft: dict[str, Any], cand: dict[str, Any], avoid: list[str] | None = None) -> dict[str, Any]:
    """The judge: clear, answerable, neutral, grounded in the moment, and not something we already asked."""
    q, opts = draft["question"], draft["options"]
    notes: list[str] = []
    clarity = 1.0 if len(q) <= 64 else 0.8 if len(q) <= MAX_QUESTION else 0.2
    if len(q) > MAX_QUESTION:
        notes.append(f"question is {len(q)} characters; a live poll is read in two seconds, keep it under {MAX_QUESTION}")
    options = 1.0
    if not 2 <= len(opts) <= 4:
        options -= 0.5
        notes.append("use between two and four options")
    if any(len(o) > MAX_OPTION for o in opts):
        options -= 0.3
        notes.append(f"options must fit a phone: {MAX_OPTION} characters at most")
    neutral = 1.0
    if any(word in q.lower() for word in _LEADING):
        neutral = 0.3
        notes.append("the question leads the voter; ask it neutrally")
    grounded = 1.0
    if cand.get("mentions") and not any(n.lower() in q.lower() for n in cand["mentions"]):
        grounded = 0.5
        notes.append("name the player the moment is about")
    worst = max((_jaccard(q, old) for old in (avoid or [])), default=0.0)
    novelty = 1.0 if worst < 0.6 else 0.3
    if worst >= 0.6:
        notes.append("too close to a poll already asked this match")
    score = 0.34 * clarity + 0.22 * options + 0.18 * neutral + 0.14 * grounded + 0.12 * novelty
    return {"score": round(score, 3), "passed": score >= PASS_MARK, "notes": notes,
            "parts": {"clarity": clarity, "options": round(options, 2), "neutral": neutral, "grounded": grounded, "novelty": novelty}}
