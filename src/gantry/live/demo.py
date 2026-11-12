"""A whole match in the terminal, no server: `python -m gantry.live.demo --fixture derby --mode autopilot`.

The same Session and the same graphs as the live app, run headless on a LocalRunner. Prints what the producer did
and why, then what it learned. Add `--approve` to play a human in manual/assisted mode (approves every suggestion).
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any


async def play(fixture: str, mode: str, seed: int, approve: bool, runner: Any = None, quiet: bool = False) -> dict[str, Any]:
    from gantry.live.runner import LocalRunner
    from gantry.live.session import Session
    from gantry.memory.ledger import ledger_list, lessons_search, prefs_all

    runner = runner or LocalRunner()
    s = Session(runner, fixture_id=fixture, seed=seed, mode=mode)
    s.paused = False
    shown = 0
    while not s.ended:
        await s.step(20)
        if approve:
            for sug in list(s.suggestions.values()):
                if not sug.get("deciding") and not (sug["action"]["kind"] == "poll" and s.poll):
                    await s.decide(sug["id"], "approve")
            await s.drain()
        for a in s.actions[shown:]:
            if not quiet and a["status"] in ("published", "dropped", "rejected", "expired"):
                print(f"  {a['minute']:>6}  {a['status']:<9} {a['kind']:<12} {a.get('text') or ''}  [{a.get('by') or a.get('reason') or ''}]")
        shown = len(s.actions)
    st = s.state()
    fx = s.match["fixture"]
    ledger = await ledger_list(runner.store, s.match_id)
    prefs = await prefs_all(runner.store)
    lessons = await lessons_search(runner.store, None, limit=20)
    out = {"session": s, "ledger": ledger, "prefs": prefs, "lessons": lessons, "polls": s.polls}
    if not quiet:
        print(f"\n{fx['home']['name']} {st['score']['home']}-{st['score']['away']} {fx['away']['name']}   windows {s.stats['windows']} (skipped {s.stats['skipped']}), errors {s.stats['errors']}")
        print(f"published {s.stats['published']}, suppressed {s.stats['suppressed']}, expired {s.stats['expired']}, dropped {s.stats['dropped']}")
        for p in s.polls:
            print(f"  poll {p['minute']:>6}  {p['participation']:.1%}  {p['question']}  ->  {p['options'][max(range(len(p['votes'])), key=lambda i: p['votes'][i])]}")
        for x in lessons:
            print(f"  lesson: {x['text']}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixture", default="derby")
    ap.add_argument("--mode", default="autopilot", choices=["manual", "assisted", "autopilot"])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--approve", action="store_true", help="play the human: approve every suggestion")
    args = ap.parse_args()
    os.environ.setdefault("GANTRY_OFFLINE_TOKEN_DELAY_S", "0")
    asyncio.run(play(args.fixture, args.mode, args.seed, args.approve))


if __name__ == "__main__":
    main()
