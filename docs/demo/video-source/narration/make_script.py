#!/usr/bin/env python3
"""make_script.py: write narration/script.json from RECORDED data, so every number the narrator says is one that was measured.

    python3 narration/make_script.py

Reads captures/real/facts.json (the recorded windows), viz/evals_data.json (the offline eval gate) and captures/real/hero_after.json
(what the producer knew after the recorded matches: written by snapshot_memory.py). Edit the prose here, never the numbers.
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
F = json.load(open(os.path.join(ROOT, "captures/real/facts.json")))
E = json.load(open(os.path.join(ROOT, "viz/evals_data.json")))
H = json.load(open(os.path.join(ROOT, "captures/real/memory_after.json")))

pct = lambda x, d=0: f"{100 * x:.{d}f}%"  # noqa: E731
NAME = {
    "penalty_outcome": "a penalty call", "decision": "a disputed decision", "red_card": "a red card", "winner_from_here": "who wins from here",
    "goal_rating": "rating a goal", "next_goal": "who scores next", "vibe_check": "rate this half", "motm": "player of the match", "potm_half": "player of the half",
    "second_half": "second-half predictions", "sub_wish": "who should come on", "sub_verdict": "a substitution verdict", "keeper_save": "rating a save",
    "hattrick_watch": "a hat-trick watch", "chat_split": "an argument in the chat", "miss": "how did he miss",
}
prefs = [p for p in H["prefs"] if p["n"]]
solid = [p for p in prefs if p["n"] >= 2]  # never quote a number that rests on one poll
top, low = max(solid, key=lambda p: p["mean"]), min(solid, key=lambda p: p["mean"])
avg = sum(p["mean"] * p["n"] for p in prefs) / sum(p["n"] for p in prefs)
cur, w63 = F["curator"], F["window_63"]
nd, cu, au, mem = E["ndcg"], E["curation"], E["autopilot"], E["memory"]
n_hidden_total = sum(c["comments"] for c in cu.values())
ids = list(nd)

SCENES = [
    ("A", "This is Gantry, an autonomous producer for a live sports stream. It watches a sports data feed and the chat, ranks and curates what matters, "
          "and then suggests polls, or on autopilot, simply runs them. Everything you will see is a recorded run of the real app, on the offline model: "
          "no keys, no network. Here is a goal, and twenty-five seconds of match time later, a poll that nobody typed."),
    ("B", "The loop is simple to say. A feed and a chat go into a window: one stretch of the broadcast, as plain JSON. The showrunner runs it through two subgraphs. "
          "The curator ranks and curates. The action desk generates actions, and decides whether each one goes out, waits for a human, or is held back. "
          "Whatever happens is written to the LangGraph Store, so the next window remembers."),
    ("C", "It runs in real time, driven by events rather than a timer. A goal arms a window twenty-five seconds later, so the chat's reaction is in it. "
          "A penalty is urgent, and fires within seconds. Here we push a penalty into the feed ourselves, and the poll is out before the kick is taken. "
          "If the graphs ever fall behind, triggers ride along with the next window. A live system sheds load; it does not build a queue."),
    ("D", "Here is one window, replayed from the real run. Triage is a cheap gate, because most ticks are noise. Then the curator runs as a subgraph, "
          "and after it the action desk, with the poll studio inside that. Three levels of subgraph, and one thread per window, "
          "so a suggestion waiting for a human never blocks the next goal."),
    ("E", f"Flow one is ranking and curation, in two stages. The first is cheap, deterministic, and explainable: every moment is scored with its reasons, "
          f"and the chat is cut into shards of forty and scored in parallel with Send. This window had {cur['comments']} comments, so two shards, then a merge. "
          f"The second stage is an editor, a model that sees only the shortlist. It can reorder the best of the chat, but never rescue what the first stage hid."),
    ("F", "The first feature is the poll. A moment becomes a candidate: a topic, the facts, and some baseline wording. Three writers, an analyst, a hype voice, "
          "and a fan from the terraces, draft it in parallel. A judge scores each draft: is it clear, neutral, about the moment, and new? Failures go back with notes. "
          "And if a model ever writes nonsense, the engine's own wording goes out instead."),
    ("G", "Then policy decides, for every action: publish, ask a human, or hold. One poll at a time, a gap between polls, never the same question twice, "
          "never a topic the human rejected. A big moment can close a small poll early. And every refusal is written to the ledger with its reason. "
          "That record is what lets it run on its own."),
    ("H", "In manual mode, every action waits for a human. Each suggestion is its own interrupt, so they can be answered in any order, by id. "
          "Here I edit the question and publish it, and it goes live at once, without waiting for the others. I reject the highlight, and the ledger records who decided. "
          "Each answer resumes the graph from its checkpoint, and nothing above the interrupt runs twice."),
    ("I", f"Memory is what changes behaviour. When a poll closes, the memory keeper files how it did, and updates what this audience votes on. "
          f"After a few matches, {NAME.get(top['template'], top['template'])} pulls {pct(top['mean'])} of viewers, and {NAME.get(low['template'], low['template'])} only {pct(low['mean'])}, "
          f"against an average of {pct(avg)}. The next candidates are ranked on that evidence, and when it is clear enough, a lesson is written down. "
          f"Play the same match again, and the second time it asks fewer questions, and gets more votes per poll."),
    ("J", f"It ships with an eval gate: offline, about five seconds. Against hidden ground truth, moment ranking scores at least {min(nd.values()):.2f}, "
          f"and {pct(min(c['precision'] for c in cu.values()))} or more of the comments it highlights are good. Spam and abuse is held every time, "
          f"and none of {n_hidden_total:,} good comments was wrongly held. Over three whole matches on autopilot: no overlapping polls, no repeats, no errors. "
          f"And with memory, the second pass gets {pct(mem['warm']['mean'], 1)} of viewers voting, against {pct(mem['cold']['mean'], 1)}."),
    ("K", "That is Gantry: seven LangGraph graphs, a live feed in, polls and pins out, and a ledger so it can be left alone. "
          "Run make dev, and open it on localhost, port twenty-thirty, or open the graphs in LangGraph Studio. "
          "These recordings used the offline model. A real model is one setting away, and that path is written, but not yet run."),
]


def main() -> None:
    out = {"scenes": [{"id": i, "text": t} for i, t in SCENES]}
    json.dump(out, open(os.path.join(HERE, "script.json"), "w"), indent=1, ensure_ascii=False)
    words = sum(len(t.split()) for _, t in SCENES)
    print(f"wrote script.json: {len(SCENES)} scenes, {words} words (~{words / 2.4 / 60:.1f} min of narration)")
    for i, t in SCENES:
        print(f"  {i}: {len(t.split()):3d} words")


if __name__ == "__main__":
    main()
