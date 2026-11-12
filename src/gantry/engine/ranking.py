"""Flow 1, the pure part: rank the moments, score the chat, find the themes.

Stage one of a two-stage ranker. Everything here is cheap, deterministic and explainable (every score comes with the
features that produced it), so it can run on every window of a live broadcast. Stage two is the editor in the
`curator` graph: a model re-ranks the SHORTLIST this stage produces, never the raw firehose.

Nothing here reads ground truth. The inputs are public feed events and public chat messages.
"""

from __future__ import annotations

import math
import re
from typing import Any

# What an event type is worth before context. Context (how late, what it did to the score, how the chat reacted)
# moves it from here.
BASE: dict[str, float] = {
    "goal": 0.80, "penalty.saved": 0.84, "penalty.missed": 0.80, "card.red": 0.80, "penalty.awarded": 0.78, "goal.disallowed": 0.74,
    "var.no_penalty": 0.60, "var.check": 0.55, "match.fulltime": 0.46, "shot.woodwork": 0.42, "save.big": 0.42, "chance.big": 0.40,
    "injury": 0.40, "match.halftime": 0.30, "substitution": 0.20, "card.yellow": 0.20, "match.kickoff": 0.12, "match.second_half": 0.10,
    "shot.saved": 0.08, "shot.off_target": 0.05, "corner": 0.04, "foul": 0.03, "offside": 0.03, "stat.update": 0.02,
}
KEY_TYPES = frozenset(t for t, v in BASE.items() if v >= 0.4)
HEADLINE, NOTABLE = 0.7, 0.38


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _heat(ev: dict[str, Any], comments: list[dict[str, Any]], baseline_per_min: float) -> float:
    """How loudly the chat reacted: messages in the minute after the event, relative to the match's normal rate."""
    n = sum(1 for c in comments if ev["vt"] <= c["vt"] <= ev["vt"] + 60)
    return n / max(baseline_per_min, 3.0)


def score_moment(ev: dict[str, Any], timeline: list[dict[str, Any]], comments: list[dict[str, Any]], baseline_per_min: float) -> dict[str, Any]:
    """One feed event -> a score in [0, 1] and the reasons. `timeline` is the match's key events so far (context)."""
    t = ev["type"]
    why: list[tuple[str, float]] = [(t, BASE.get(t, 0.05))]
    before = [e for e in timeline if e["vt"] < ev["vt"]]
    minute = ev["clock"] // 60
    if t in KEY_TYPES and t not in ("match.fulltime",):
        if minute >= 88:
            why.append(("stoppage-time drama", 0.12))
        elif minute >= 75:
            why.append(("late in the game", 0.06))
    if t == "goal":
        h, a = ev["score"]["home"], ev["score"]["away"]
        mine, theirs = (h, a) if ev["team"] == "home" else (a, h)
        was = mine - 1
        if not any(e["type"] == "goal" for e in before):
            why.append(("opening goal", 0.04))
        if was == theirs:
            why.append(("puts them ahead", 0.08))
        elif mine == theirs:
            why.append(("equaliser", 0.07))
        elif was > theirs + 1:
            why.append(("extends a comfortable lead", -0.06))
        deficit = max((e["score"]["away" if ev["team"] == "home" else "home"] - e["score"][ev["team"]] for e in before if e["type"] == "goal"), default=0)
        if deficit >= 2 and mine >= theirs:
            why.append(("completes a comeback", 0.08))
        mine_before = sum(1 for e in before if e["type"] == "goal" and e.get("player") and ev.get("player") and e["player"]["id"] == ev["player"]["id"])
        if mine_before == 1:
            why.append(("a brace", 0.03))
        elif mine_before >= 2:
            why.append(("a hat-trick", 0.10))
    if t in ("goal", "goal.disallowed", "penalty.saved", "penalty.missed", "var.no_penalty") and any(
        e["type"] in ("var.check", "penalty.awarded") and 0 < ev["vt"] - e["vt"] <= 200 for e in before
    ):
        why.append(("resolves a story the chat was waiting on", 0.05))
    if t == "substitution" and "forced" in ev.get("tags", []):
        why.append(("forced by injury", 0.06))
    heat = _heat(ev, comments, baseline_per_min)
    if heat >= 1.5 and t not in ("match.kickoff", "stat.update"):
        why.append((f"chat spiked {heat:.1f}x", min(0.10, 0.025 * heat)))
    score = clamp(sum(d for _, d in why))
    return {
        "event_id": ev["id"], "type": t, "vt": ev["vt"], "minute": ev["minute"], "team": ev["team"], "player": ev.get("player"), "player2": ev.get("player2"),
        "text": ev["text"], "tags": ev.get("tags", []), "urgent": bool(ev.get("urgent")), "score_line": ev["score"],
        "score": round(score, 3), "heat": round(heat, 2), "why": [{"label": lbl, "delta": round(d, 3)} for lbl, d in why],
        "tier": "headline" if score >= HEADLINE else "notable" if score >= NOTABLE else "routine",
    }


