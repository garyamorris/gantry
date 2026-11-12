# Gantry: design and the decisions behind it

## 1. The idea in one paragraph

A live stream has two firehoses: what happens (the feed) and what people say about it (the chat). A human producer
watches both, decides what matters, and does things about it: runs a poll, pins a comment, cuts a highlight. Gantry is
that producer. Flow 1 **ranks and curates**. Flow 2 **generates actions**. A ledger and a memory make it safe to leave
running. Everything else in the repo exists to make those three things real, testable and observable.

## 2. Ground rules

1. **Offline and deterministic by default.** `GANTRY_PROVIDER=offline`. No network, no keys, no wall clock in any graph.
   Everything random is a hash of `(seed, labels)`, so a match replays bit for bit.
2. **The engine proposes and enforces; models write and judge.** Scores, policy and timing are pure code
   (`gantry.engine`). A model improves wording and re-ranks a shortlist. Its output is validated, and the engine's draft
   is the fallback. A dead provider degrades the prose, never the broadcast.
3. **The graph decides; the runtime applies.** Graphs never touch the session. They emit `action.publish`; the session
   makes it real. Graphs stay pure and replayable, and the runtime keeps the last word on timing.
4. **State is plain JSON.** A window is the graph's whole input. No clients, clocks or handles in state.
5. **Nothing above an `interrupt()` has a non-idempotent effect.** A resumed node re-runs from line 1. Stream events
   carry deterministic keys; Store writes are keyed puts.
6. **No blocking calls on the event loop.** Content lives in Python modules, so there is no file IO at runtime at all.
7. **Ground truth stays in the simulator.** `public()` strips it. Only the evals import it.

## 3. Why these graphs

`showrunner` is the loop, and `curator`, `action_desk` and `poll_studio` are its three nested subgraphs. They are also
exported on their own because each is a sensible unit to open in Studio, test and evaluate. `memory_keeper` and
`recap_press` are triggered by different events (a poll closing, a whistle) and so are separate runs on separate
threads. `producer_chat` is the conversational way into the ledger.

**One thread per window.** The obvious design is one thread per match. It is wrong for a live system: an `interrupt()`
would park the match's only thread on a human, and the next goal would either wait or wipe the pending approval. With a
thread per window the broadcast never waits, and continuity lives where it belongs, in the Store.

**One interrupt per action.** A window can produce a poll, a highlight and a pin. Bundling them into one approval means
the poll waits while someone thinks about the pin. `action_desk` fans out with `Send`, so each action has its own
interrupt, is answered by id in any order, and publishes the moment it is approved.

## 4. Flow 1: a two-stage ranker

Stage one runs on everything and is cheap, deterministic and explainable.

* **Moments.** A base value per event type, then context: how late, what it did to the score (opener, equaliser, goes
  ahead, completes a comeback), a brace or hat-trick, whether it resolves a story the chat was waiting on (a VAR check, a
  penalty), and how hard the chat spiked. Every score is the sum of labelled reasons; the Rundown shows them.
* **Comments.** Relevance to the moment (the players in it count for more than the clubs), substance (length, reasoning,
  numbers, a real question), engagement, novelty, minus spam and abuse. Labels: `highlight`, `keep`, `collapse`
  (a repeat), `hide`.
* **Themes.** What the non-hidden chat is talking about, with the best question on each.

Stage two is the **editor**: a model sees only the shortlist of eight and picks up to three highlights and at most one
pin. It can reorder the best of the chat. It cannot surface anything stage one hid, because ids outside the shortlist are
discarded.

Curation shows up in the product as the chat's **Top chat** view (held and repeated messages gone, highlights marked)
against **Live chat** (everything, with held messages dimmed and labelled).

## 5. Flow 2: candidates, then policy

A poll candidate is a **template applied to a moment**: a topic key (`red:e033`, `winner:1-1:0`, `potm:first_half`), the
facts a writer may use, and baseline wording in three voices. Sixteen templates cover goals, decisions, red cards,
penalties, saves, misses, substitutions, half-time, full-time, lulls, and arguments in the chat.

Candidates are ranked by `0.45 x priority + 0.40 x expected + 0.15 x freshness - fatigue`. `priority` is the moment's
score times how well the template fits. `expected` is what memory says this audience does with this kind of poll.
Anything under 0.52 is not worth interrupting the chat for, and the ledger records that it was considered.

`policy.screen` then returns **publish**, **ask** or **suppress**, always with a reason:

| rule | why |
|---|---|
| the moment has passed | a late poll is worse than none |
| already done this match (topic key), or too similar to an earlier poll (Store similarity) | never repeat |
| a human rejected this topic | they said no |
| a poll is live, or one closed under 240 s ago (75 s for a big moment) | one at a time, with room to breathe |
| a big moment may close a small live poll early | a red card does not wait for "rate this half" |
| too many of one template | variety |
| a player is being treated | not the moment for a fun poll |
| mode and confidence | Manual asks for everything; Assisted asks for polls; Autopilot publishes above 0.58 (0.72 for medium risk) |

Timing-based refusals carry a `retry_vt`. The session arms a window for then, recent key moments are carried into it,
and the poll that lost its slot gets another chance while it is still fresh.

## 6. Memory

| namespace | what | used by |
|---|---|---|
| `gantry/ledger/<match>` | every action: published, pending, rejected, expired, suppressed, dropped, with the reason; polls indexed on their text | `action_desk.recall`, the policy, the dashboard, `producer_chat`, `recap_press` |
| `gantry/polls/<match>` | how each poll actually did | `recap_press` |
| `gantry/prefs` | per template, across matches: polls run, mean participation, approved / edited / rejected | candidate ranking |
| `gantry/lessons` | short written lessons, indexed for semantic recall | shown to the writers and in the dashboard |

Human decisions are memory too: a kind of poll that is rejected more than it is accepted is ranked down.

## 7. The simulated world

* **The feed** (`sim.match`): three fixtures, each a scripted story plus seeded filler. The derby has a VAR heartbreak, a
  red card, a saved penalty and a 93rd-minute winner. The stalemate is long quiet stretches (what does a producer do when
  nothing happens?). The goalfest has seven goals and a hat-trick (poll fatigue).
* **The chat** (`sim.chat`): about 900 messages a match from 42 personas. Bursts after every big moment. Written to be
  hard: spam that talks about football, criticism that is harsh but not abusive, abuse that collects likes.
* **The audience** (`sim.audience`): how many viewers vote depends on the kind of question (a hidden appeal per
  template), how soon after the moment the poll opened, how recently the last one closed, how often that template has
  run, and how readable it is. Nothing in the engine imports this module. The producer learns it by watching polls close.

## 8. What I would do next

A feed-only mode with no simulator underneath. A real chat source. Sessions that survive a restart (the clock and
cursors in the Store). LLM scoring of comment shards behind the same `Send` (the map step is already there). Server
crons for the lull tick instead of the in-process clock, for a multi-worker deployment.
