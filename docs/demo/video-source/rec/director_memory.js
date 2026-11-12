// director_memory.js: the Memory tab, after the producer has watched several matches (prewarm.py). The match on screen is paused at kick-off;
// everything in the Memory tab is read from the real LangGraph Store through GET /api/memory.
// Run:  rec/rec --url http://127.0.0.1:2030/app/ --css 1600x900 --out raw/memory.mp4 --director rec/director_memory.js --max-seconds 60 --tail 1.5
const MID = () => JSON.parse(localStorage.getItem('gantry.match_id'));
const post = (path, body) => fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then((r) => r.json());
await sleep(800);
await post(`/api/matches/${MID()}/control`, { op: 'pause' });
await sleep(1200);
rec.mark('start');
const top = document.querySelector('.console').getBoundingClientRect().top + window.scrollY - 112;
await smoothScrollBy(document.scrollingElement, top - document.scrollingElement.scrollTop, 1100);
await click('css:.tab[data-tab="memory"]', { after: 600 });
rec.mark('memory');
await sleep(6500);
const box = document.querySelector('.console-body');
await smoothScrollBy(document.scrollingElement, 160, 1400);
rec.mark('memory_lessons');
await sleep(6500);
await smoothScrollBy(document.scrollingElement, -160, 1000);
await sleep(1500);
rec.mark('end');
rec.done();