def rank_moments(events: list[dict[str, Any]], timeline: list[dict[str, Any]], comments: list[dict[str, Any]], baseline_per_min: float) -> list[dict[str, Any]]:
    scored = [score_moment(ev, timeline, comments, baseline_per_min) for ev in events]
    return sorted(scored, key=lambda m: (-m["score"], -m["vt"]))


# ------------------------------------------------------------------------------------------------ chat scoring
_URL = re.compile(r"(https?://|www\.|\b[\w-]+\.(?:com|io|ly|net|co|gg)\b|bit\.ly)", re.I)
_PROMO_PHRASES = ("my channel", "my page", "link in my bio", "in my bio", "go subscribe", "sub for more", "follow me", "dm me", "check my", "subbing back")
_PROMO_WORDS = frozenset({"free", "giveaway", "sub4sub", "followers", "views", "tips", "telegram", "crypto", "earn", "promo", "cheap", "win rate", "fixed"})
_ABUSE = ("scum", "idiot", "clown", "brain-dead", "braindead", "useless waste", "waste of space", "disgrace of a human", "thug", "trash", "garbage",
          "hate him", "pathetic", "what's coming to him", "ban him for life", "shut up")
_REASONING = ("because", "that is why", "that's why", " if ", " so ", "need to", "needs to", "need a", "should", "has to", "have to", "means",
              "every ", "again", "tells you", "the difference", "by the rules", "that is how", "can't", "nobody", "only ")
_TOPICAL = frozenset({
    "goal", "offside", "penalty", "pen", "var", "ref", "referee", "keeper", "goalkeeper", "save", "header", "corner", "press", "midfield", "xg",
    "shots", "shot", "sub", "subs", "formation", "marking", "defender", "defenders", "striker", "full-back", "full-backs", "centre-backs", "pass",
    "run", "space", "lines", "possession", "set", "pieces", "counter", "handball", "yellow", "red", "card", "bench", "finish", "replay", "foul",
})
_WORD = re.compile(r"[a-z0-9'-]+")


def tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def norm_key(text: str) -> str:
    """Near-duplicate key: lower-case, no punctuation or emoji, repeated letters and repeated words collapsed."""
    words = [re.sub(r"(.)\1{2,}", r"\1", w) for w in tokens(text)]
    out: list[str] = []
    for w in words:
        if not out or out[-1] != w:
            out.append(w)
    return " ".join(out)


def build_lexicon(moments: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, list[str]]:
    """The names worth mentioning right now: the players of the window's best moments (strong signal) and both clubs (weak)."""
    teams: set[str] = set()
    players: set[str] = set()
    fx = state.get("fixture") or {}
    for side in ("home", "away"):
        teams.update(tokens((fx.get(side) or {}).get("name", "")))
    for m in moments[:6]:
        if m.get("tier") == "routine":
            continue
        for key in ("player", "player2"):
            if m.get(key):
                players.update(tokens(m[key]["name"]))
    teams -= {"city", "town", "united", "athletic", "park"}
    return {"players": sorted(n for n in players if len(n) > 2), "teams": sorted(n for n in teams if len(n) > 2)}


def score_comment(c: dict[str, Any], lexicon: dict[str, list[str]], seen: dict[str, int]) -> dict[str, Any]:
    """One chat message -> score, label and reasons. `seen` counts near-duplicate keys already met (mutated)."""
    text = c["text"]
    low = text.lower()
    toks = tokens(text)
    words = len(toks)
    reasons: list[str] = []

    spam = 0.0
    if _URL.search(text):
        spam += 0.6
    spam += 0.7 * sum(1 for p in _PROMO_PHRASES if p in low)
    spam += 0.3 * sum(1 for w in _PROMO_WORDS if w in low)
    if "$" in text or "100%" in text:
        spam += 0.3
    spam = clamp(spam)
    toxic = clamp(0.6 * sum(1 for a in _ABUSE if a in low))

    key = norm_key(text)
    nth = seen.get(key, 0)
    seen[key] = nth + 1
    novelty = 1.0 if nth == 0 else 0.25 if nth == 1 else 0.1

    uniq = set(toks)
    player_hits = sum(1 for t in uniq if t in lexicon["players"])
    team_hits = sum(1 for t in uniq if t in lexicon["teams"])
    topical_hits = sum(1 for t in uniq if t in _TOPICAL)
    relevance = clamp(0.5 * player_hits + 0.15 * team_hits + 0.2 * topical_hits)

    letters = [ch for ch in text if ch.isalpha()]
    shouting = bool(letters) and sum(ch.isupper() for ch in letters) / len(letters) > 0.6
    is_question = "?" in text and words >= 4 and not shouting
    substance = 0.6 * min(1.0, words / 14)
    substance += min(0.4, 0.2 * sum(1 for r in _REASONING if r in f" {low} "))
    if re.search(r"\d", text) and not spam:
        substance += 0.12
    if is_question:
        substance += 0.15
    if shouting and words <= 8:
        substance *= 0.4
    if words == 0:
        substance = 0.0
    substance = clamp(substance)

    engagement = clamp(math.log1p(c.get("likes", 0)) / math.log1p(40))
    badge = {"mod": 0.05, "member": 0.03}.get((c.get("author") or {}).get("badge") or "", 0.0)
    score = (0.30 * relevance + 0.34 * substance + 0.18 * engagement + 0.18 * novelty + badge + 0.08 * min(1, player_hits)) * (1 - toxic) * (1 - spam)

    if spam >= 0.55:
        label = "hide"
        reasons.append("spam or self-promotion")
    elif toxic >= 0.55:
        label = "hide"
        reasons.append("abusive language")
    elif nth >= 1:
        label = "collapse"
        reasons.append("repeated chant" if words <= 5 else "already said")
    elif words <= 2 or (shouting and words <= 4):
        label = "keep"
        reasons.append("pure reaction")
    else:
        label = "keep"
    if label == "keep":
        if relevance >= 0.45:
            reasons.append("on the moment")
        if substance >= 0.6:
            reasons.append("makes an argument")
        if is_question:
            reasons.append("asks a real question")
        if engagement >= 0.6:
            reasons.append("the chat likes it")
    return {
        "id": c["id"], "vt": c["vt"], "author": c.get("author"), "text": text, "likes": c.get("likes", 0), "score": round(clamp(score), 3), "label": label,
        "question": is_question, "reasons": reasons, "key": key,
        "features": {"relevance": round(relevance, 2), "substance": round(substance, 2), "engagement": round(engagement, 2), "novelty": round(novelty, 2),
                     "spam": round(spam, 2), "toxic": round(toxic, 2)},
    }


