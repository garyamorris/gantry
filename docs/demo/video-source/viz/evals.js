// evals.js: the offline eval gate as one animated page. Every number is read from viz/evals_data.json (make_evals_data.py runs the gate's measurements).
// Pure function of time (see fhlib.js): ?seek=S one frame, ?fit=S warps to S seconds.
import { play, getJSON, h, easeOut, clamp } from './fhlib.js';

const DURATION = 34.9;   // = the scene length: no warp, so the times below are scene-local and sit on the spoken phrases
const NAMES = { derby: 'derby', stalemate: 'stalemate', goalfest: 'goalfest' };
const pct = (x, d = 1) => `${(100 * x).toFixed(d)}%`;
const stage = document.getElementById('stage');

function sec(css, title) { const e = h('div', { class: 'sec', style: css }, h('h2', null, title)); stage.append(e); return e; }
const fade = (e, t, t0, d = 0.6) => { const p = easeOut(clamp((t - t0) / d)); e.style.opacity = p; e.style.transform = `translateY(${(1 - p) * 10}px)`; };

async function main() {
  const D = await getJSON('evals_data.json');
  const F = D.floors;
  const ids = Object.keys(D.ndcg);
  const nd = ids.map((f) => D.ndcg[f]);
  const pr = ids.map((f) => D.curation[f].precision);
  const rc = ids.map((f) => D.curation[f].hide_recall);
  const fh = ids.reduce((a, f) => a + D.curation[f].false_hides, 0);
  const total = ids.reduce((a, f) => a + D.curation[f].comments, 0);

  // ---- A: flow 1, against hidden labels
  const A = sec('left:21px;top:62px;width:780px', 'Flow 1 · rank and curate, against hidden ground truth');
  const rows = [
    { nm: 'Moment ranking', sub: `NDCG@5 · ${nd.map((x) => x.toFixed(3)).join(' · ')}`, v: Math.min(...nd), floor: F['ndcg@5'], max: 1, fmt: (x) => x.toFixed(3), lo: 0.8, color: '#8aa0ff' },
    { nm: 'Highlights that are good', sub: `${ids.map((f) => D.curation[f].highlights).reduce((a, b) => a + b)} highlights · ${pr.map((x) => pct(x)).join(' · ')}`, v: Math.min(...pr), floor: F.highlight_precision, max: 1, fmt: (x) => pct(x), lo: 0.6, color: '#4fd1c5' },
    { nm: 'Spam and abuse held', sub: `${ids.map((f) => D.curation[f].hidden).reduce((a, b) => a + b)} of them · ${rc.map((x) => pct(x, 0)).join(' · ')}`, v: Math.min(...rc), floor: F.hide_recall, max: 1, fmt: (x) => pct(x, 0), lo: 0.8, color: '#f6a560' },
  ].map((r) => {
    const fill = h('div', { class: 'fill', style: `background:${r.color}` });
    const floor = h('div', { class: 'floor', style: `left:${(100 * (r.floor - r.lo)) / (r.max - r.lo)}%` });
    const val = h('div', { class: 'val' }, '');
    const row = h('div', { class: 'row' }, h('div', { class: 'nm', html: `${r.nm}<small>${r.sub}</small>` }), h('div', { class: 'track' }, fill, floor), val);
    A.append(row);
    return { ...r, fill, val, row };
  });
  const fhRow = h('div', { class: 'row', style: 'height:44px' }, h('div', { class: 'nm', html: 'Good comments wrongly held<small>floor: under 1%</small>' }), h('div'), h('div', { class: 'val pass', html: `${fh} <small>of ${total.toLocaleString('en-US')}</small>` }));
  A.append(fhRow);

  // ---- B: autopilot over whole matches
  const B = sec('left:21px;top:392px;width:780px', 'Autopilot · three whole matches, nobody at the desk');
  const cards = ids.map((f) => {
    const a = D.autopilot[f];
    const n = h('div', { class: 'val', style: 'text-align:left;font-size:30px;color:var(--text)' }, '0');
    const c = h('div', { class: 'row', style: 'height:58px;grid-template-columns:150px 150px 1fr' }, h('div', { class: 'nm', style: 'font-family:var(--mono)' }, NAMES[f]), n,
      h('div', { class: 'd', style: 'font-size:15px;line-height:20px;color:var(--text-2)', html: `${pct(a.mean)} of viewers vote · ${a.held_back} ideas held back<br><span class="pass">${a.overlaps} overlaps · ${a.repeats} repeats · ${a.errors} errors</span>` }));
    B.append(c);
    return { c, n, v: a.polls };
  });

  // ---- C: memory
  const C = sec('left:830px;top:62px;width:430px', 'Memory · cold, then warm');
  const mem = D.memory;
  const mk = (label, m, color) => h('div', { class: 'card', style: `margin-bottom:12px;border-color:${color}55` }, h('div', { class: 'k' }, label), h('div', { class: 'n', style: `color:${color}` }, ''),
    h('div', { class: 'd', html: `of viewers voted, on average<br>${m.polls} polls · ${m.duds} duds under 12%` }));
  const cold = mk('cold · knows nothing', mem.cold, '#8d97ad');
  const warm = mk('warm · after two other matches', mem.warm, '#7bd88a');
  C.append(cold, warm);
  const dropped = mem.cold.templates.filter((t) => !mem.warm.templates.includes(t));
  const lesson = h('div', { class: 'card', style: 'opacity:0;border-color:#2d5a3a' }, h('div', { class: 'k', style: 'color:#7bd88a' }, 'what it wrote down'),
    h('div', { class: 'd', html: `<b style="color:var(--text)">${mem.lessons[0].id.split(':')[0].replace(/_/g, ' ')}</b>: ${mem.lessons[0].text}<br><br>It stopped asking <b style="color:var(--text)">${dropped.map((x) => x.replace(/_/g, ' ')).join(', ') || 'nothing'}</b>.` }));
  C.append(lesson);
  const coldN = cold.querySelector('.n'), warmN = warm.querySelector('.n');

  const ROW_AT = [6.0, 9.7, 14.0];       // "moment ranking", "94 percent", "spam and abuse"
  const FH_AT = 16.4, B_AT = 20.5, C_AT = 27.0, WARM_AT = 28.6, COLD_AT = 31.4, LESSON_AT = 33.0;
  function render(t) {
    fade(A, t, 0.3);
    rows.forEach((r, i) => {
      r.row.style.opacity = easeOut(clamp((t - ROW_AT[i]) / 0.5));
      const k = easeOut(clamp((t - ROW_AT[i] - 0.3) / 1.6));
      r.fill.style.width = `${100 * ((r.v - r.lo) / (r.max - r.lo)) * k}%`;
      r.val.innerHTML = `<span class="${k >= 1 ? 'pass' : ''}">${r.fmt(r.lo + (r.v - r.lo) * k)}</span> <small>${k >= 1 ? '✓' : ''}</small>`;
    });
    fhRow.style.opacity = easeOut(clamp((t - FH_AT) / 0.5));
    fade(B, t, B_AT);
    cards.forEach((c, i) => { const p = easeOut(clamp((t - (B_AT + 0.3 + i * 0.9)) / 0.5)); c.c.style.opacity = p; c.n.innerHTML = `${Math.round(c.v * easeOut(clamp((t - (B_AT + 0.4 + i * 0.9)) / 1.0)))} <small>polls</small>`; });
    fade(C, t, C_AT);
    warm.style.opacity = easeOut(clamp((t - WARM_AT) / 0.5)); warmN.textContent = pct(mem.warm.mean * easeOut(clamp((t - WARM_AT - 0.1) / 1.2)), 1);
    cold.style.opacity = easeOut(clamp((t - COLD_AT) / 0.5)); coldN.textContent = pct(mem.cold.mean * easeOut(clamp((t - COLD_AT - 0.1) / 1.2)), 1);
    lesson.style.opacity = easeOut(clamp((t - LESSON_AT) / 0.6));
  }
  play({ duration: DURATION, beats: [{ t: 6.0, name: 'flow1' }, { t: 20.5, name: 'autopilot' }, { t: 27.0, name: 'memory' }, { t: 28.6, name: 'warm' }, { t: 33.0, name: 'lesson' }], render });
}
main().catch((e) => { window.__error = String(e); window.__done = true; console.error(e); });
