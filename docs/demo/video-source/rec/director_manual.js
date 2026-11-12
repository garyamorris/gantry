// director_manual.js: the human-in-the-loop film. Manual mode through the REAL dashboard: suggestions wait in the Inbox with a countdown, the
// human edits a poll's question and publishes it, rejects a highlight, and the ledger records who decided what.
// Run:  rec/rec --url http://127.0.0.1:2030/app/ --css 1600x900 --out raw/manual.mp4 --director rec/director_manual.js --max-seconds 200 --tail 2

const MID = () => JSON.parse(localStorage.getItem('gantry.match_id'));
const board = async () => (await fetch(`/api/matches/${MID()}`).then((r) => r.json())).scoreboard;
const mark = async (name, extra) => { const b = await board(); rec.mark(name, { minute: b.minute, vt: Math.round(b.vt), ...(extra || {}) }); };
const next = () => click('css:button[title^="Skip ahead"]', { after: 200 });
const tab = (id) => click(`css:.tab[data-tab="${id}"]`, { after: 300 });
async function view(where) {
  const top = where === 'stage' ? 0 : document.querySelector('.console').getBoundingClientRect().top + window.scrollY - 112;
  await smoothScrollBy(document.scrollingElement, top - document.scrollingElement.scrollTop, 900);
  await sleep(250);
}
const cards = () => [...document.querySelectorAll('.sugg')];
const cardOf = (kind) => cards().find((c) => c.querySelector('.sugg-kind') && c.querySelector('.sugg-kind').textContent.trim() === kind);
const goalPoll = () => cards().find((c) => c.querySelector('.sugg-kind') && c.querySelector('.sugg-kind').textContent.trim() === 'Poll' && /Tindall/.test(c.textContent));   // the poll about the goal, not the lull poll
const btn = (card, text) => [...card.querySelectorAll('button')].find((b) => b.textContent.trim() === text);

await sleep(1500);
await mark('start');
await click('css:.seg--mode button[data-v="manual"]', { after: 500 });
await click('css:button[title="3 match seconds per second"]', { after: 400 });   // 3x: a suggestion\'s countdown is long enough to read, edit and publish
await mark('manual_on');
await sleep(1500);

// ---- to the first goal; the window opens ~25 match-seconds later and three suggestions arrive
await next();
await sleep(4500);
await next();
await waitFor(async () => (await board()).vt > 686, 30000, 'goal at 12');
await mark('goal12');
await view('console');
await tab('inbox');
await waitFor(() => !!goalPoll(), 40000, 'the goal poll suggestion arrives');
await mark('inbox_cards');
await sleep(5200);                    // let the viewer read the cards and watch the countdown

// ---- edit the goal poll's question and publish it
await click(btn(goalPoll(), 'Edit'), { after: 500 });
await mark('edit_open');
const input = document.querySelector('.sugg-edit input');   // only one card is in edit mode
await typeInto(input, "Tindall's opener: how good was it?", { perChar: true, delay: 55 });
await sleep(600);
await mark('edit_typed');
await click(/^Publish edited$/, { after: 500 });
await mark('edit_published');
await view('stage');
await waitFor(() => !!document.querySelector('.poll'), 8000, 'the edited poll opens');
await mark('poll_live');
const opt = document.querySelector('.poll .opt');
await sleep(1600);
if (opt) await click(opt, { after: 400 });      // a viewer votes: the real vote endpoint
await mark('voted');
await sleep(3400);

// ---- reject the highlight; the human's decisions are the producer's memory too
await view('console');
await tab('inbox');
await sleep(900);
await click(cardOf('Highlight') ? cardOf('Highlight').querySelector('.btn--danger') : /^Reject$/, { after: 500 });
await mark('rejected');
await sleep(2200);

// ---- the ledger: who decided what, and why
await tab('ledger');
await sleep(1400);
await mark('ledger_manual');
await sleep(4500);

await mark('end');
await sleep(1200);
rec.done();
