// The producer's console: what the graphs ranked (flow 1), what they want to do (flow 2), what they did, what they learned.
import { h, mount } from '../core/dom.js';
import { fmtPct, meanParticipation, realSecondsUntil } from '../core/live.js';
import { lsGet, lsSet } from '../core/store.js';

const TABS = [['rundown', 'Rundown'], ['inbox', 'Inbox'], ['ledger', 'Ledger'], ['memory', 'Memory'], ['pipeline', 'Pipeline']];
const KIND = { poll: 'Poll', pin_comment: 'Pin a comment', highlight: 'Highlight', host_message: 'Channel message', memory_write: 'Remember this?' };
const MODE_EMPTY = {
  autopilot: ['Autopilot is on.', ' The producer publishes on its own when it is confident and the topic is low-risk; anything else lands here for you. Switch to Assisted or Manual to approve every poll yourself.'],
  assisted: ['Nothing waiting.', ' In Assisted mode pins and highlights go out on their own; every poll waits here for you.'],
  manual: ['Nothing waiting.', ' In Manual mode every action waits here. Suggestions expire when the moment passes: slow the clock down if you want time to think.'],
};

function summarize(e) {
  const d = e.data || {};
  switch (e.kind) {
    case 'window.open': return `▶ ${d.window_id} ${d.minute} · ${d.trigger.kind}${d.trigger.reason ? ` (${d.trigger.reason})` : ''} · ${d.events} events, ${d.comments} comments · ${d.mode}`;
    case 'window.skip': return `skipped: ${d.reason}`;
    case 'window.done': return `■ done: ${Object.entries(d.actions || {}).map(([k, v]) => `${v.length} ${k}`).join(', ') || 'no actions'}`;
    case 'moment.ranked': return `#${d.rank} ${d.minute} ${d.type} = ${d.score}  (${(d.why || []).slice(1).map((w) => w.label).join(', ') || 'base'})`;
    case 'chat.scored': return `shard ${d.shard}: ${d.n} comments scored, ${d.hidden} hidden`;
    case 'editor.pick': return `${d.source} picked ${d.highlight.length} highlight(s)${d.pin ? ' and a pin' : ''}`;
    case 'curation.result': return `curated: ${d.highlight.length} highlighted, ${d.hidden.length} held, ${d.collapsed.length} folded · chat at ${d.heat}x`;
    case 'memory.recalled': return `recalled: ${d.ledger.published} published, ${d.ledger.suppressed} held back, ${d.polls.length} recent polls, ${d.lessons.length} lessons`;
    case 'action.proposed': return `proposed ${d.kind} ${d.topic} · priority ${d.priority}${d.expected != null ? ` · expected ${d.expected}` : ''}${d.value != null ? ` · value ${d.value}` : ''}`;
    case 'poll.brief': return `brief: ${d.topic} (${d.template})`;
    case 'poll.draft': return `${d.writer} r${d.round}${d.source === 'fallback' ? ' (fallback)' : ''}: “${d.question}”`;
    case 'poll.judged': return `judge r${d.round}: ${d.scores.map((s) => `${s.writer} ${s.score}`).join(', ')} → ${d.best}`;
    case 'poll.final': return `poll: “${d.question}” [${d.options.join(' | ')}]`;
    case 'action.screened': return `${d.verdict.toUpperCase()} ${d.kind}: ${d.reason}`;
    case 'action.pending': return `waiting for a human: ${d.action.kind} ${d.action.question || d.action.title || d.action.text || ''}`;
    case 'action.publish': return `PUBLISHED ${d.action.kind} by ${d.by}${d.edited ? ' (edited)' : ''}: ${d.action.question || d.action.title || d.action.text || ''}`;
    case 'action.closed': return `${d.status} ${d.kind}: ${d.reason}`;
    case 'memory.written': return `ledger ← ${d.key}`;
    case 'memory.outcome': return `poll closed: ${fmtPct(d.participation, 1)} voted on “${d.question}”`;
    case 'memory.pref': return `learned: ${d.template} now averages ${fmtPct(d.mean, 1)} over ${d.n} (channel ${fmtPct(d.average, 1)})`;
    case 'memory.lesson': return `LESSON: ${d.text}`;
    case 'recap.section': return `recap (${d.kind}): ${d.scoreline}`;
    case 'progress': return d.label;
    case 'note': case 'error': return d.text;
    default: return e.kind;
  }
}

