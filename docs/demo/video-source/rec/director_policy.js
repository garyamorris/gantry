// director_policy.js: the policy film. Autopilot through the first hour with the Pipeline tab open, so the screening lines scroll past
// (action.screened: PUBLISH / SUPPRESS with a reason), then the Ledger's "Held back, and why" view. Memory is WARM here (prewarm.py played
// three other matches first), so some refusals are the producer's own judgement from what this audience votes on.
// Run:  rec/rec --url http://127.0.0.1:2030/app/ --css 1600x900 --out raw/policy.mp4 --director rec/director_policy.js --max-seconds 200 --tail 2

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

await sleep(1200);
await mark('start');
await click('css:button[title="10 match seconds per second"]', { after: 300 });
await tab('pipeline');                // select the tab FIRST: it is taller, and the scroll target depends on the page height
await view('console');
await mark('pipeline');
for (let i = 0; i < 14; i++) {
  const b = await board();
  if (b.vt >= 3290) break;            // the 52' VAR drama; the next key moment is the red card
  await next();
  await view('console');             // clicking the button scrolls the page back up: put the pipeline trace back in view
  await sleep(6700);
}
await mark('pre_red');
await next();                         // to just before the red card (vt 4050)
await view('console');
await waitFor(async () => (await board()).vt > 4053, 30000, 'red card');
await mark('red_card');
await sleep(9500);                    // the window opens ~25 match-seconds later: curate, propose, studio, screen, handle
await mark('red_window');
await tab('ledger');
await sleep(1200);
await mark('ledger');
await click(/^Held back, and why$/, { after: 600 });
await mark('ledger_held');
await sleep(11000);
await click(/^Published$/, { after: 600 });
await mark('ledger_published');
await sleep(4500);
await mark('end');
rec.done();
