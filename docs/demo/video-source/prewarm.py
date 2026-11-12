#!/usr/bin/env python3
"""prewarm.py: play whole matches on the running server (autopilot, 60x, one after another) so the producer has real history for the memory footage.

    python3 prewarm.py [--base http://127.0.0.1:2030] [fixture:seed ...]      default: goalfest:7 stalemate:7 derby:11

Each match is the real app on the real server: the same dispatcher, graphs and Store as a person watching. They run one at a time so they do not slow
each other's clocks. Standard library only.
"""
import argparse
import json
import time
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:2030")
ap.add_argument("plays", nargs="*", default=["goalfest:7", "stalemate:7", "derby:11"])
a = ap.parse_args()


def call(method, path, body=None):
    req = urllib.request.Request(a.base + path, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"}, method=method)
    return json.load(urllib.request.urlopen(req, timeout=60))


for play in a.plays:
    fixture, seed = play.split(":")
    snap = call("POST", "/api/matches", {"fixture": fixture, "seed": int(seed), "mode": "autopilot", "speed": 60})
    mid, t0 = snap["match_id"], time.time()
    while True:
        sb = call("GET", f"/api/matches/{mid}")
        if sb["scoreboard"]["ended"]:
            break
        time.sleep(2)
    polls = sb["polls"]
    print(f"{fixture}:{seed} ended in {time.time() - t0:.0f}s: {len(polls)} polls, errors {sb['stats']['errors']}, mean vote {sum(p['participation'] for p in polls) / max(len(polls), 1):.1%}")
    call("POST", f"/api/matches/{mid}/control", {"op": "pause"})
