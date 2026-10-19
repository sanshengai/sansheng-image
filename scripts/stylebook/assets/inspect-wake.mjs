// Our protocol check; imports the separately installed upstream runtime.
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import crypto from 'node:crypto';

const [skill, src, rawTimes] = process.argv.slice(2);
let browser;
try {
  const { chromium } = await import(pathToFileURL(path.join(skill, 'scripts/node_modules/playwright-core/index.mjs')));
  const { findChromium } = await import(pathToFileURL(path.join(skill, 'scripts/render.mjs')));
  const executablePath = findChromium();
  if (!executablePath) throw Error('No render browser; run animate wake doctor');
  browser = await chromium.launch({ executablePath, args: ['--allow-file-access-from-files'] });
  const page = await browser.newPage({ viewport: { width: 800, height: 800 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('requestfailed', r => { if (!/favicon/.test(r.url())) errors.push(r.url()); });
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  const url = pathToFileURL(path.join(src, 'index.html')).href;
  await page.goto(url);
  const cfg = await page.evaluate(() => window.CFG);
  if (!cfg || !Number.isInteger(cfg.outW) || !Number.isInteger(cfg.outH) || cfg.outW < 2 || cfg.outH < 2 ||
      !Number.isFinite(cfg.fps) || cfg.fps <= 0 || !Number.isFinite(cfg.dur) || cfg.dur <= 0)
    throw Error('Invalid window.CFG: require outW/outH/fps/dur');
  await page.setViewportSize({ width: cfg.outW, height: cfg.outH });
  await page.reload();
  await page.waitForFunction(() => window.READY === true, null, { timeout: 120000 });
  if (await page.evaluate(() => typeof window.render !== 'function')) throw Error('Missing window.render(t)');
  const times = JSON.parse(rawTimes);
  const hashes = {};
  // Seek out of order and repeat the same moments to detect accumulated state and random drawing.
  for (const t of [...times].reverse().concat(times, [...times].reverse())) {
    await page.evaluate(t => window.render(t), t);
    const digest = crypto.createHash('sha256').update(await page.screenshot({ type: 'png' })).digest('hex');
    if (hashes[t] && hashes[t] !== digest) throw Error(`Non-deterministic render(${t})`);
    hashes[t] = digest;
  }
  const fonts = await page.evaluate(() => [...document.fonts].filter(f => f.status === 'error').map(f => f.family));
  if (errors.length || fonts.length) throw Error(`Load/render errors: ${JSON.stringify({ errors, fonts })}`);
  process.stdout.write(JSON.stringify({ cfg, deterministic: true, repeated_frames: hashes }, null, 2));
} catch (e) {
  console.error(String(e.stack || e));
  process.exitCode = 2;
} finally {
  if (browser) await browser.close();
}
