// The live chat, YouTube-live style: Top chat (curated) or Live chat (everything), a pinned comment, the poll, a composer.
// Message text is always a TEXT NODE (never innerHTML): the chat is untrusted input by definition.
import { h, mount, stickToBottom } from '../core/dom.js';
import { chatCounts, fmtInt, pollRows, vtNow } from '../core/live.js';
import { lsGet, lsSet } from '../core/store.js';

const MAX_NODES = 320;
const TAGS = { highlight: 'highlighted', hide: 'held', collapse: 'repeat' };

export function createChat(root, S, on) {
  const el = {};
  const nodes = new Map();
  let filter = lsGet('chat_filter', 'top');
  let votedFor = {};
  let closedShown = null;
  let closedTimer = null;

  const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Chat filter' },
    h('button', { 'data-v': 'top', title: 'Curated: spam, abuse and repeated chants are filtered out', onclick: () => setFilter('top') }, 'Top chat'),
    h('button', { 'data-v': 'live', title: 'Everything, with what was held shown dimmed', onclick: () => setFilter('live') }, 'Live chat'));
  el.list = h('div', { class: 'chat-list', role: 'log', 'aria-label': 'Chat messages', tabindex: 0 });
  el.input = h('input', { type: 'text', maxlength: 200, placeholder: 'Chat as a viewer (it gets ranked too)…', 'aria-label': 'Your message', autocomplete: 'off' });
  const form = h('form', { class: 'composer', onsubmit: (e) => { e.preventDefault(); const t = el.input.value.trim(); if (t) { on.say(t); el.input.value = ''; } } },
    el.input, h('button', { class: 'btn btn--primary', type: 'submit' }, 'Send'));
  mount(root, h('div', { class: 'chat-head' }, h('h2', null, 'Live chat'), seg), (el.sub = h('div', { class: 'chat-sub' })), (el.pinned = h('div')), (el.poll = h('div')), el.list, form);
  const stick = stickToBottom(el.list, 80);

  function setFilter(v) {
    filter = v;
    lsSet('chat_filter', v);
    el.list.className = `chat-list is-${v}`;
    for (const b of seg.children) b.setAttribute('aria-pressed', String(b.dataset.v === v));
    stick.pin();
    sub();
  }

  function sub() {
    const c = chatCounts(S);
    mount(el.sub, filter === 'top'
      ? h('span', null, 'Curated by the producer: ', h('b', null, String(c.held)), ' held, ', h('b', null, String(c.repeats)), ' repeats folded, ', h('b', null, String(c.highlights)), ' highlighted')
      : h('span', null, 'Everything, unfiltered. Held messages are dimmed.'));
  }

  function node(m) {
    const a = m.author || {};
    const lab = S.labels[m.id];
    const n = h('div', { class: 'msg', 'data-id': m.id },
      h('span', { class: 'av', style: { background: a.color || '#8e96aa' }, 'aria-hidden': 'true' }, (a.name || '?').slice(0, 1).toUpperCase()),
      h('div', { class: 'body' },
        a.badge ? h('span', { class: `badge badge--${a.badge}` }, a.badge === 'host' ? 'HOST' : a.badge === 'mod' ? 'MOD' : 'MEMBER') : null,
        h('span', { class: 'name' }, a.name || 'viewer'), h('span', { class: 'text' }, m.text),
        h('span', { class: 'tag' }), m.likes > 2 ? h('span', { class: 'likes' }, `♥ ${m.likes}`) : null));
    if (m.host) n.dataset.host = m.host;
    if (m.real) n.dataset.real = '1';
    label(n, lab);
    return n;
  }

  function label(n, lab) {
    if (!lab) return;
    n.dataset.label = lab.label;
    const tag = n.querySelector('.tag');
    tag.textContent = lab.label === 'hide' ? `held: ${lab.reason || 'filtered'}` : TAGS[lab.label] || '';
  }

  function add(m) {
    if (nodes.has(m.id)) return;
    const n = node(m);
    nodes.set(m.id, n);
    el.list.appendChild(n);
    if (nodes.size > MAX_NODES) {
      const first = el.list.firstChild;
      nodes.delete(first.dataset.id);
      first.remove();
    }
    stick.follow();
  }

  function labels(ids) {
    for (const id of ids) {
      const n = nodes.get(id);
      if (n) label(n, S.labels[id]);
    }
    sub();
    stick.follow();
  }

  function pinned() {
    const p = S.pinned;
    if (!p) return mount(el.pinned);
    const c = p.comment;
    mount(el.pinned, h('div', { class: 'pinned' }, h('div', { class: 'cap' }, '📌 Pinned by the producer'),
      h('div', null, h('span', { class: 'name', style: { color: 'var(--muted)', fontWeight: 600, marginRight: '6px' } }, (c.author && c.author.name) || 'viewer'), c.text)));
  }

  function pollCard(p, closed) {
    const rows = pollRows(p);
    const mine = votedFor[p.id];
    const reveal = closed || mine !== undefined;
    const life = h('i');
    const card = h('div', { class: `poll${closed ? ' is-closed' : ''}`, 'data-poll': p.id },
      h('div', { class: 'cap' }, closed ? '📊 Poll closed' : '📊 Live poll', h('span', { class: 'by' }, p.by === 'autopilot' ? 'on autopilot' : 'approved by you')),
      h('h3', null, p.question),
      rows.map((r, i) => h('button', { class: `opt${mine === i ? ' is-mine' : ''}${reveal && r.top ? ' is-top' : ''}`, disabled: closed, title: closed ? '' : 'Vote',
        onclick: () => { if (votedFor[p.id] === undefined) { votedFor[p.id] = i; on.vote(p.id, i); mount(el.poll, pollCard(S.poll || p, false)); } } },
        h('i', { style: { width: reveal ? `${r.pct}%` : '0%' } }), h('span', null, r.label), reveal ? h('span', { class: 'pct' }, `${r.pct}%`) : null)),
      h('div', { class: 'foot' }, h('span', null, `${fmtInt(p.total)} votes`), closed ? h('span', null, `· ${Math.round(100 * (p.participation || 0))}% of viewers`) : h('span', { class: 'life' }, life),
        p.closed_early ? h('span', null, `· ${p.closed_early}`) : null));
    card._life = life;
    return card;
  }

  /** Votes arrive many times a second: update the open card in place (re-mounting would restart its entrance animation). */
  function refresh(card, p) {
    const rows = pollRows(p);
    const reveal = votedFor[p.id] !== undefined;
    card.querySelectorAll('.opt').forEach((b, i) => {
      b.classList.toggle('is-top', reveal && rows[i].top);
      b.firstChild.style.width = reveal ? `${rows[i].pct}%` : '0%';
      const pct = b.querySelector('.pct');
      if (pct) pct.textContent = `${rows[i].pct}%`;
    });
    card.querySelector('.foot span').textContent = `${fmtInt(p.total)} votes`;
  }

  function poll() {
    const cur = el.poll.firstChild;
    if (S.poll && cur && cur.dataset.poll === S.poll.id && !cur.classList.contains('is-closed')) return refresh(cur, S.poll);
    if (S.poll) {
      closedShown = null;
      mount(el.poll, pollCard(S.poll, false));
    } else if (S.lastClosed && closedShown !== S.lastClosed.id) {
      closedShown = S.lastClosed.id;
      mount(el.poll, pollCard(S.lastClosed, true));
      clearTimeout(closedTimer);
      closedTimer = setTimeout(() => { if (!S.poll) mount(el.poll); }, 7000);
    } else if (!S.lastClosed) mount(el.poll);
    stick.follow();
  }

  function tick(now) {
    const p = S.poll;
    const card = el.poll.firstChild;
    if (!p || !card || !card._life) return;
    const left = Math.max(0, Math.min(1, (p.closes_vt - vtNow(S, now)) / p.duration_s));
    card._life.style.width = `${100 * left}%`;
  }

  for (const m of S.chat.slice(-160)) add(m);
  setFilter(filter);
  pinned();
  poll();
  stick.pin();
  return { add, labels, pinned, poll, tick, sub };
}
