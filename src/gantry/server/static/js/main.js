// Gantry's single page: boot, find or start a broadcast, follow its event stream, and keep three views in step.
import * as api from './api.js';
import { h, mount, rafThrottle } from './core/dom.js';
import { apply, createState } from './core/live.js';
import { lsGet, lsSet } from './core/store.js';
import { createChat } from './views/chat.js';
import { createConsole } from './views/console.js';
import { createStage } from './views/stage.js';

const MODE_HELP = {
  manual: 'Manual: every action waits for you',
  assisted: 'Assisted: pins and highlights go out; polls wait for you',
  autopilot: 'Autopilot: publishes on its own when confident',
};
const app = document.getElementById('app');
let boot = null;
let S = null;
let stream = null;
let views = null;
let bar = {};

function toast(text, bad = false) {
  const t = h('div', { class: `toast${bad ? ' toast--bad' : ''}`, role: 'status' }, text);
  document.body.appendChild(t);
  setTimeout(() => t.remove(), bad ? 5200 : 2600);
}

async function guard(promise) {
  try {
    return await promise;
  } catch (e) {
    toast(e.message || 'Something went wrong', true);
    return null;
  }
}

function seg(cls, options, value, onPick, label) {
  const el = h('div', { class: `seg ${cls}`, role: 'group', 'aria-label': label },
    options.map(([v, text, title]) => h('button', { 'data-v': String(v), title: title || '', 'aria-pressed': String(String(v) === String(value)), onclick: () => onPick(v) }, text)));
  el.set = (v) => { for (const b of el.children) b.setAttribute('aria-pressed', String(b.dataset.v === String(v))); };
  return el;
}

function topbar() {
  bar = {};
  bar.fixture = h('select', { class: 'select', 'aria-label': 'Match', onchange: (e) => start({ fixture: e.target.value }) },
    boot.fixtures.map((f) => h('option', { value: f.id, selected: f.id === S.fixture.id }, `${f.home.short} v ${f.away.short} · ${f.title}`)));
  bar.mode = seg('seg--mode', boot.modes.map((m) => [m, m[0].toUpperCase() + m.slice(1), MODE_HELP[m]]), S.sb.mode, (v) => guard(api.control(S.matchId, 'mode', v)), 'Producer mode');
  bar.speed = seg('', boot.speeds.map((x) => [x, `${x}×`, `${x} match seconds per second`]), S.sb.speed, (v) => guard(api.control(S.matchId, 'speed', v)), 'Clock speed');
  bar.play = h('button', { class: 'btn btn--icon', 'aria-label': 'Pause', onclick: () => guard(api.control(S.matchId, S.sb.paused ? 'play' : 'pause')) }, '❚❚');
  bar.agent = h('span', { class: 'pill pill--agent', title: 'What the graphs are doing right now' }, h('span', { class: 'dot' }), h('span', null, 'Watching'));
  const model = boot.provider === 'offline' ? 'offline model' : boot.models.smart;
  return h('header', { class: 'topbar' },
    h('div', { class: 'brand' }, h('span', { 'aria-hidden': 'true' }, '⛩'), 'Gantry', h('small', null, 'the autonomous producer')),
    bar.fixture, bar.mode, bar.speed, bar.play,
    h('button', { class: 'btn', title: 'Skip ahead to just before the next big moment', onclick: () => guard(api.control(S.matchId, 'jump')) }, 'Next moment ⏭'),
    h('button', { class: 'btn', title: 'Start this match again from kick-off', onclick: () => start({}) }, 'Restart'),
    h('span', { class: 'spacer' }), bar.agent,
    h('span', { class: 'pill', title: boot.provider === 'offline' ? 'Deterministic, free, no keys. Set GANTRY_PROVIDER=anthropic for Claude.' : `fast: ${boot.models.fast}` }, model));
}

function controls() {
  const sb = S.sb;
  bar.mode.set(sb.mode);
  bar.speed.set(sb.speed);
  bar.play.textContent = sb.paused ? '▶' : '❚❚';
  bar.play.setAttribute('aria-label', sb.paused ? 'Play' : 'Pause');
  const a = sb.agent || {};
  bar.agent.firstChild.className = `dot${a.running ? ' dot--on' : ''}`;
  bar.agent.lastChild.textContent = a.running ? `${a.window_id || ''} ${a.label || 'Working'}`.trim() : a.label || 'Watching';
  views.stage.meta([h('span', { class: 'pill' }, MODE_HELP[sb.mode]), h('span', { class: 'pill' }, `match ${S.matchId}`)]);
}

