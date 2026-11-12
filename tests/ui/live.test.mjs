// The client's model of a broadcast is pure, so it is tested here without a browser: `node --test tests/ui/*.test.mjs`.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { apply, ballSpot, chatCounts, createState, meanParticipation, pollRows, realSecondsUntil, vtNow } from '../../src/gantry/server/static/js/core/live.js';
import { parseSSE } from '../../src/gantry/server/static/js/core/sse.js';

const snap = () => ({
  match_id: 'm1', fixture: { id: 'derby', home: { name: 'H' }, away: { name: 'A' } }, timing: { end_vt: 6000 }, seq: 10,
  scoreboard: { vt: 100, speed: 10, paused: false, ended: false, minute: "2'", score: { home: 0, away: 0 }, mode: 'manual' },
  feed: [], markers: [], chat: [{ id: 'c1', text: 'spam', curation: { label: 'hide', reason: 'spam' } }, { id: 'c2', text: 'hello' }], poll: null, polls: [], pinned: null,
  highlights: [], suggestions: [{ id: 's1', action: { kind: 'poll' } }], actions: [], rundowns: [], lessons: [], stats: {},
});

test('a snapshot becomes state, with chat labels lifted out', () => {
  const S = createState(snap());
  assert.equal(S.matchId, 'm1');
  assert.deepEqual(S.labels, { c1: { label: 'hide', reason: 'spam' } });
  assert.equal(S.suggestions.get('s1').action.kind, 'poll');
  assert.equal(S.lastSeq, 10);
});

test('stream messages fold in and report what changed', () => {
  const S = createState(snap());
  assert.deepEqual(apply(S, 'feed', { id: 'e1', type: 'goal', vt: 120, minute: "2'", team: 'home', text: 'GOAL' }), ['feed']);
  assert.equal(S.markers.length, 1, 'a goal is a timeline marker');
  apply(S, 'feed', { id: 'e2', type: 'corner', vt: 130 });
  assert.equal(S.markers.length, 1, 'a corner is not');
  assert.deepEqual(apply(S, 'chat', { id: 'c3', text: 'x', curation: { label: 'collapse' } }), ['chat']);
  assert.equal(S.labels.c3.label, 'collapse');
  assert.deepEqual(apply(S, 'curated', { labels: { c2: { label: 'highlight' } } }), ['labels']);
  assert.deepEqual(chatCounts(S), { held: 1, repeats: 1, highlights: 1, total: 3 });
  apply(S, 'suggestion', { op: 'add', suggestion: { id: 's2', action: { kind: 'pin_comment' } } });
  apply(S, 'suggestion', { op: 'remove', id: 's1' });
  assert.deepEqual([...S.suggestions.keys()], ['s2']);
  assert.deepEqual(apply(S, 'nonsense', {}), []);
});

test('a poll opens, updates in place, closes once', () => {
  const S = createState(snap());
  const open = { id: 'p1', status: 'open', options: ['a', 'b'], votes: [0, 0], total: 0 };
  apply(S, 'poll', open);
  apply(S, 'poll', { ...open, votes: [30, 10], total: 40 });
  assert.equal(S.poll.total, 40);
  assert.deepEqual(pollRows(S.poll).map((r) => [r.pct, r.top]), [[75, true], [25, false]]);
  const closed = { ...open, status: 'closed', votes: [60, 40], total: 100, participation: 0.2 };
  apply(S, 'poll', closed);
  apply(S, 'poll', closed);
  assert.equal(S.poll, null);
  assert.equal(S.polls.length, 1);
  assert.equal(S.lastClosed.id, 'p1');
  assert.equal(Math.abs(meanParticipation([...S.polls, { participation: 0.4 }]) - 0.3) < 1e-9, true);
  assert.equal(meanParticipation([]), null);
  assert.deepEqual(pollRows({ options: ['a'], votes: [0], total: 0 })[0], { label: 'a', votes: 0, pct: 0, top: false });
});

test('the clock is extrapolated between scoreboard ticks, and stops when paused', () => {
  const S = createState(snap());
  apply(S, 'state', { ...S.sb, vt: 200 }, 1000);
  assert.equal(vtNow(S, 1500), 205, 'half a second at 10x');
  assert.equal(vtNow(S, 60000), 220, 'never runs more than two seconds ahead of the last tick');
  assert.equal(realSecondsUntil(305, S, 1500), 10);
  apply(S, 'control', { op: 'pause', scoreboard: { ...S.sb, paused: true } }, 2000);
  assert.equal(vtNow(S, 9000), 200);
  assert.equal(realSecondsUntil(305, S, 9000), null);
});

test('events land on the right part of the pitch (home attacks right)', () => {
  assert.equal(ballSpot({ type: 'goal', team: 'home' }).x > 140, true);
  assert.equal(ballSpot({ type: 'goal', team: 'away' }).x < 20, true);
  assert.equal(ballSpot({ type: 'penalty.saved', team: 'home' }).x < 20, true, "the keeper's own goal");
  assert.deepEqual(ballSpot({ type: 'match.kickoff', team: null }), { x: 80, y: 45 });
  const spot = ballSpot({ type: 'foul', team: 'home', seq: 4 });
  assert.equal(spot.x > 60 && spot.x < 110 && spot.y > 20 && spot.y < 70, true);
});

test("the server's SSE frames parse (ids, event names, heartbeats)", () => {
  const text = 'event: hello\ndata: {"seq":3}\n\n: heartbeat\n\nid: 4\nevent: poll\ndata: {"q":1}\n\n';
  const evs = parseSSE(text);
  assert.deepEqual(evs.map((e) => [e.event, e.id]), [['hello', undefined], ['poll', '4']]);
  assert.equal(JSON.parse(evs[1].data).q, 1);
});