export function createConsole(root, S, ctx) {
  let tab = lsGet('console_tab', 'rundown');
  let ledgerFilter = 'all';
  let memory = null;
  let memoryAt = -1;
  let studio = null;
  const editing = new Set();
  const el = {};
  const head = h('div', { class: 'console-head', role: 'tablist', 'aria-label': 'Producer console' }, h('span', { class: 'label' }, 'Producer'),
    TABS.map(([id, label]) => h('button', { class: 'tab', role: 'tab', 'data-tab': id, onclick: () => show(id) }, label, id === 'inbox' ? (el.count = h('span', { class: 'n' })) : null)),
    (el.kpis = h('div', { class: 'kpis' })));
  el.body = h('div', { class: 'console-body', role: 'tabpanel' });
  mount(root, head, el.body);

  function show(id) {
    tab = id;
    lsSet('console_tab', id);
    for (const b of head.querySelectorAll('.tab')) b.setAttribute('aria-selected', String(b.dataset.tab === id));
    render();
    if (id === 'memory') loadMemory(true);
  }

  function kpis() {
    const st = S.sb.agent || {};
    const mean = meanParticipation(S.polls);
    const n = S.actions.filter((a) => a.status === 'published').length;
    mount(el.kpis,
      h('span', { class: 'kpi' }, 'actions ', h('b', null, String(n))), h('span', { class: 'kpi' }, 'polls ', h('b', null, String(S.polls.length + (S.poll ? 1 : 0)))),
      h('span', { class: 'kpi' }, 'avg vote ', h('b', null, mean == null ? '-' : fmtPct(mean))), h('span', { class: 'kpi', title: 'How far behind the clock the last window started, in match seconds' }, 'lag ', h('b', null, `${S.sb.lag || 0}s`)),
      h('span', { class: 'kpi' }, st.running ? '● working' : '○ watching'));
    el.count.textContent = String(S.suggestions.size);
    el.count.dataset.n = String(S.suggestions.size);
  }

  // ---------------------------------------------------------------------------------------------- rundown
  function rundown() {
    const r = S.rundowns[S.rundowns.length - 1];
    if (!r) return h('div', { class: 'empty' }, h('b', null, 'No rundown yet.'), ' The first window opens a few minutes after kick-off (or on the first big moment). Each one ranks what happened on the pitch and in the chat.');
    const moments = r.moments.length ? r.moments.map((m) => h('div', { class: 'moment' }, h('span', { class: 'min' }, m.minute),
      h('div', null, h('div', { class: 'txt' }, m.text), h('div', { class: 'chips' }, m.why.map((w, i) => h('span', { class: `chip${i ? (w.delta >= 0 ? ' chip--up' : ' chip--down') : ''}` }, i ? `${w.delta > 0 ? '+' : ''}${w.delta.toFixed(2)} ${w.label}` : `${w.label} ${w.delta.toFixed(2)}`))),
        h('div', { class: 'bar' }, h('i', { style: { width: `${Math.round(100 * m.score)}%` } }))),
      h('span', { class: 'score' }, m.score.toFixed(2)))) : h('div', { class: 'empty' }, 'Nothing on the pitch worth ranking in this window.');
    const best = r.highlights.length ? r.highlights.map((c) => h('div', { class: 'quote' }, h('div', null, c.text), h('div', { class: 'who' }, `${(c.author && c.author.name) || 'viewer'}${c.likes ? ` · ♥ ${c.likes}` : ''}`)))
      : h('div', { class: 'empty' }, 'No comment cleared the bar this window.');
    const max = Math.max(1, ...r.themes.map((t) => t.count));
    return h('div', null,
      h('div', { class: 'note' }, h('span', { class: 'who' }, `Producer’s note · ${r.minute} · window ${r.window_id}`), r.note || ''),
      h('div', { class: 'grid2' },
        h('div', null, h('div', { class: 'h3' }, 'Moments, ranked'), moments),
        h('div', null, h('div', { class: 'h3' }, 'Best of the chat'), best,
          h('div', { class: 'h3' }, 'What the chat is talking about'),
          r.themes.length ? r.themes.map((t) => h('div', { class: 'theme' }, h('span', { style: { minWidth: '130px' } }, t.label), h('div', { class: 'bar' }, h('i', { style: { width: `${(100 * t.count) / max}%` } })), h('span', { class: 'kpi' }, String(t.count)))) : h('div', { class: 'kpi' }, 'No theme stands out.'),
          h('div', { class: 'chips', style: { marginTop: '12px' } }, h('span', { class: 'chip' }, `${r.hidden.length} held as spam or abuse`), h('span', { class: 'chip' }, `${r.collapsed} repeats folded`), h('span', { class: 'chip' }, `chat at ${r.heat}x`)))));
  }

  // ------------------------------------------------------------------------------------------------- inbox
  function card(s) {
    const a = s.action;
    const isPoll = a.kind === 'poll';
    const edit = editing.has(s.id);
    const inputs = {};
    const timer = h('span', { class: 'sugg-timer', 'data-exp': s.expires_vt == null ? '' : String(s.expires_vt) });
    const body = edit
      ? h('div', { class: 'sugg-edit' },
        isPoll ? [(inputs.q = h('input', { value: a.question, maxlength: 140, 'aria-label': 'Question' })), a.options.map((o, i) => (inputs[`o${i}`] = h('input', { value: o, maxlength: 40, 'aria-label': `Option ${i + 1}` })))]
          : (inputs.t = h('input', { value: a.text || '', maxlength: 240, 'aria-label': 'Text' })))
      : [h('div', { class: 'sugg-q' }, a.question || a.title || a.text || a.topic), isPoll ? h('div', { class: 'sugg-opts' }, a.options.map((o) => h('span', null, o))) : (a.title && a.text ? h('div', { class: 'sugg-why' }, a.text) : null)];
    const send = (decision) => {
      const payload = { id: s.id, decision };
      if (decision === 'edit') {
        if (isPoll) Object.assign(payload, { question: inputs.q.value, options: a.options.map((_, i) => inputs[`o${i}`].value).filter((x) => x.trim()) });
        else payload.text = inputs.t.value;
      }
      editing.delete(s.id);
      ctx.on.decide(payload);
    };
    const memoryCard = a.kind === 'memory_write';
    return h('div', { class: `sugg${isPoll ? ' sugg--poll' : ''}${s.deciding ? ' is-busy' : ''}` },
      h('div', { class: 'sugg-head' }, h('span', { class: 'sugg-kind' }, KIND[a.kind] || a.kind), a.template ? h('span', { class: 'chip' }, a.template.replace(/_/g, ' ')) : null,
        a.risk && a.risk !== 'low' ? h('span', { class: 'chip chip--expired' }, `${a.risk} risk`) : null, a.confidence != null ? h('span', { class: 'chip' }, `confidence ${a.confidence.toFixed(2)}`) : null, timer),
      body,
      h('div', { class: 'sugg-why' }, memoryCard ? `Evidence: ${a.evidence.n} polls averaging ${fmtPct(a.evidence.mean)} against a channel average of ${fmtPct(a.evidence.average)}.`
        : [a.why ? `${a.why[0].toUpperCase()}${a.why.slice(1)}.` : '', a.writer ? ` Written by the ${a.writer} voice (judge ${a.judge}).` : '', a.minute ? ` Proposed at ${a.minute}.` : '']),
      h('div', { class: 'sugg-actions' },
        edit ? [h('button', { class: 'btn btn--primary', onclick: () => send('edit') }, 'Publish edited'), h('button', { class: 'btn', onclick: () => { editing.delete(s.id); render(); } }, 'Cancel')]
          : [h('button', { class: 'btn btn--primary', onclick: () => send('approve') }, memoryCard ? 'Keep' : isPoll ? 'Publish poll' : 'Approve'),
            memoryCard || a.kind === 'pin_comment' || a.kind === 'highlight' ? null : h('button', { class: 'btn', onclick: () => { editing.add(s.id); render(); } }, 'Edit'),
            h('button', { class: 'btn btn--danger', onclick: () => send('reject') }, memoryCard ? 'Discard' : 'Reject')]));
  }

  function inbox() {
    const list = [...S.suggestions.values()].sort((a, b) => (a.action.kind === 'poll' ? 0 : 1) - (b.action.kind === 'poll' ? 0 : 1) || a.created_vt - b.created_vt);
    if (!list.length) {
      const [b, rest] = MODE_EMPTY[S.sb.mode] || MODE_EMPTY.manual;
      return h('div', { class: 'empty' }, h('b', null, b), rest);
    }
    return h('div', null, list.map(card));
  }

  // ------------------------------------------------------------------------------------------------ ledger
  function ledger() {
    const held = (a) => a.status !== 'published';
    const rows = S.actions.filter((a) => ledgerFilter === 'all' || (ledgerFilter === 'published' ? !held(a) : held(a))).slice(-90).reverse();
    const chip = (id, label) => h('button', { class: 'btn', 'aria-pressed': String(ledgerFilter === id), style: ledgerFilter === id ? { borderColor: 'var(--agent)', color: 'var(--agent)' } : null, onclick: () => { ledgerFilter = id; render(); } }, label);
    return h('div', null,
      h('div', { class: 'filterbar' }, chip('all', `Everything (${S.actions.length})`), chip('published', 'Published'), chip('held', 'Held back, and why')),
      rows.length ? rows.map((a) => h('div', { class: 'row' }, h('span', { class: 'min' }, a.minute), h('span', null, h('span', { class: `chip chip--${a.status}` }, a.status)),
        h('span', { class: 'what' }, h('b', null, `${KIND[a.kind] || a.kind}: `), a.text || a.topic, h('span', { class: 'why' }, [a.by ? `by ${a.by}${a.edited ? ' (edited)' : ''} · ` : '', a.reason || a.why || '']))))
        : h('div', { class: 'empty' }, h('b', null, 'Nothing here yet.'), ' Every action the producer takes, and every one it decides against, is written to the ledger with its reason. That record is what lets it run on its own without repeating itself.'));
  }

  // ------------------------------------------------------------------------------------------------ memory
  async function loadMemory(force) {
    if (!force && memoryAt === S.memoryVersion) return;
    memoryAt = S.memoryVersion;
    try {
      memory = await ctx.api.memory();
    } catch {
      memory = { prefs: [], lessons: [] };
    }
    if (tab === 'memory') render();
  }

  function memoryView() {
    if (!memory) return h('div', { class: 'empty' }, 'Loading…');
    const prefs = memory.prefs.filter((p) => p.n);
    const max = Math.max(0.3, ...prefs.map((p) => p.mean));
    return h('div', null,
      h('div', { class: 'grid2' },
        h('div', null, h('div', { class: 'h3' }, 'What this audience votes on (share of viewers, across matches)'),
          prefs.length ? prefs.map((p) => h('div', { class: 'pref' }, h('span', null, p.template.replace(/_/g, ' ')), h('div', { class: 'bar' }, h('i', { style: { width: `${(100 * p.mean) / max}%` } })),
            h('span', { class: 'num' }, `${fmtPct(p.mean, 1)} · ${p.n} poll${p.n > 1 ? 's' : ''}`)))
            : h('div', { class: 'empty' }, h('b', null, 'Nothing learned yet.'), ' Every closed poll updates how its kind of question performs. Those numbers decide which poll gets the next slot.')),
        h('div', null, h('div', { class: 'h3' }, 'Lessons written down'),
          memory.lessons.length ? memory.lessons.map((l) => h('div', { class: 'lesson' }, l.text)) : h('div', { class: 'empty' }, 'A lesson is written when a kind of poll clearly beats or trails the channel average.'),
          h('div', { class: 'inject' }, h('button', { class: 'btn btn--danger', onclick: async () => { await ctx.on.resetMemory(); loadMemory(true); } }, 'Forget everything'),
            h('span', { class: 'kpi' }, 'Memory lives in the LangGraph Store and carries over to the next match.')))));
  }

  // ---------------------------------------------------------------------------------------------- pipeline
  function pipeline() {
    const lines = S.trace.slice(-90).map((e) => h('div', { class: e.kind === 'window.open' ? 'w' : '' }, h('span', { class: 't' }, `${(e.minute || '').padStart(6)} `), h('span', { class: 'k' }, `${e.kind.padEnd(16)} `), h('span', { class: 'a' }, `${(e.agent || '').padEnd(10)} `), summarize(e)));
    const box = h('div', { class: 'trace', role: 'log', 'aria-label': 'Graph activity' }, lines.length ? lines : h('div', { class: 't' }, 'Graph activity appears here as windows run.'));
    queueMicrotask(() => { box.scrollTop = box.scrollHeight; });
    const inj = (label, ev) => h('button', { class: 'btn', onclick: () => ctx.on.inject(ev) }, label);
    return h('div', null,
      h('div', { class: 'inject', style: { marginTop: 0, marginBottom: '12px' } }, h('button', { class: 'btn btn--primary', onclick: () => ctx.on.trigger() }, 'Look now'),
        h('span', { class: 'kpi' }, 'Push a feed event:'), inj('VAR check', { type: 'var.check', team: 'home', text: 'VAR check: a possible handball in the build-up.' }),
        inj('Red card', { type: 'card.red', team: 'away', player: 'the captain', text: 'RED CARD! The away captain is sent off for a second yellow.' }),
        inj('Penalty', { type: 'penalty.awarded', team: 'home', player: 'the number nine', text: 'PENALTY to the home side! A clumsy challenge in the box.', urgent: true })),
      h('div', { class: 'h3' }, 'Live trace (custom stream events from the running graphs)'), box,
      h('div', { class: 'h3' }, 'The graphs'),
      h('div', { class: 'graphs' }, ctx.boot.graphs.map((g) => h('div', { class: 'graph' }, h('h4', null, g.title), h('code', null, g.id), h('p', null, g.blurb), h('div', { class: 'chips' }, g.features.map((f) => h('span', { class: 'chip' }, f)))))),
      h('div', { class: 'inject' }, h('a', { class: 'btn', href: ctx.boot.studio.base_link, target: '_blank', rel: 'noreferrer' }, 'Open LangGraph Studio'),
        h('button', { class: 'btn', onclick: async () => { studio = await ctx.api.threads(S.matchId); render(); } }, 'Recent windows as Studio threads'),
        studio ? studio.windows.map((w) => h('a', { class: 'chip', href: w.studio, target: '_blank', rel: 'noreferrer' }, w.window_id)) : null));
  }

  function render() {
    kpis();
    mount(el.body, tab === 'rundown' ? rundown() : tab === 'inbox' ? inbox() : tab === 'ledger' ? ledger() : tab === 'memory' ? memoryView() : pipeline());
    tick(performance.now());
  }

  function tick(now) {
    for (const t of el.body.querySelectorAll('.sugg-timer')) {
      if (!t.dataset.exp) continue;
      const left = realSecondsUntil(Number(t.dataset.exp), S, now);
      t.textContent = left == null ? 'clock paused' : left <= 0 ? 'expiring…' : `expires in ${left < 10 ? left.toFixed(1) : Math.round(left)}s`;
    }
  }

  /** Repaint for a set of dirty tags; an open edit form is never clobbered by an unrelated update. */
  function update(tags) {
    kpis();
    const mine = { rundown: ['rundown'], inbox: ['suggestions', 'control'], ledger: ['actions'], memory: ['memory'], pipeline: ['trace'] }[tab];
    if (tab === 'memory' && tags.includes('memory')) return loadMemory(false);
    if (tab === 'inbox' && editing.size && !tags.includes('suggestions')) return undefined;
    if (mine.some((t) => tags.includes(t))) render();
    return undefined;
  }

  show(tab);
  return { update, tick, show };
}