function paint(tags) {
  if (tags.includes('score')) {
    views.stage.score();
    controls();
  }
  if (tags.includes('control')) views.chat.sub();
  if (tags.includes('highlight') || tags.includes('feed')) views.stage.marks();
  if (tags.includes('poll')) views.chat.poll();
  if (tags.includes('pin')) views.chat.pinned();
  views.console.update(tags);
}

const pending = new Set();
const flush = rafThrottle(() => {
  const tags = [...pending];
  pending.clear();
  if (S && views) paint(tags);
});

function onMessage(channel, data) {
  if (!S) return;
  const tags = apply(S, channel, data, performance.now());
  if (channel === 'feed') views.stage.feed(data);
  else if (channel === 'chat') views.chat.add(data);
  else if (channel === 'curated') views.chat.labels(Object.keys(data.labels));
  for (const t of tags) pending.add(t);
  flush();
}

function build(snap) {
  if (stream) stream.close();
  S = createState(snap);
  S.sbAt = performance.now();
  lsSet('match_id', S.matchId);
  const stageRoot = h('div');
  const consoleRoot = h('section', { class: 'console', 'aria-label': 'Producer console' });
  const chat = h('aside', { class: 'col-chat', 'aria-label': 'Live chat' });
  mount(app, topbar(), h('main', { class: 'watch' }, h('div', { class: 'col-main' }, stageRoot, consoleRoot), chat));
  // the top bar wraps to two rows on narrower windows: the sticky chat column must sit below however tall it is
  const bar0 = app.querySelector('.topbar');
  const syncBar = () => document.documentElement.style.setProperty('--topbar-h', `${bar0.offsetHeight}px`);
  syncBar();
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(syncBar).observe(bar0);
  app.setAttribute('aria-busy', 'false');
  const on = {
    restart: () => start({}),
    say: (text) => guard(api.say(S.matchId, lsGet('name', 'you'), text)),
    vote: (pollId, i) => guard(api.vote(S.matchId, pollId, i)),
    decide: (body) => guard(api.decide(S.matchId, body)),
    trigger: async () => { if (await guard(api.trigger(S.matchId))) toast('Looking now'); },
    inject: async (ev) => { if (await guard(api.inject(S.matchId, ev))) toast('Event pushed into the feed'); },
    resetMemory: async () => { if (await guard(api.resetMemory())) toast('Memory cleared'); },
  };
  views = { stage: createStage(stageRoot, S, on), chat: createChat(chat, S, on), console: createConsole(consoleRoot, S, { boot, on, api }) };
  controls();
  stream = api.follow(S.matchId, S.lastSeq, onMessage, (state) => { if (state === 'error') toast('Lost the stream: the server may have restarted', true); });
}

async function start(opts) {
  const prev = S ? { fixture: S.fixture.id, mode: S.sb.mode, speed: S.sb.speed } : { fixture: 'derby', mode: lsGet('mode', 'autopilot'), speed: lsGet('speed', 10) };
  const snap = await guard(api.newMatch({ ...prev, ...opts }));
  if (snap) build(snap);
}

function loop() {
  if (S && views) {
    const now = performance.now();
    views.stage.tick(now);
    views.chat.tick(now);
    views.console.tick(now);
    if (S.sb) {
      lsSet('mode', S.sb.mode);
      lsSet('speed', S.sb.speed);
    }
  }
  setTimeout(loop, 200);
}

async function init() {
  boot = await guard(api.boot());
  if (!boot) {
    mount(app, h('p', { style: { padding: '24px' } }, 'The server is not answering. Run `make dev` and reload.'));
    return;
  }
  const saved = lsGet('match_id', null);
  let snap = null;
  if (saved) {
    try {
      snap = await api.snapshot(saved);
      if (snap.scoreboard.ended) snap = null;
    } catch {
      snap = null;
    }
  }
  if (snap) build(snap);
  else await start({});
  loop();
}

init();
