// Toolbar / status-bar / notifications / segment delete.
import { setup, assert, summary } from './_helpers.mjs';

const { browser, page, errors } = await setup();

// (1) Side-resizer drags the right-hand panel within bounds.
const widthBefore = await page.evaluate(() =>
  document.getElementById('side').getBoundingClientRect().width);
const handle = await page.$eval('#side-resizer', (el) => {
  const r = el.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
});
await page.mouse.move(handle.x, handle.y);
await page.mouse.down();
await page.mouse.move(handle.x - 100, handle.y, { steps: 5 });
await page.mouse.up();
const widthAfter = await page.evaluate(() =>
  document.getElementById('side').getBoundingClientRect().width);
assert(widthAfter > widthBefore + 50,
       `dragging resizer left widens the side panel (${widthBefore}→${widthAfter})`);

// (2) Coordinate readout snaps to the cursor and switches to a
// start→end pair while a box-select is in flight.
await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  state.tool = 'select';
  state.pan = { x: 200, y: 200 }; state.zoom = 1;
  render();
});
const wrap = await page.$eval('#canvas-wrap', (el) => {
  const r = el.getBoundingClientRect();
  return { left: r.left, top: r.top };
});
await page.mouse.move(wrap.left + 300, wrap.top + 280, { steps: 4 });
await new Promise(r => setTimeout(r, 50));
const readout = await page.$eval('#coords', (el) => ({
  text: el.textContent || '', hidden: el.classList.contains('hidden'),
}));
assert(!readout.hidden && readout.text === '(100, 80)',
       `coords reads the snapped cursor (got "${readout.text}")`);

await page.mouse.down();
await page.mouse.move(wrap.left + 460, wrap.top + 380, { steps: 4 });
const boxText = await page.$eval('#coords', (el) => el.textContent || '');
await page.mouse.up();
assert(/^\(\d+, \d+\)\s*→\s*\(\d+, \d+\)$/.test(boxText),
       `box-select readout shows start→end (got "${boxText}")`);

// (3) Notifications: info auto-dismisses, warn is sticky with [×].
const noteState = await page.evaluate(async () => {
  document.getElementById('notifications').innerHTML = '';
  const info = notify('Hello info', 'info');
  const warn = notify('Heads up', 'warn');
  const infoHasClose = !!info.querySelector('.notification-close');
  const warnHasClose = !!warn.querySelector('.notification-close');
  warn.querySelector('.notification-close').click();
  const warnGone = !document.body.contains(warn);
  await new Promise(r => setTimeout(r, 3700));
  const infoGone = !document.body.contains(info);
  return { infoHasClose, warnHasClose, warnGone, infoGone };
});
assert(!noteState.infoHasClose, 'info toast has no close button (auto-dismiss)');
assert(noteState.warnHasClose, 'warn toast has a close button (sticky)');
assert(noteState.warnGone, 'click on [×] dismisses the warn toast');
assert(noteState.infoGone, 'info toast auto-dismisses after timeout');

// (4) Clear-all empties the stack.
const clearAll = await page.evaluate(() => {
  document.getElementById('notifications').innerHTML = '';
  refreshClearAllButton();
  notify('a', 'warn'); notify('b', 'error');
  const btn = document.getElementById('notifications-clear-all');
  const visibleBefore = !btn.hidden;
  btn.click();
  return { visibleBefore,
           hiddenAfter: btn.hidden,
           remaining: document.getElementById('notifications').children.length };
});
assert(clearAll.visibleBefore && clearAll.hiddenAfter && clearAll.remaining === 0,
       'clear-all removes every queued toast and re-hides itself');

// (5) Segment delete: marking one segment of a 3-segment wire splits
// the wire into head + tail.
const split = await page.evaluate(() => {
  state.parts = []; state.wires = []; state.nameCounters = {}; state.nextId = 1;
  state.selectedIds.clear(); state.selectedSegments.clear();
  state.tool = 'select';
  state.wires.push({ id: 'W1',
    points: [[0, 0], [80, 0], [80, -80], [160, -80]] });
  state.nextId = 2;
  state.selectedIds.add('W1');
  state.selectedSegments.add('W1|1');
  pushHistory(); render();
  document.dispatchEvent(new KeyboardEvent('keydown',
    { key: 'Delete', bubbles: true }));
  return state.wires.map(w => w.points).sort((a, b) =>
    JSON.stringify(a).localeCompare(JSON.stringify(b)));
});
assert(split.length === 2, `mid-segment delete splits wire (got ${split.length})`);
assert(JSON.stringify(split[0]) === '[[0,0],[80,0]]'
       && JSON.stringify(split[1]) === '[[80,-80],[160,-80]]',
       `head + tail fragments preserved (got ${JSON.stringify(split)})`);

await browser.close();
process.exit(summary(errors));
