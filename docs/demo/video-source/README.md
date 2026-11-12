# How the Gantry walkthrough video was made

`../gantry-walkthrough.mp4` (1080p) and `-720p.mp4` (messaging copy), with `gantry-walkthrough.srt/.vtt` (captions, also burned in). The `.mp4`s are
git-ignored (they are attached to the repo's GitHub Release); the rebuild below writes them to `docs/demo/`.
Everything on screen comes from **recorded runs of the real app**: the real dashboard against a real `langgraph dev`, and replays of real
Agent Server runs. Nothing is mocked. **The model is the offline one** (`GANTRY_PROVIDER=offline`: deterministic, no keys): the Claude and
OpenAI paths are written but were not run for this video, and the narration says so.

| Stage | What | Where |
|---|---|---|
| 1. Recording server | clean `make dev` on :2030 (a fresh process, so no old matches share the Store or slow the clock) | |
| 2. Capture | runs real windows through the Agent Server with every stream mode (`tasks`, `updates`, `custom`, `messages`, `checkpoints`, subgraph namespaces kept) and the real `xray` graph structure | `capture.py` -> `captures/real/` |
| 3. Dashboard footage | an offscreen WKWebView recorder (`rec/rec`, built from `rec/rec.swift`) drives the real UI like a person (a fake cursor clicks the real controls): `director_hero.js` (autopilot: goal, poll, a pushed penalty, the red card, the rundown, Top vs Live chat, the ledger, full time), `director_manual.js` (manual mode: the Inbox, an edited poll, a rejected highlight), `director_policy.js` (the Pipeline trace's `SUPPRESS` lines and the Ledger's "held back, and why", with memory warm), `director_memory.js` (the Memory tab after `prewarm.py` played three other matches) | `raw/` |
| 4. Animated visuals | `viz/replay.html` replays the recorded runs on the real graph structure (nodes, `Send` fan-out, nested subgraphs, interrupt and resume by id, the custom-event ticker); `viz/overview.html` is the loop as one diagram; `viz/evals.html` shows the offline eval gate, with numbers measured by `make_evals_data.py` | `viz/` -> `viz_out/` via `record_viz.py` |
| 5. Narration | the script is generated from recorded data (`narration/make_script.py` reads `captures/real/facts.json`, `viz/evals_data.json` and `captures/real/memory_after.json` from `snapshot_memory.py`), so every number spoken was measured. OpenAI `gpt-4o-mini-tts`, voice `marin` | `narration/` |
| 6. Transcription | every narration clip is transcribed back with word timestamps (`whisper-1`); caption text comes from the script, times from the transcript; on-screen labels are placed at the moment their phrase is spoken | `narration/captions.py`, `make_manifest.py` |
| 7. Assembly | FilmCraft driven through its control server: timeline, callouts, caption track burned in, bitrate-fitted export | `build_video2.py` |

Take notes: recording against a server that still has old matches playing shares one Store and slows every clock, so restart `make dev` first and pause any stray match
(the Browser pane's own tab starts one). The page scrolls back to the top when the recorder clicks a button, so directors re-scroll after each click.

Changes made to the Foghorn replay page for Gantry: nested subgraphs report the same `interrupt` at every level, so it is now counted once (by interrupt id),
and the interrupt card says what is being asked (a poll, a highlight) instead of only a node name.

Rebuild (the recordings in `raw/` and `viz_out/` are not committed: they are large):

```bash
make dev                                              # in the repo root; a fresh server
uv run python docs/demo/video-source/capture.py       # real runs + graph structure -> captures/real
uv run python docs/demo/video-source/make_evals_data.py
cd docs/demo/video-source
swiftc -O -swift-version 5 -suppress-warnings rec/rec.swift -o rec/rec && swiftc -O -swift-version 5 -suppress-warnings rec/vtool.swift -o rec/vtool
rec/rec --url http://127.0.0.1:2030/app/ --css 1600x900 --out raw/hero.mp4   --director rec/director_hero.js   --max-seconds 540 --tail 2
rec/rec --url http://127.0.0.1:2030/app/ --css 1600x900 --out raw/manual.mp4 --director rec/director_manual.js --max-seconds 140 --tail 2
python3 prewarm.py                                    # three more matches, so memory has history
rec/rec --url http://127.0.0.1:2030/app/ --css 1600x900 --out raw/policy.mp4 --director rec/director_policy.js --max-seconds 200 --tail 2
rec/rec --url http://127.0.0.1:2030/app/ --css 1600x900 --out raw/memory.mp4 --director rec/director_memory.js --max-seconds 60 --tail 1.5
python3 snapshot_memory.py && python3 narration/make_script.py
OPENAI_API_KEY=... python3 narration/tts.py all && python3 narration/captions.py words && python3 narration/captions.py cues   # about 10 cents
python3 record_viz.py                                 # animated visuals fitted to the narration
python3 make_manifest.py
FILMCRAFT_CONTROL_PORT=7979 python3 build_video2.py manifest.json ../gantry-walkthrough.mp4 --small --joins
python3 verify_mp4.py ../gantry-walkthrough.mp4 --joins --sync
```
