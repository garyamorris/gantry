// overview.js: the Gantry loop as one animated diagram (feed + chat -> window -> showrunner [curator, action_desk] -> the stream; the memory loop underneath).
// Every number on it is read from the recorded runs (captures/real/facts.json). Pure function of time (see fhlib.js): ?seek=S one frame, ?fit=S warps to S seconds.
import { play, getJSON, h, svgEl, easeOut, ph, clamp, q } from './fhlib.js';

const DURATION = 26.3;
const CAP = q.get('cap') || 'captures/real';
const FEED = [['12\'', 'GOAL', 'Tindall scores first'], ['52\'', 'VAR', 'Okafor goal ruled out'], ['63\'', 'RED', 'Dragovic sent off'], ['71\'', 'PEN', 'penalty to Harbour City']];
const CHAT = ['GET IN!!!', 'FREE betting tips, check my channel', 'Okafor only needed half a yard, they gave him two', 'the ref is a brain-dead clown', 'LETS GO HAR HAR HAR', 'Does Dragovic miss the next match for that?'];
const COL = { feed: '#4fd1c5', chat: '#f6a560', f1: '#8aa0ff', f2: '#cf8ae8', mem: '#7bd88a', out: '#f2c14e' };

const stage = document.getElementById('stage');
const wires = document.getElementById('wires');
const els = [];
function box(cls, css, html, extra = {}) {
  const e = h('div', { class: cls, style: css, html });
  if (extra.c) e.style.setProperty('--c', extra.c);
  stage.append(e);
  return e;
}
const at = (e, t, t0, d = 0.6, dy = 10) => { const p = easeOut(clamp((t - t0) / d)); e.style.opacity = p; e.style.transform = `translateY(${(1 - p) * dy}px)`; };
function wire(d, color, w = 2.4) {
  const p = svgEl('path', { d, stroke: color, 'stroke-width': w, opacity: 0 }, wires);
  p._len = 0;
  return p;
}
function draw(p, t, t0, dur) {
  if (!p._len) { p._len = p.getTotalLength(); p.setAttribute('stroke-dasharray', `${p._len} ${p._len}`); }
  const k = clamp((t - t0) / dur);
  p.setAttribute('stroke-dashoffset', p._len * (1 - easeOut(k)));
  p.setAttribute('opacity', k > 0 ? 0.9 : 0);
}

