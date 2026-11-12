// The client's model of one broadcast. PURE: no DOM, no network, so it runs under `node --test`.
//
// `createState(snapshot)` builds it from GET /api/matches/{id}; `apply(S, channel, data)` folds in one SSE message and
// returns the list of things that changed, so the views repaint only what they must.

export const KEY_TYPES = new Set(['goal', 'card.red', 'penalty.awarded', 'penalty.saved', 'penalty.missed', 'goal.disallowed', 'var.no_penalty', 'var.check',
  'shot.woodwork', 'save.big', 'chance.big', 'injury']);
const CAP = { feed: 200, chat: 400, actions: 300, rundowns: 30, trace: 220, polls: 40 };

function push(list, item, cap) {
  list.push(item);
  if (list.length > cap) list.splice(0, list.length - cap);
}

export function createState(snap) {
  const labels = {};
  for (const m of snap.chat) if (m.curation) labels[m.id] = m.curation;
  return {
    matchId: snap.match_id, fixture: snap.fixture, timing: snap.timing, sb: snap.scoreboard, sbAt: 0, feed: [...snap.feed], markers: [...snap.markers], chat: [...snap.chat],
    labels, poll: snap.poll, polls: [...snap.polls], lastClosed: null, pinned: snap.pinned, highlights: [...snap.highlights],
    suggestions: new Map(snap.suggestions.map((s) => [s.id, s])), actions: [...snap.actions], rundowns: [...snap.rundowns], lessons: [...snap.lessons],
    recaps: [...(snap.recaps || [])], stats: snap.stats, trace: [], lastSeq: snap.seq, ended: !!snap.scoreboard.ended, memoryVersion: 0,
  };
}

/** Fold one stream message into S. Returns the dirty tags. */
export function apply(S, channel, data, now = 0) {
  switch (channel) {
    case 'hello':
    case 'state':
      S.sb = channel === 'hello' ? data.scoreboard : data;
      S.sbAt = now;
      S.ended = !!S.sb.ended;
      return ['score'];
    case 'control':
      S.sb = data.scoreboard;
      S.sbAt = now;
      S.ended = !!S.sb.ended;
      return ['score', 'control'];
    case 'feed':
      push(S.feed, data, CAP.feed);
      if (KEY_TYPES.has(data.type)) S.markers.push({ id: data.id, vt: data.vt, type: data.type, minute: data.minute, team: data.team, text: data.text });
      return ['feed'];
    case 'chat':
      push(S.chat, data, CAP.chat);
      if (data.curation) S.labels[data.id] = data.curation;
      return ['chat'];
    case 'curated':
      Object.assign(S.labels, data.labels);
      return ['labels'];
    case 'poll':
      if (data.status === 'open') S.poll = data;
      else {
        if (S.poll && S.poll.id === data.id) S.poll = null;
        S.lastClosed = data;
        if (!S.polls.some((p) => p.id === data.id)) push(S.polls, data, CAP.polls);
      }
      return ['poll'];
    case 'pin':
      S.pinned = data;
      return ['pin'];
    case 'highlight':
      S.highlights.push(data);
      return ['highlight'];
    case 'suggestion':
      if (data.op === 'remove') S.suggestions.delete(data.id);
      else S.suggestions.set(data.suggestion.id, data.suggestion);
      return ['suggestions'];
    case 'action':
      push(S.actions, data, CAP.actions);
      return ['actions'];
    case 'rundown':
      push(S.rundowns, data, CAP.rundowns);
      return ['rundown'];
    case 'agent':
      push(S.trace, data, CAP.trace);
      return ['trace'];
    case 'memory':
      if (data.kind === 'memory.lesson') S.lessons.push(data);
      S.memoryVersion++;
      return ['memory'];
    case 'recap':
      S.recaps.push(data);
      return ['recap'];
    default:
      return [];
  }
}

/** Virtual time right now, extrapolated from the last scoreboard tick so clocks and bars move smoothly between ticks. */
export function vtNow(S, now) {
  const sb = S.sb;
  if (!sb || sb.paused || sb.ended || !S.sbAt) return sb ? sb.vt : 0;
  return sb.vt + Math.min(2, Math.max(0, (now - S.sbAt) / 1000)) * sb.speed;
}

export function pollRows(poll) {
  const total = poll.total || 0;
  const top = Math.max(...poll.votes);
  return poll.options.map((label, i) => ({ label, votes: poll.votes[i], pct: total ? Math.round((100 * poll.votes[i]) / total) : 0, top: total > 0 && poll.votes[i] === top }));
}

export function meanParticipation(polls) {
  const done = polls.filter((p) => typeof p.participation === 'number');
  return done.length ? done.reduce((a, p) => a + p.participation, 0) / done.length : null;
}

/** Real seconds until virtual time `vt` at the current speed (null while paused). */
export function realSecondsUntil(vt, S, now) {
  if (vt == null || !S.sb || S.sb.paused) return null;
  return Math.max(0, (vt - vtNow(S, now)) / S.sb.speed);
}

export function chatCounts(S) {
  let held = 0;
  let repeats = 0;
  let highlights = 0;
  for (const m of S.chat) {
    const l = S.labels[m.id] && S.labels[m.id].label;
    if (l === 'hide') held++;
    else if (l === 'collapse') repeats++;
    else if (l === 'highlight') highlights++;
  }
  return { held, repeats, highlights, total: S.chat.length };
}

/** Where on a 160x90 pitch an event happened. Home attacks right. Deterministic from the event. */
export function ballSpot(ev) {
  const home = ev.team === 'home';
  const wob = ((ev.seq || 0) * 37) % 30;
  const t = ev.type;
  if (t === 'goal') return { x: home ? 151 : 9, y: 45 };
  if (t === 'penalty.awarded') return { x: home ? 138 : 22, y: 45 };
  if (t === 'penalty.saved' || t === 'save.big') return { x: home ? 12 : 148, y: 45 }; // team = the keeper's
  if (t.startsWith('shot.') || t === 'chance.big' || t === 'goal.disallowed' || t.startsWith('var.')) return { x: home ? 132 : 28, y: 30 + wob };
  if (t === 'corner') return { x: home ? 153 : 7, y: wob > 15 ? 82 : 8 };
  if (t === 'offside') return { x: home ? 118 : 42, y: 25 + wob };
  if (t === 'foul' || t.startsWith('card.') || t === 'injury') return { x: 80 + (home ? 12 : -12) + (wob - 15) / 2, y: 28 + wob };
  return { x: 80, y: 45 };
}

export const FLASH = {
  goal: 'GOAL', 'card.red': 'RED CARD', 'penalty.awarded': 'PENALTY', 'goal.disallowed': 'NO GOAL', 'penalty.saved': 'SAVED!', 'var.no_penalty': 'NO PENALTY',
  'match.halftime': 'HALF-TIME', 'match.fulltime': 'FULL-TIME',
};
export const MARK = { goal: '⚽', 'card.red': '🟥', 'penalty.awarded': '🎯', 'penalty.saved': '🧤', 'goal.disallowed': '📺', 'var.no_penalty': '📺', 'var.check': '📺', injury: '🩹' };

export function fmtInt(n) {
  return Math.round(n || 0).toLocaleString('en-US');
}
export function fmtPct(x, digits = 0) {
  return x == null ? '-' : `${(100 * x).toFixed(digits)}%`;
}
