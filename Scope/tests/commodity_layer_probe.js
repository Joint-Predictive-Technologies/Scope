/* Measures the commodity layer on a running map, from the page's own canvas.
 *
 *   NODE_PATH=<playwright> node Scope/tests/commodity_layer_probe.js <url>
 *
 * 🔴 WHY THIS IS IN THE REPO AND NOT IN A SCRATCHPAD.  Two prior sessions in this
 * campaign reported layer measurements — the zero-ink-overlap count and the
 * frame-bisect — from instruments that lived only in a session scratchpad. Both
 * had to be recorded as UNVERIFIED because the verifier could not re-run them.
 * The numbers below are the ones that justified re-tuning this layer on
 * 2026-09-03, so the instrument ships with them.
 *
 * It is NOT a pytest: it needs a browser and a served map. It asserts, exits
 * non-zero on failure, and prints what it measured either way.
 *
 * ⚠️ It measures the COMMODITY CANVAS'S OWN ALPHA, not a screenshot. A screenshot
 * diff cannot separate the layer from the basemap under it, and during this
 * session a screenshot read taken mid-container-swap produced a confident, wrong
 * conclusion. `a32` = share of viewport at alpha >= 32, the point at which ink is
 * clearly visible rather than merely present.
 */
'use strict';
const { chromium } = require('playwright');

const URL = process.argv[2] || 'http://localhost:8792/';

/* Baselines measured 2026-09-03 at 1440x900, national view, both families on.
 * The shipped-before values are kept so a regression reads as a regression. */
const BEFORE = { a32: 0.225, mode: 'density' };
/* 🔴 THIS FLOOR WAS 0.45 AND COULD NOT CATCH THE DEFECT IT NAMES.  Re-running the
 * probe with the OLD exposure constants restored measured a32 = 0.481 — over that
 * floor — because the per-family fix ALONE lifts a32 from 0.225 to 0.481. A floor
 * of 0.45 therefore tested the per-family fix twice and the exposure not at all.
 * 0.55 sits above the old-exposure reading and below the measured 0.645. */
const MIN_A32 = 0.55;
const MAX_REDRAW_MS = 40;  /* the fix measured 7.4 median / 13.8 worst */

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  await p.goto(URL, { waitUntil: 'networkidle', timeout: 60000 });
  await p.waitForTimeout(3500);

  const amber = () => p.evaluate(() => {
    const o = [];
    document.querySelectorAll('path').forEach(e => {
      if ((e.getAttribute('fill') || '').toUpperCase() === '#E8A33D') {
        const v = e.getAttribute('fill-opacity');
        if (v != null) o.push(+v);
      }
    });
    o.sort((x, y) => y - x);
    return { n: o.length, max: o[0] };
  });

  const ink = () => p.evaluate(async () => {
    const M = window.__mapdiag;
    M.draw();
    await new Promise(r => setTimeout(r, 700));
    const c = document.querySelector('canvas.commodity-canvas');
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    let a32 = 0, a80 = 0, max = 0;
    for (let i = 3; i < d.length; i += 4) {
      const a = d[i];
      if (a >= 32) a32++;
      if (a >= 80) a80++;
      if (a > max) max = a;
    }
    const t = c.width * c.height;
    return { mode: M.mode, peak: +M.densityPeak.toFixed(3),
             a32: +(100 * a32 / t).toFixed(3), a80: +(100 * a80 / t).toFixed(3), max };
  });

  const cost = () => p.evaluate(() => {
    const M = window.__mapdiag, t = [];
    for (let i = 0; i < 8; i++) { const s = performance.now(); M.draw(); t.push(performance.now() - s); }
    t.sort((a, b) => a - b);
    return { median: +t[4].toFixed(1), worst: +t[7].toFixed(1) };
  });

  const fail = [];
  const off1 = await amber();
  await p.click('#com-toggle');
  await p.waitForTimeout(4500);
  const on = await amber(), k = await ink(), c = await cost();
  await p.click('#com-toggle');
  await p.waitForTimeout(2500);
  const off2 = await amber();

  console.log('dot amber fill  OFF %s -> ON %s -> OFF %s', off1.max, on.max, off2.max);
  console.log('layer           %j', k);
  console.log('redraw ms       %j', c);
  console.log('before-state    a32 %s (%s)', BEFORE.a32, BEFORE.mode);

  /* 🔴 THE MODE IS PER FAMILY. One mode chosen from the COMBINED count put the 270
     scattered mineral sites into a density field of their own sparseness and they
     vanished; only turning oil/gas off could reveal a mine. */
  /* ⚠️ AND THIS TESTED THE SEPARATOR, NOT THE BEHAVIOUR.  Forcing one mode for the
     whole layer still yields "oil gas density · commodity density", which matched.
     The requirement is that the SPARSE family is not rendered as a density field
     of its own sparseness, so name it. */
  if (!/commodity points/.test(k.mode)) fail.push('the sparse family is not in points mode: ' + k.mode);
  if (k.a32 < MIN_A32) fail.push(`a32 ${k.a32}% below the ${MIN_A32}% floor (before-state was ${BEFORE.a32}%)`);
  /* the §1 figure/ground swap: dots recede so the substrate can be read */
  if (!(on.max < off1.max * 0.5)) fail.push(`dots did not dim: ${off1.max} -> ${on.max}`);
  /* and it must come back exactly — the first version multiplied in place and
     never restored, so one toggle dimmed a ring for the rest of the session */
  if (Math.abs(off2.max - off1.max) > 1e-9) fail.push(`dimming not restored: ${off1.max} -> ${off2.max}`);
  if (c.worst > MAX_REDRAW_MS) fail.push(`redraw worst ${c.worst}ms over ${MAX_REDRAW_MS}ms`);
  if (errs.length) fail.push('page errors: ' + errs.slice(0, 3).join(' | '));

  await b.close();
  if (fail.length) { console.error('\n🔴 FAIL\n  - ' + fail.join('\n  - ')); process.exit(1); }
  console.log('\n✅ commodity layer probe passed');
})().catch(e => { console.error(e); process.exit(1); });
