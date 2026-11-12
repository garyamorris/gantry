// The stage: what would be the video player. A pitch, a score bug, the commentary line, and the match timeline.
import { h, svg, mount } from '../core/dom.js';
import { ballSpot, FLASH, MARK, fmtInt, vtNow } from '../core/live.js';

function pitch() {
  const stripes = [];
  for (let i = 0; i < 10; i++) stripes.push(svg('rect', { x: 6 + i * 14.8, y: 6, width: 14.8, height: 78, fill: i % 2 ? '#0f3a22' : '#11422a' }));
  const line = { fill: 'none', stroke: 'rgba(255,255,255,.55)', 'stroke-width': 0.5 };
  return svg('svg', { class: 'pitch', viewBox: '0 0 160 90', preserveAspectRatio: 'xMidYMid slice', 'aria-hidden': 'true' },
    svg('rect', { width: 160, height: 90, fill: '#0a2014' }), stripes,
    svg('rect', { x: 6, y: 6, width: 148, height: 78, ...line }), svg('line', { x1: 80, y1: 6, x2: 80, y2: 84, ...line }), svg('circle', { cx: 80, cy: 45, r: 9, ...line }),
    svg('rect', { x: 6, y: 24, width: 22, height: 42, ...line }), svg('rect', { x: 132, y: 24, width: 22, height: 42, ...line }),
    svg('rect', { x: 6, y: 35, width: 8, height: 20, ...line }), svg('rect', { x: 146, y: 35, width: 8, height: 20, ...line }),
    svg('rect', { x: 3.6, y: 40, width: 2.4, height: 10, ...line }), svg('rect', { x: 154, y: 40, width: 2.4, height: 10, ...line }),
    svg('circle', { class: 'ring', cx: 80, cy: 45, r: 3, fill: 'none', stroke: '#fff', 'stroke-width': 0.6, opacity: 0 }),
    svg('circle', { class: 'ball', cx: 80, cy: 45, r: 1.9, fill: '#fff', stroke: '#111', 'stroke-width': 0.4 }));
}

