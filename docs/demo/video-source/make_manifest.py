#!/usr/bin/env python3
"""make_manifest.py: write manifest.json for build_video2.py from a declarative per-scene spec.

Scene lengths come from the narration (narration/durations.json). Callouts land on the moment a PHRASE is spoken (word times from
narration/words.json, the word-level transcript of the actual audio). Footage ranges are cut at the recorder's MARKS (raw/*.markers.json,
written by the directors), so a re-recorded take cannot drift out of sync with the spec.

    python3 make_manifest.py [out=manifest.json]
Footage: raw/hero.mp4 (autopilot, real dashboard), raw/manual.mp4 (manual mode), raw/memory.mp4 (the Memory tab), viz_out/*.mp4 (animated visuals
driven by the recorded runs; rendered by record_viz.py with fit = scene length).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DUR = json.load(open(os.path.join(HERE, "narration/durations.json")))
WORDS = json.load(open(os.path.join(HERE, "narration/words.json")))
PB, PA = 0.35, 0.45  # pad before / after narration per scene


def marks(path: str) -> dict[str, float]:
    return {m["name"]: m["t"] for m in json.load(open(os.path.join(HERE, path + ".markers.json")))}


HM, MM, YM, PM = marks("raw/hero.mp4"), marks("raw/manual.mp4"), marks("raw/memory.mp4"), marks("raw/policy.mp4")
HERO, MAN, MEM, POL = "raw/hero.mp4", "raw/manual.mp4", "raw/memory.mp4", "raw/policy.mp4"


def norm(s: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", s.lower())


def phrase_time(sid: str, phrase: str, nth: int = 0) -> float:
    """Seconds from the start of the NARRATION where `phrase` begins (matched on the transcribed words); falls back to 0."""
    ws = WORDS[sid]
    toks = [(norm(w["word"]) or [""])[0] for w in ws]
    want = norm(phrase)
    hits = [i for i in range(len(toks) - len(want) + 1) if toks[i : i + len(want)] == want]
    if len(hits) <= nth:
        print(f"WARN: phrase {phrase!r} not found in scene {sid}", file=sys.stderr)
        return 0.0
    return float(ws[hits[nth]]["start"])


def V(path, i=None, o=None, speed="fit", **kw):
    d = {"type": "video", "path": path, "speed": speed}
    if i is not None:
        d["in"] = round(i, 2)
    if o is not None:
        d["out"] = round(o, 2)
    d.update(kw)
    return d


def FREEZE(path, t, move="zoom-in", weight=1.0, **kw):
    return {"type": "video", "path": path, "in": round(t, 2), "freeze": True, "move": move, "speed": "fit", "weight": weight, **kw}


def callouts(sid: str, items):
    out = []
    for phrase, text, dur, *rest in items:
        nth = rest[0] if rest else 0
        out.append({"at": round(PB + phrase_time(sid, phrase, nth) - 0.15, 2), "dur": dur, "text": text, "style": "tech", "anchor": "tr"})
    return out


def clip_dur(path: str) -> float:
    out = subprocess.run([os.path.join(HERE, "rec/vtool"), "info", os.path.join(HERE, path)], capture_output=True, text=True).stdout
    m = re.search(r"duration ([\d.]+) s", out)
    return float(m.group(1)) if m else 20.0


# id, segments, callouts
SCENES = [
    ("A", [V(HERO, HM["start"] + 0.4, HM["poll12_closed"], speed="fit")],
     [("Gantry", "Gantry · an autonomous producer", 4.5), ("offline model", "offline model · no keys, no network", 4.5), ("a poll that nobody typed", "a poll nobody typed", 4.0)]),
    ("B", [V("viz_out/overview.mp4", 0, None)],
     [("a window", "window · one stretch, as JSON", 4.0), ("The showrunner", "showrunner · two subgraphs", 3.5), ("The curator", "curator · rank and curate", 3.5), ("The action desk", "action_desk · generate actions", 4.0), ("Store", "LangGraph Store · memory", 4.0)]),
    ("C", [V(HERO, HM["pipeline_open"], HM["inject_poll_closed"] - 2.0, speed="fit")],
     [("driven by events", "event-driven, not a timer", 4.0), ("A penalty is urgent", "urgent · fires in seconds", 4.0), ("sheds load", "one window in flight · sheds load", 4.5)]),
    ("D", [FREEZE("viz_out/replay_showrunner.mp4", 0.2, "none", weight=5.5), V("viz_out/replay_showrunner.mp4", 0, None, weight=15.0)],
     [("Triage", "triage · a cheap gate", 3.5), ("curator runs", "curate · the curator subgraph", 3.5), ("action desk", "act · the action desk subgraph", 3.5), ("poll studio", "a subgraph inside a subgraph", 4.0), ("one thread per window", "one thread per window", 4.0)]),
    ("E", [V(HERO, HM["rundown63"] - 3.5, HM["rundown63"] + 3.5, weight=7.0), FREEZE("viz_out/replay_curator.mp4", 0.2, "none", weight=2.0), V("viz_out/replay_curator.mp4", 0, None, weight=16.0),
           V(HERO, HM["live_chat"] - 1.0, HM["live_chat"] + 3.4, weight=4.4)],
     [("into shards", "Send · shards of 40", 4.0), ("two shards", "2 shards → merge (defer)", 4.0), ("an editor", "editor · re-ranks the shortlist", 4.5)]),
    ("F", [V(POL, PM["red_card"] - 0.8, PM["red_card"] + 5.7, weight=6.5), V("viz_out/replay_studio.mp4", 0, None, weight=14.0), V(POL, PM["red_card"] + 5.7, PM["red_card"] + 9.4, weight=5.4)],
     [("Three writers", "Send ×3 · three writers", 4.0), ("A judge", "judge · a rubric", 3.5), ("Failures go back", "revise loop · notes", 4.0), ("nonsense", "validated · engine fallback", 4.0)]),
    ("G", [V(POL, 30.0, 46.0), V(POL, PM["ledger"] - 0.3, PM["ledger"] + 4.6)],
     [("publish, ask a human", "publish · ask · hold", 4.0), ("One poll at a time", "policy · the rules", 4.0), ("every refusal", "ledger · every refusal has a reason", 4.5)]),
    ("H", [V(MAN, MM["inbox_cards"] + 1.0, MM["inbox_cards"] + 5.2), V("viz_out/replay_resume.mp4", 0, 5.3), V(MAN, MM["edit_open"] - 0.5, MM["edit_published"] + 1.5),
           V(MAN, MM["rejected"] - 0.8, MM["rejected"] + 3.2), V("viz_out/replay_resume.mp4", 5.3, None)],
     [("its own interrupt", "interrupt() · one per action", 4.0), ("by id", "resume by id", 3.5), ("from its checkpoint", "resume from the checkpoint", 4.0)]),
    ("I", [V(MEM, YM["memory"] - 0.4, YM["end"], speed="fit")],
     [("memory keeper", "memory_keeper · writes the Store", 4.0), ("against an average", "preferences · per kind of poll", 4.5), ("a lesson is written", "lessons · semantic search", 4.0)]),
    ("J", [V("viz_out/evals.mp4", 0, None)], []),
    ("K", [V(HERO, 165.0, 181.5, weight=16.0), FREEZE(HERO, HM["end_stage"], "zoom-out", weight=6.0)], []),
]


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "manifest.json")
    scenes = [{"id": sid, "segments": segs, "callouts": callouts(sid, cos), "pad_before": PB, "pad_after": PA} for sid, segs, cos in SCENES]
    m = {
        "width": 1920, "height": 1080, "fps": 30, "narration_dir": "narration/audio",
        "preset": "Match Source – Adaptive Low Bitrate", "max_mb": 90, "audio_kbps": 128,
        "small": {"width": 1280, "height": 720, "max_mb": 40, "audio_kbps": 96},
        "xfade": 0.3, "narration_db": -3.0, "captions": True, "caption_style": {"font": "Inter", "size": 34, "background": "#000000b0", "margin": 0.05},
        "title": {"text": "Gantry", "sub": "an autonomous producer for a live stream", "seconds": 3.5,
                  "bg": {"type": "video", "path": HERO, "in": round(HM["goal12"] + 0.6, 2), "freeze": True}, "dim": -90},
        "outro": {"text": "Run it yourself", "sub": "make dev  ·  localhost:2030/app  ·  LangGraph Studio", "seconds": 3.5,
                  "bg": {"type": "video", "path": HERO, "in": round(HM["end_stage"], 2), "freeze": True}, "dim": -90},
        "scenes": scenes,
    }
    json.dump(m, open(out, "w"), indent=1, ensure_ascii=False)
    total = sum(DUR[s[0]] + PB + PA for s in SCENES)
    print(f"wrote {out}: {len(scenes)} scenes, ~{total + 7:.0f}s incl. cards")


if __name__ == "__main__":
    main()