async function main() {
  const facts = await getJSON(`../${CAP}/facts.json`);
  const w63 = facts.window_63, cur = facts.curator;

  // ---------------------------------------------------------------------------------------------- sources
  const feedBox = box('box', 'left:20px;top:66px;width:272px;height:196px;border-color:#2c5d59', `<div class="t" style="color:${COL.feed}">live sports feed</div><div class="s">goals, cards, VAR, subs</div>`);
  const feedTicks = FEED.map(([m, k, txt], i) => { const e = h('div', { class: 'tick', style: `top:${66 + i * 24}px`, html: `<b style="color:${COL.feed}">${m} ${k}</b>${txt}` }); feedBox.append(e); return e; });
  const chatBox = box('box', 'left:20px;top:282px;width:272px;height:230px;border-color:#6a4a2c', `<div class="t" style="color:${COL.chat}">live chat</div><div class="s">${cur.comments} messages in a window</div>`);
  const chatTicks = CHAT.map((txt, i) => { const e = h('div', { class: 'tick', style: `top:${66 + i * 25}px`, html: `<b style="color:${COL.chat}">▸</b>${txt}` }); chatBox.append(e); return e; });

  // ---------------------------------------------------------------------------------------------- window
  const win = box('box', 'left:318px;top:150px;width:190px;height:268px;border-color:#38435e', `<div class="t">window</div><div class="s">one stretch of the broadcast, as JSON</div>
    <div class="tick" style="top:84px;opacity:1"><b>id</b>w001</div><div class="tick" style="top:106px;opacity:1"><b>trigger</b>${w63.trigger.reason}</div>
    <div class="tick" style="top:128px;opacity:1"><b>events</b>${w63.events}</div><div class="tick" style="top:150px;opacity:1"><b>comments</b>${w63.comments}</div>
    <div class="tick" style="top:172px;opacity:1"><b>minute</b>${w63.minute}</div><div class="tick" style="top:194px;opacity:1"><b>score</b>1-1</div>
    <div class="tick" style="top:216px;opacity:1;color:var(--text-3)">its own thread</div>`);

  // ---------------------------------------------------------------------------------------------- showrunner
  const show = box('box', 'left:536px;top:92px;width:510px;height:440px;border:1.5px dashed #4b5878;background:rgba(19,24,35,0.35)', `<div class="t">showrunner</div><div class="s" style="top:32px">triage, then two subgraphs</div>`);
  const triage = h('div', { class: 'chip', style: 'right:14px;top:11px' }, 'triage'); show.append(triage);
  const f1 = h('div', { class: 'box', style: `left:12px;top:60px;width:486px;height:166px;--c:${COL.f1};border-color:#3b4a86;background:var(--surface)` }, h('div', { class: 't', style: `color:${COL.f1}` }, 'curator'), h('div', { class: 'tag', style: `--c:${COL.f1}` }, 'flow 1 · rank & curate'));
  const f2 = h('div', { class: 'box', style: `left:12px;top:238px;width:486px;height:192px;--c:${COL.f2};border-color:#6a3f86;background:var(--surface)` }, h('div', { class: 't', style: `color:${COL.f2}` }, 'action_desk'), h('div', { class: 'tag', style: `--c:${COL.f2}` }, 'flow 2 · generate actions'));
  show.append(f1, f2);
  /** a row of chips laid out left to right from their MEASURED widths, so nothing can overflow its card */
  const row = (parent, labels, y, c, gap = 8, size = null) => labels.map((text, i) => {
    const e = h('div', { class: 'chip', style: `top:${y}px;--c:${c}${size ? `;font-size:${size}px` : ''}` }, text);
    parent.append(e);
    e._i = i;
    return e;
  }).map((e, i, all) => { e.style.left = `${14 + all.slice(0, i).reduce((x, p) => x + p.offsetWidth + gap, 0)}px`; return e; });
  const c1 = row(f1, ['rank_moments', `score_shard ×${cur.shards}`, 'merge', 'editor'], 46, COL.f1);
  const f1out = h('div', { class: 's', style: 'left:14px;top:90px;right:14px;opacity:0;font-size:16px;line-height:21px;color:var(--text-2)', html: `<b style="color:var(--text)">moments ranked</b> with reasons · <b style="color:var(--text)">${cur.highlights}</b> best comments · <b style="color:var(--text)">${cur.hidden}</b> held as spam or abuse · <b style="color:var(--text)">${cur.collapsed}</b> repeats folded` });
  f1.append(f1out);
  const f1bar = h('div', { class: 'bar', style: `left:14px;top:140px;width:456px;--c:${COL.f1};opacity:0` }, h('i')); f1.append(f1bar);
  const c2 = row(f2, ['recall', 'propose', 'poll_studio', 'screen', 'handle ×3'], 46, COL.f2, 6);
  c2[0].style.setProperty('--c', COL.mem);
  const writers = row(f2, ['analyst', 'hype', 'terrace'], 86, COL.f2, 8, 14).map((e, i) => { e.style.left = `${150 + i * 104}px`; return e; });
  const judge = row(f2, ['judge'], 118, COL.f2, 8, 14)[0]; judge.style.left = '254px';
  const verdicts = row(f2, ['publish', 'ask a human', 'hold, with a reason'], 156, '#8d97ad', 12);
  verdicts[0].style.setProperty('--c', '#6fd884'); verdicts[1].style.setProperty('--c', '#f2c14e');

  // ---------------------------------------------------------------------------------------------- the stream (outputs)
  const stream = box('box', 'left:1074px;top:92px;width:188px;height:424px;border-color:#6a5a1f', `<div class="t" style="color:${COL.out}">the stream</div><div class="s">what the viewers see</div>`);
  const out = (top, label, text, stamp, cls) => h('div', { class: 'out', style: `left:10px;top:${top}px;width:168px;--c:${COL.out}`, html: `<b>${label}</b>${text}<span class="stamp ${cls}">${stamp}</span>` });
  const outs = [out(66, 'poll', 'Red card: fair?', 'published', 'pub'), out(130, 'pin', 'the best comment', 'waits', 'ask'), out(194, 'highlight', '63\' · RED CARD', 'waits', 'ask'), out(258, 'chat', 'Top chat, curated', 'live', 'pub')];
  stream.append(...outs);
  const modes = h('div', { class: 's', style: 'left:12px;top:326px;right:10px;font-size:14px;line-height:18px;opacity:0', html: '<b style="color:var(--text)">Autopilot</b> publishes what it is sure of. <b style="color:var(--text)">Manual</b> waits for a human.' });
  stream.append(modes);

  // ---------------------------------------------------------------------------------------------- memory
  const store = box('box', 'left:318px;top:562px;width:728px;height:150px;border-color:#2d5a3a', `<div class="t" style="color:${COL.mem}">LangGraph Store · what it has done and what it learned</div>`);
  const sub = [['ledger', 'every action, and every refusal, with its reason', 14], ['preferences', 'what this audience votes on, per kind of poll', 252], ['lessons', 'written down, found by meaning', 490]].map(([t, s2, x]) => {
    const e = h('div', { class: 'out', style: `left:${x}px;top:44px;width:224px;height:98px;--c:${COL.mem};opacity:0;background:var(--surface-2)`, html: `<b>${t}</b>${s2}` });
    store.append(e);
    return e;
  });
  const prefRow = h('div', { class: 'tick', style: 'left:12px;top:68px;right:8px;opacity:0;font-size:15px', html: `<span style="color:${COL.mem};font-family:var(--mono);font-weight:650;margin-right:8px">decision</span>27% ↑` }); sub[1].append(prefRow);
  const closed = box('out', `left:1074px;top:562px;width:188px;--c:${COL.out}`, '<b>poll closed</b>21% of viewers voted');
  const keeper = box('chip', 'left:1074px;top:652px;--c:#7bd88a', 'memory_keeper');
  const others = box('big', 'left:20px;top:606px;width:280px;white-space:normal;font-family:var(--font);font-weight:500;font-size:15px;line-height:20px;color:var(--text-3)', 'Also: <b style="color:var(--text)">recap_press</b> writes the half-time and full-time recaps. <b style="color:var(--text)">producer_chat</b> answers "what did you do, and why?"');

  // wires
  const wFeed = wire('M292 164 C 305 164 305 240 318 250', COL.feed);
  const wChat = wire('M292 396 C 305 396 305 290 318 300', COL.chat);
  const wWin = wire('M508 284 C 522 284 522 180 536 180', '#8d97ad');
  const wF12 = wire('M790 318 L 790 330', COL.f1, 2.2);
  const wOut = wire('M1046 250 C 1060 250 1060 220 1074 220', COL.out);
  const wClose = wire('M1168 516 L 1168 562', COL.out, 2);
  const wKeep = wire('M1074 664 L 1046 664', COL.mem, 2.2);
  const wRecall = wire('M440 562 C 440 480 520 410 538 394', COL.mem, 2.2);

  const WANT = (t, a, b) => clamp((t - a) / (b - a));
  // times in clip seconds (this clip is fitted 1:1 to the scene, so these are scene-local); phrases from narration/words.json (+0.35 s pad)
  const T = { src: 0.4, win: 4.4, wWin: 7.9, show: 8.6, triage: 9.2, f1: 11.7, c1: 12.1, f1out: 13.6, bar: 13.8, wF12: 14.0, f2: 14.3, c2: 14.7, writers: 16.1, judge: 16.9, verdicts: 17.6,
              wOut: 19.6, stream: 19.9, outs: 20.3, modes: 22.0, store: 22.5, sub: 22.9, closed: 24.3, keeper: 24.7, pref: 25.0, recall: 24.6, others: 25.4 };
  function render(t) {
    // sources + tickers
    at(feedBox, t, T.src); at(chatBox, t, T.src + 0.3);
    feedTicks.forEach((e, i) => { const p = easeOut(clamp((t - (1.0 + i * 0.55)) / 0.4)); e.style.opacity = p; e.style.transform = `translateX(${(1 - p) * 14}px)`; });
    chatTicks.forEach((e, i) => { const p = easeOut(clamp((t - (1.3 + i * 0.4)) / 0.4)); e.style.opacity = p; e.style.transform = `translateX(${(1 - p) * 14}px)`; });
    // window
    draw(wFeed, t, T.win - 0.7, 0.7); draw(wChat, t, T.win - 0.5, 0.7);
    at(win, t, T.win, 0.7);
    // showrunner
    draw(wWin, t, T.wWin, 0.7);
    at(show, t, T.show, 0.6); at(triage, t, T.triage, 0.4);
    at(f1, t, T.f1, 0.5);
    c1.forEach((e, i) => { at(e, t, T.c1 + i * 0.3, 0.3, 4); e.classList.toggle('on', t > T.c1 + 0.3 + i * 0.3 && t < T.c1 + 1.4 + i * 0.3); });
    at(f1out, t, T.f1out, 0.5, 4);
    f1bar.style.opacity = clamp((t - T.bar) / 0.3); f1bar.firstChild.style.width = `${100 * easeOut(clamp((t - T.bar - 0.1) / 1.1))}%`;
    draw(wF12, t, T.wF12, 0.3);
    at(f2, t, T.f2, 0.5);
    c2.forEach((e, i) => { at(e, t, T.c2 + i * 0.3, 0.3, 4); e.classList.toggle('on', t > T.c2 + 0.3 + i * 0.3 && t < T.c2 + 1.4 + i * 0.3); });
    writers.forEach((e, i) => { at(e, t, T.writers + i * 0.12, 0.3, 4); e.classList.toggle('on', t > T.writers + 0.1 && t < T.writers + 1.0); });
    at(judge, t, T.judge, 0.3, 4); judge.classList.toggle('on', t > T.judge + 0.1 && t < T.judge + 0.8);
    verdicts.forEach((e, i) => { at(e, t, T.verdicts + i * 0.55, 0.3, 4); e.classList.toggle('on', t > T.verdicts + 0.5 + i * 0.55); });
    // outputs
    draw(wOut, t, T.wOut, 0.4); at(stream, t, T.stream, 0.5);
    outs.forEach((e, i) => at(e, t, T.outs + i * 0.4, 0.4, 6));
    at(modes, t, T.modes, 0.5);
    // memory
    at(store, t, T.store, 0.5);
    sub.forEach((e, i) => at(e, t, T.sub + i * 0.3, 0.4, 6));
    at(closed, t, T.closed, 0.4); draw(wClose, t, T.closed + 0.2, 0.4);
    at(keeper, t, T.keeper, 0.3); draw(wKeep, t, T.keeper + 0.1, 0.3);
    at(prefRow, t, T.pref, 0.4, 4);
    sub[1].style.borderColor = t > T.pref && t < T.pref + 1.6 ? COL.mem : '';
    draw(wRecall, t, T.recall, 0.9);
    at(others, t, T.others, 0.5);
  }
  play({
    duration: DURATION,
    beats: [{ t: 0.4, name: 'sources' }, { t: 4.4, name: 'window' }, { t: 8.6, name: 'showrunner' }, { t: 11.7, name: 'flow1' }, { t: 14.3, name: 'flow2' }, { t: 19.9, name: 'stream' }, { t: 22.5, name: 'memory' }, { t: 24.6, name: 'recall' }],
    render,
  });
}
main().catch((e) => { window.__error = String(e); window.__done = true; console.error(e); });
