#!/usr/bin/env python3
"""snapshot_memory.py: write what the producer knows right now (preferences and lessons) -> captures/real/memory_after.json.

    python3 snapshot_memory.py [--base http://127.0.0.1:2030]

make_script.py reads this, so the narration quotes the numbers the recorded memory screen actually shows. Standard library only.
"""
import argparse
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:2030")
a = ap.parse_args()
mem = json.load(urllib.request.urlopen(a.base + "/api/memory", timeout=30))
out = {"prefs": mem["prefs"], "lessons": [{"id": x["id"], "text": x["text"]} for x in mem["lessons"]]}
json.dump(out, open(os.path.join(HERE, "captures/real/memory_after.json"), "w"), indent=1)
for p in sorted((p for p in out["prefs"] if p["n"]), key=lambda p: -p["mean"]):
    print(f"  {p['template']:18s} n={p['n']} mean={p['mean']:.3f} approved={p['approved']} rejected={p['rejected']}")
for l in out["lessons"]:
    print("  lesson:", l["text"])
print("->", os.path.join(HERE, "captures/real/memory_after.json"))
