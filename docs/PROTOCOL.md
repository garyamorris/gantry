# Gantry protocol: inputs, REST, the stream, custom events, the ledger

## 1. The window (the `showrunner` input)

Built by `gantry.live.windows.build_window`. Self-contained and public: no ground truth.

```jsonc
{
  "match_id": "derby-7-a1b2c3",
  "mode": "manual | assisted | autopilot",
  "window": {
    "id": "w012", "seq": 12, "from_vt": 3930, "to_vt": 4075,
    "trigger": {"kind": "event | urgent | phase | tick | retry | manual", "event_id": "e033", "reason": "card.red", "lag": 0},
    "events":   [ /* feed events since the last window, plus key moments from the last 200 s */ ],
    "comments": [ /* up to 160 chat messages, likes as of to_vt */ ],
    "timeline": [ /* the match's key events so far: the context a moment is scored against */ ],
    "chat": {"count": 71, "per_min": 29.4, "baseline_per_min": 9.8}
  },
  "match_state": {
    "phase": "second_half", "minute": "63'", "clock": 3750, "vt": 4075, "end_vt": 6000,
    "score": {"home": 1, "away": 1}, "stats": {...}, "scorers": [...], "sent_off": [...],
    "on_pitch": {"home": [...], "away": [...]}, "bench": {...}, "top_rated": [...],
    "fixture": {"id": "derby", "home": {"name": "Harbour City", ...}, "away": {...}},
    "viewers": 16891,
    "live": {"active_poll": {"id", "closes_vt", "opened_vt", "priority"} | null, "pinned": {...} | null, "last_poll_closed_vt": 3960 | null}
  }
}
```

A feed event: `{id, seq, type, vt, clock, period, minute, team, player, player2, text, score, tags, urgent}`.
A chat message: `{id, vt, author: {id, name, badge, color}, text, likes}`.
`vt` is virtual seconds on the broadcast timeline (kick-off = 0, includes the half-time break).

Feed types: `goal`, `goal.disallowed`, `card.red`, `card.yellow`, `penalty.awarded`, `penalty.saved`, `penalty.missed`,
`var.check`, `var.no_penalty`, `shot.woodwork`, `shot.saved`, `shot.off_target`, `save.big`, `chance.big`, `injury`,
`substitution`, `corner`, `foul`, `offside`, `stat.update`, `match.kickoff`, `match.halftime`, `match.second_half`,
`match.fulltime`.

Other graph inputs: `curator` takes the same window; `action_desk` takes it plus `rundown`; `poll_studio` takes
`{poll_brief}`; `memory_keeper` takes `{match_id, mode, poll_result}`; `recap_press` takes
`{match_id, kind, match_state, timeline}`; `producer_chat` takes `{messages}` with context `{match_id}`. Every one of
them fills in a sample when sent `{}`.

## 2. REST (`/api`)

| method and path | body | returns |
|---|---|---|
| `GET /api/boot` | | version, provider, models, model call stats, graphs with assistant ids, fixtures, modes, speeds |
| `POST /api/matches` | `{fixture, seed, mode, speed, autostart}` | a snapshot of the new match |
| `GET /api/matches/{id}` | | snapshot: scoreboard, recent feed and chat (with curation labels), poll, suggestions, actions, rundowns, stats, `seq` |
| `GET /api/matches/{id}/stream` | header `Last-Event-ID` or `?since=` | the event stream (section 3) |
| `POST /api/matches/{id}/control` | `{op: play / pause / speed / mode / jump, value}` | the scoreboard |
| `POST /api/matches/{id}/trigger` | | fires a window now |
| `POST /api/matches/{id}/decide` | `{id, decision: approve / edit / reject, question?, options?, text?, note?}` | `{ok}`; 404 gone, 409 a poll is live |
| `POST /api/matches/{id}/vote` | `{poll_id, option}` | the poll |
| `POST /api/matches/{id}/chat` | `{name, text}` | the message, screened |
| `POST /api/matches/{id}/events` | `{type, team?, player?, text?, urgent?, tags?}`, header `Idempotency-Key` | 202 accepted, 200 duplicate, 422 unknown type |
| `GET /api/matches/{id}/ledger` | | every action decided this match, from the Store |
| `GET /api/matches/{id}/threads` | | recent windows as thread ids with Studio links |
| `GET /api/memory`, `POST /api/memory/reset` | | preferences and lessons |

Errors are `{"error": "..."}` with a matching status. Threads, runs, assistants and the Store are the built-in Agent
Server API at the same origin.

## 3. The stream (SSE)

Frame: `id: <seq>` (absent on transient messages), `event: <channel>`, `data: <json>`. A heartbeat comment every 15 s.