def dedupe_pass(scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shards are scored in parallel, each blind to the others: this pass finds what was said twice ACROSS them."""
    seen: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for c in sorted(scored, key=lambda x: (x["vt"], x["id"])):
        nth = seen.get(c["key"], 0)
        seen[c["key"]] = nth + 1
        if nth and c["features"]["novelty"] == 1.0 and c["label"] == "keep":
            words = len(tokens(c["text"]))
            c = {**c, "score": round(max(0.0, c["score"] - 0.135), 3), "features": {**c["features"], "novelty": 0.25},
                 "label": "collapse", "reasons": ["repeated chant" if words <= 5 else "already said"]}
        out.append(c)
    return out


def shortlist(scored: list[dict[str, Any]], k: int = 8, floor: float = 0.5) -> list[dict[str, Any]]:
    """The best comments worth an editor's eye: high score, not hidden, no two saying the same thing or from the same author."""
    out: list[dict[str, Any]] = []
    keys: set[str] = set()
    authors: dict[str, int] = {}
    for c in sorted(scored, key=lambda x: (-x["score"], x["vt"])):
        if c["label"] != "keep" or c["score"] < floor or c["key"] in keys:
            continue
        aid = (c.get("author") or {}).get("id", "")
        if authors.get(aid, 0) >= 1:
            continue
        keys.add(c["key"])
        authors[aid] = authors.get(aid, 0) + 1
        out.append(c)
        if len(out) >= k:
            break
    return out


# ------------------------------------------------------------------------------------------------------ themes
THEMES: dict[str, tuple[str, tuple[str, ...]]] = {
    "var": ("the VAR call", ("var", "offside", "replay", "clear and obvious", "toenail", "ruled out", "flag", "robbed")),
    "penalty": ("the penalty", ("penalty", " pen ", " pen?", "handball", "dive", "spot", "stonewall", "pens ")),
    "referee": ("the referee", ("ref ", "ref.", "ref!", "referee", " red", "sent off", "decision", "last man", "booked")),
    "subs": ("the substitutions", (" sub ", "subs ", "bench", "come on", "coming on", "taking", "change", "starting", "bring on")),
    "keeper": ("the goalkeeping", ("keeper", "goalkeeper", "save", "saved", "near post")),
    "tactics": ("the tactics", ("press", "midfield", "formation", "full-back", "overloading", "between the lines", "xg", "possession", "set piece", "isolated", "back four")),
    "prediction": ("how this ends", ("final score", "calling it", "win this", "wins this", "next goal", "scores next", "written all over", "book it")),
}


def find_themes(scored: list[dict[str, Any]], top: int = 4) -> list[dict[str, Any]]:
    """What the (non-hidden) chat is talking about, biggest first, with a sample and the best question on it."""
    live = [c for c in scored if c["label"] != "hide"]
    total = max(len(live), 1)
    out: list[dict[str, Any]] = []
    for key, (label, needles) in THEMES.items():
        hits = [c for c in live if any(n in f" {c['text'].lower()} " for n in needles)]
        if len(hits) < 3:
            continue
        best = sorted(hits, key=lambda c: -c["score"])
        question = next((c["text"] for c in best if c["question"]), None)
        out.append({"key": key, "label": label, "count": len(hits), "share": round(len(hits) / total, 3), "sample": [c["id"] for c in best[:2]], "question": question})
    return sorted(out, key=lambda t: (-t["count"], t["key"]))[:top]
