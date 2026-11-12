#!/usr/bin/env python3
"""record_viz.py: render the animated visuals (viz/*.html, driven by the recorded runs in captures/real) to viz_out/*.mp4 with rec.

    python3 record_viz.py [--only overview,replay_curator] [--list]

Each clip is rendered in real time at 1920x1080/30fps by the offscreen WebKit recorder; `fit` warps the animation to the length the
narration needs (scene length = narration + pads, from narration/durations.json), so the motion is natural instead of retimed afterwards.
Serves this folder on a private port for the duration of the run. Thread ids come from captures/real/index.json (full ids: they are UUIDv7,
so their first characters collide).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DUR = json.load(open(os.path.join(HERE, "narration/durations.json"))) if os.path.exists(os.path.join(HERE, "narration/durations.json")) else {}
IDX = json.load(open(os.path.join(HERE, "captures/real/index.json")))
PORT = 8772
CAP = "../captures/real"
SHOW63, CUR, STUDIO = IDX["showrunner-63"]["id"], IDX["curator-winner"]["id"], IDX["poll-penalty"]["id"]


def f(scene: str, share: float = 1.0, extra: float = 0.6, default: float = 20.0) -> float:
    return round(DUR.get(scene, default) * share + extra, 1)


# name -> (page, params, fit seconds)
# Fits are chosen so each animation's key beats land on the spoken phrases (see make_manifest.py for the lead holds in front of them).
CLIPS = {
    "overview": ("overview.html", {}, f("B", 1.0, 0.8, 26.3)),
    "replay_showrunner": ("replay.html", {"graph": "showrunner", "thread": SHOW63, "run": "0"}, 15.4),
    "replay_curator": ("replay.html", {"graph": "curator", "thread": CUR}, 16.0),
    "replay_studio": ("replay.html", {"graph": "poll_studio", "thread": STUDIO}, 14.0),
    "replay_resume": ("replay.html", {"graph": "showrunner", "thread": SHOW63, "run": "all", "from": 1.45, "to": 4.4}, 11.0),  # the interrupt, then the two resumes by id
    "evals": ("evals.html", {}, f("J", 1.0, 0.8, 34.9)),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    names = a.only.split(",") if a.only else list(CLIPS)
    if a.list:
        for n in names:
            print(n, CLIPS[n])
        return 0
    os.makedirs(os.path.join(HERE, "viz_out"), exist_ok=True)
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1", "--directory", HERE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    try:
        for n in names:
            page, params, fit = CLIPS[n]
            q = "&".join([f"{k}={v}" for k, v in {**({"cap": CAP} if page == "replay.html" else {}), **params, "fit": fit}.items()])
            url = f"http://127.0.0.1:{PORT}/viz/{page}?{q}"
            out = os.path.join(HERE, "viz_out", f"{n}.mp4")
            print(f"== {n}: fit {fit}s\n   {url}")
            r = subprocess.run([os.path.join(HERE, "rec/rec"), "--url", url, "--out", out, "--director", os.path.join(HERE, "viz/director_wait.js"), "--tail", "0.4", "--max-seconds", str(int(fit + 60))],
                               capture_output=True, text=True)
            tail = [l for l in r.stderr.splitlines() if "console.error" in l or "WARN" in l or "done:" in l][:6]
            print("   " + "\n   ".join(t[:200] for t in tail))
    finally:
        srv.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