| channel | data | notes |
|---|---|---|
| `hello` | `{match_id, scoreboard, seq}` | first frame |
| `state` | the scoreboard | transient, twice a second: `vt, minute, phase, score, viewers, speed, paused, mode, agent, lag, pending` |
| `control` | `{op, value, scoreboard}` | a control was applied; `op: "ended"` when the broadcast is over |
| `feed` | a feed event | |
| `chat` | a chat message, with `curation` if it was held on arrival; `host` on channel messages | |
| `curated` | `{window_id, labels: {comment_id: {label, reason, by}}}` | `label`: `highlight`, `hide`, `collapse` |
| `rundown` | `{window_id, minute, note, heat, themes, moments, highlights, hidden, collapsed}` | flow 1's result for one window |
| `poll` | the poll: `{id, question, options, votes, total, status, opened_vt, closes_vt, duration_s, by, participation?, closed_early?}` | repeated as votes arrive; `status: "closed"` once |
| `pin` | `{comment, until_vt}` or `null` | |
| `highlight` | `{id, title, text, event_id, vt, minute}` | |
| `suggestion` | `{op: add / update, suggestion}` or `{op: remove, id}` | the Inbox |
| `action` | `{id, kind, topic, status, reason, text, by, edited, minute}` | `status`: `published`, `suppressed`, `rejected`, `expired`, `dropped` |
| `agent` | a custom stream event (section 4) plus `window_id, graph, vt, minute` | the live trace |
| `memory` | `{kind: memory.outcome / memory.pref / memory.lesson, ...}` | |
| `recap` | `{kind, scoreline, text, moments, desk}` | |
| `gap` | `{missed_from, missed_to}` or `{dropped}` | the client fell behind the ring buffer |

## 4. Custom stream events (`stream_mode="custom"`)

Envelope: `{"v": 1, "kind", "key", "agent", "data"}`. `key` is deterministic, so a consumer can drop what a resumed node
re-emits.

| kind | from | data |
|---|---|---|
| `window.open`, `window.skip`, `window.done` | showrunner | the window's id, trigger and counts; the skip reason; the summary |
| `moment.ranked` | curator | a scored moment: `rank, event_id, type, minute, text, score, tier, heat, why[]` |
| `chat.scored` | curator | `{shard, n, hidden}` per shard |
| `editor.pick` | curator | `{source: model / fallback / engine, highlight, pin, engine}` |
| `curation.result` | curator | `{window_id, highlight, pin, hidden, collapsed, themes, heat, note, headline}`. **The runtime labels the chat from this** |
| `memory.recalled` | action_desk | ledger counts, recent polls, top preferences, lessons |
| `action.proposed` | action_desk | a candidate: `kind, topic, priority`, and for polls `template, expected, value, viable` |
| `poll.brief`, `poll.draft`, `poll.judged`, `poll.final` | poll_studio | the candidate; a writer's draft per round; the judge's scores and notes; the winner |
| `action.screened` | action_desk | `{id, kind, topic, verdict, reason, confidence}` |
| `action.pending` | action_desk | `{action}`: waiting for a human |
| `action.publish` | action_desk | `{action, by, edited}`. **The runtime makes this real** |
| `action.closed` | action_desk | `{id, kind, topic, status, reason, retry_vt?}` |
| `memory.written` | action_desk | `{ns, key, status}` |
| `memory.outcome`, `memory.pref`, `memory.lesson` | memory_keeper | a poll's result; the template's new stats; a lesson |
| `recap.section` | recap_press | the recap |
| `progress`, `note` | any | `{label, pct}`; `{text}` |

## 5. Interrupts

| payload | resume with |
|---|---|
| `{"kind": "approve_action", "action": {...}, "choices": [...], "expires_vt"}` | `{"decision": "approve" / "reject" / "expire"}` or `{"decision": "edit", "question", "options"}` (polls) or `{"decision": "edit", "text"}` |
| `{"kind": "memory_write", "lesson": {...}, "choices": ["keep", "discard"]}` | `{"decision": "keep" / "discard"}` |

One interrupt per action. Resume by id: `Command(resume={interrupt_id: value})`. Anything unreadable is a rejection.

## 6. A ledger entry

```jsonc
{
  "id": "w012:poll:red:e033",        // suppressed entries use "skip:<topic>": one row per topic, the latest reason wins
  "match_id": "derby-7-a1b2c3", "window_id": "w012", "kind": "poll", "topic": "red:e033", "template": "red_card",
  "status": "published | pending | rejected | expired | suppressed | dropped",
  "by": "autopilot | human | policy | timeout", "reason": "autopilot: confidence 0.96, medium risk", "edited": false,
  "question": "DRAGOVIC SEES RED 🟥 DESERVED?", "options": ["🟥 Off he goes", "😤 Harsh", "🤷 Not even a foul"],
  "vt": 4075, "minute": "64'", "confidence": 0.96, "priority": 0.81, "risk": "medium",
  "participation": 0.2208, "votes": [1641, 1492, 596], "total": 3729, "winner": "🟥 Off he goes", "closed_vt": 4166,   // stamped when the poll closes
  "text": "DRAGOVIC SEES RED 🟥 DESERVED? 🟥 Off he goes 😤 Harsh 🤷 Not even a foul"                                   // what the semantic index sees
}
```
