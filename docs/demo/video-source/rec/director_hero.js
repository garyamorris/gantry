// director_hero.js: the autopilot film. Drives the REAL dashboard against the REAL server like a person would (a fake cursor clicks the real controls).
// Run:  rec/rec --url http://127.0.0.1:2030/app/ --css 1600x900 --out raw/hero.mp4 --director rec/director_hero.js --max-seconds 420 --tail 2
// Marks (rec.mark) are written to raw/hero.mp4.markers.json; make_manifest.py cuts the scenes from them.
// Nothing is mocked: every poll, highlight and held message on screen was decided by the graphs, offline model, autopilot mode.

const MID = () => JSON.parse(localStorage.getItem('gantry.match_id'));
const post = (path, body) => fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) }).then((r) => r.json());
const board = async () => (await fetch(`/api/matches/${MID()}`).then((r) => r.json())).scoreboard;
const mark = async (name, extra) => { const b = await board(); rec.mark(name, { minute: b.minute, vt: Math.round(b.vt), score: b.score, ...(extra || {}) }); };
const speed = (x) => click(`css:button[title="${x} match seconds per second"]`, { after: 250 });
const next = () => click('css:button[title^="Skip ahead"]', { after: 200 });
const tab = (id) => click(`css:.tab[data-tab="${id}"]`, { after: 300 });
const pollOpen = () => !!document.querySelector('.poll:not(.is-closed)');
const pollClosed = () => !!document.querySelector('.poll.is-closed') || !document.querySelector('.poll');
async function view(where) {
  const top = where === 'stage' ? 0 : document.querySelector('.console').getBoundingClientRect().top + window.scrollY - 112;
  await smoothScrollBy(document.scrollingElement, top - document.scrollingElement.scrollTop, 900);
  await sleep(250);
}
async function jumpAndWatch(label, ms = 9000) {
  await next();
  await sleep(ms);
  await mark(label);
}

// ------------------------------------------------------------------------------------------------------------------ a cold producer
await post('/api/memory/reset');
await sleep(1200);
await mark('start');
await speed(10);
await sleep(2200);

// ------------------------------------------------------------------------------------------------------- intro: the first goal and its poll
await next();                         // to the 4' chance
await sleep(5200);
await mark('chance4');
await next();                         // to just before the 12' goal
await waitFor(async () => (await board()).vt > 686, 20000, 'goal at 12');   // the goal is released at vt 680
await mark('goal12');
await waitFor(pollOpen, 16000, 'first poll opens');
await mark('poll12_open');
await sleep(5200);                    // votes arriving
await waitFor(pollClosed, 20000, 'first poll closes');
await mark('poll12_closed');
const closedAt = (await board()).vt;
await sleep(1800);

// ------------------------------------------------------------------------------------------------- a producer's own feed: push a penalty
await view('console');
await tab('pipeline');
await mark('pipeline_open');
await waitFor(async () => (await board()).vt > 935, 30000, 'past the quiet-spell window');   // a clear gap since the last poll, and not mid-window
await speed(3);                                                                               // slow enough to watch the urgent window and its poll
await mark('inject_ready');
await click(/^Penalty$/, { after: 300 });
await mark('inject_penalty');
await sleep(3000);                                                                            // window.open (urgent), the ranking and the poll brief scroll past
await mark('inject_trace');
await view('stage');
await waitFor(pollOpen, 20000, 'penalty poll opens');
await mark('inject_poll_open');
await sleep(5500);
await waitFor(pollClosed, 40000, 'penalty poll closes');
await mark('inject_poll_closed');
await speed(10);
await sleep(600);

// --------------------------------------------------------------------------------------------- through the match to the red card, pipeline open
await view('console');
await tab('pipeline');
await sleep(500);
for (let i = 0; i < 12; i++) {
  const b = await board();
  if (b.vt >= 3290) break;            // the last jump landed on the 52' VAR drama (vt 3310): the next key moment is the red card
  await next();
  await sleep(i < 3 ? 6500 : 7200);
}
await mark('red_approach');
await next();                         // exactly one jump: to 12 match-seconds before the red card (vt 4050)
await waitFor(async () => (await board()).vt > 4053, 30000, 'red card');
await mark('red_card');
await sleep(7500);                    // the window opens ~25 match-seconds later; the trace fills
await mark('red_window');
await tab('rundown');
await sleep(2600);
await mark('rundown63');
await view('stage');
await sleep(500);
await view('console');

// ------------------------------------------------------------------------------------------------------------ the chat: Top chat vs Live chat
await click(/^Live chat$/, { tag: 'button', after: 500 });
await mark('live_chat');
await sleep(3800);
await click(/^Top chat$/, { tag: 'button', after: 500 });
await mark('top_chat');
await sleep(3200);

// ----------------------------------------------------------------------------------------------------------- the ledger: what it did and why not
await tab('ledger');
await sleep(1500);
await mark('ledger');
await click(/^Held back, and why$/, { after: 600 });
await mark('ledger_held');
await sleep(4200);

// -------------------------------------------------------------------------------------------------- the rest of the match, a little faster
await speed(20);
await view('stage');
for (let i = 0; i < 10; i++) {
  const b = await board();
  if (b.ended || b.vt > 5900) break;
  await next();
  await sleep(7400);
}
await mark('late_goal');
await waitFor(async () => (await board()).ended, 120000, 'full time');
await mark('ended');
await sleep(2500);
await view('console');
await tab('memory');
await sleep(2000);
await mark('memory');
await sleep(5200);
await tab('ledger');
await click(/^Everything/, { after: 400 });
await sleep(1500);
await mark('ledger_final');
await sleep(3500);
await view('stage');
await mark('end_stage');
await sleep(2500);
rec.done();