export function createStage(root, S, on) {
  const fx = S.fixture;
  const el = {};
  const stage = h('div', { class: 'stage', id: 'main', role: 'img', 'aria-label': `${fx.home.name} against ${fx.away.name}, live` },
    pitch(),
    h('div', { class: 'scorebug', 'aria-live': 'off' },
      h('span', { class: 'team' }, h('i', { style: { background: fx.home.color } }), fx.home.short), (el.sh = h('span', { class: 'score' }, '0')),
      (el.sa = h('span', { class: 'score' }, '0')), h('span', { class: 'team' }, fx.away.short, h('i', { style: { background: fx.away.color } })),
      (el.clock = h('span', { class: 'clock' }, 'KO'))),
    h('div', { class: 'livebadge' }, (el.live = h('span', { class: 'tag' }, h('span', { class: 'dot' }), 'LIVE')), (el.viewers = h('span', { class: 'viewers' }, ''))),
    (el.flash = h('div', { class: 'flash', 'aria-hidden': 'true' }, h('b'))),
    (el.lower = h('div', { class: 'lowerthird', role: 'status', 'aria-live': 'polite' }, (el.lmin = h('span', { class: 'min' }, '')), (el.ltxt = h('span', { class: 'txt' }, 'Waiting for kick-off.')))),
    (el.overlay = h('div', { class: 'overlay', hidden: true })));
  const timeline = h('div', { class: 'timeline', 'aria-label': 'Match timeline' }, h('div', { class: 'track' }, (el.fill = h('div', { class: 'fill' }))), (el.marks = h('div')),
    (el.head = h('div', { class: 'head' })));
  const ht = h('div', { class: 'ht', title: 'Half-time' });
  timeline.appendChild(ht);
  const total = S.timing.end_vt;
  const pos = (vt) => `${Math.max(0, Math.min(100, (100 * vt) / total))}%`;
  ht.style.left = pos(S.timing.p1_end_vt);
  ht.style.width = `${(100 * (S.timing.p2_start_vt - S.timing.p1_end_vt)) / total}%`;
  const title = h('div', { class: 'titlebar' },
    h('div', null, h('h1', null, `${fx.home.name} v ${fx.away.name}`), h('div', { class: 'sub' }, `${fx.title} · ${fx.competition}`), h('div', { class: 'sub' }, fx.blurb)),
    (el.meta = h('div', { class: 'meta' })));
  mount(root, stage, timeline, title);
  const ball = stage.querySelector('.ball');
  const ring = stage.querySelector('.ring');
  const seenMarks = new Set();

  function score() {
    const sb = S.sb;
    el.sh.textContent = sb.score.home;
    el.sa.textContent = sb.score.away;
    el.clock.textContent = sb.minute;
    el.viewers.textContent = `${fmtInt(sb.viewers)} watching`;
    const off = sb.ended || sb.phase === 'pre';
    el.live.className = `tag${off ? ' tag--off' : ''}`;
    el.live.lastChild.textContent = sb.ended ? 'ENDED' : sb.phase === 'pre' ? 'STARTING' : sb.paused ? 'PAUSED' : 'LIVE';
    if (sb.ended) {
      el.overlay.hidden = false;
      mount(el.overlay, h('div', null, h('h2', null, `Full-time: ${fx.home.name} ${sb.score.home}-${sb.score.away} ${fx.away.name}`),
        h('p', null, 'The broadcast is over. The ledger and what the producer learned are still below; start another match and it will remember.'),
        h('button', { class: 'btn btn--primary', onclick: () => on.restart() }, 'Play it again')));
    } else el.overlay.hidden = true;
  }

  function tick(now) {
    const vt = vtNow(S, now);
    el.fill.style.width = pos(vt);
    el.head.style.left = pos(vt);
  }

  function marks() {
    for (const m of S.markers) {
      if (seenMarks.has(m.id)) continue;
      seenMarks.add(m.id);
      const icon = MARK[m.type];
      el.marks.appendChild(h('span', { class: `mark${icon ? '' : ' mark--card'}`, style: { left: pos(m.vt) }, title: `${m.minute} ${m.text}` }, icon || '•'));
    }
    for (const c of S.highlights) {
      if (seenMarks.has(`h:${c.id}`)) continue;
      seenMarks.add(`h:${c.id}`);
      el.marks.appendChild(h('span', { class: 'flag', style: { left: pos(c.vt) }, title: `Highlight: ${c.title}` }));
    }
  }

  function feed(ev, animate = true) {
    el.lmin.textContent = ev.minute;
    el.ltxt.textContent = ev.text;
    const side = ev.team === 'home' ? fx.home.color : ev.team === 'away' ? fx.away.color : '#eceef4';
    el.lower.style.borderLeftColor = side;
    const spot = ballSpot(ev);
    ball.setAttribute('cx', spot.x);
    ball.setAttribute('cy', spot.y);
    if (!animate) return;
    el.lower.classList.remove('is-new');
    void el.lower.offsetWidth;
    el.lower.classList.add('is-new');
    ring.setAttribute('cx', spot.x);
    ring.setAttribute('cy', spot.y);
    ring.classList.remove('is-on');
    void ring.getBoundingClientRect();
    ring.classList.add('is-on');
    const word = FLASH[ev.type];
    if (word) {
      const b = el.flash.firstChild;
      b.textContent = word;
      b.style.setProperty('--c', ev.type === 'goal' ? side : ev.type === 'card.red' ? '#b91c1c' : 'rgba(10,12,16,.82)');
      el.flash.classList.remove('is-on');
      void el.flash.offsetWidth;
      el.flash.classList.add('is-on');
    }
  }

  function meta(pills) {
    mount(el.meta, pills);
  }

  if (S.feed.length) feed(S.feed[S.feed.length - 1], false);
  score();
  marks();
  return { score, tick, marks, feed, meta };
}
