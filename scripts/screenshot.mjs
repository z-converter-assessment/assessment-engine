// 페이지를 headless chromium 으로 열어 full-page PNG 로 찍고 콘솔 에러를 걷는다.
// 사용법·옵션·설치 전제는 docs/guides/local-dev.md "화면 캡처" 절.

import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

function parseArgs(argv) {
  const opts = { base: 'http://localhost:8000', server: null, vw: 1440, vh: 1000, settle: 2500, scale: 2 };
  const urls = [];
  let outDir = null;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--base') opts.base = argv[++i];
    else if (a === '--server') opts.server = argv[++i];
    else if (a === '--vw') opts.vw = Number(argv[++i]);
    else if (a === '--vh') opts.vh = Number(argv[++i]);
    else if (a === '--settle') opts.settle = Number(argv[++i]);
    else if (a === '--scale') opts.scale = Number(argv[++i]);
    else if (!outDir) outDir = a;
    else urls.push(a);
  }
  return { opts, outDir, urls };
}

function standardPages(serverId) {
  const pages = [
    ['overview', '/'],
    ['servers', '/servers'],
    ['env-metrics', '/environment/metrics'],
    ['env-realtime', '/environment/realtime'],
    ['env-topology', '/environment/topology'],
    ['env-assessment', '/environment/assessment'],
  ];
  if (serverId) {
    for (const tab of ['', '/cpu', '/memory', '/network', '/storage', '/services', '/metrics']) {
      const name = 'detail' + (tab ? tab.replace('/', '-') : '-overview');
      pages.push([name, `/servers/${serverId}${tab}`]);
    }
  }
  return pages;
}

function slug(path) {
  const s = path.replace(/^\/+/, '').replace(/\/+$/, '').replace(/[^a-zA-Z0-9]+/g, '-');
  return s || 'root';
}

async function main() {
  const { opts, outDir, urls } = parseArgs(process.argv.slice(2));
  if (!outDir) {
    console.error('usage: node scripts/screenshot.mjs <outDir> [--server <public_id>] [url ...]');
    process.exit(2);
  }
  mkdirSync(outDir, { recursive: true });

  let targets;
  if (urls.length > 0) {
    targets = urls.map((u) => [slug(new URL(u, opts.base).pathname), u]);
  } else {
    targets = standardPages(opts.server);
  }

  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: opts.vw, height: opts.vh },
    deviceScaleFactor: opts.scale,
  });

  const results = [];
  for (const [name, path] of targets) {
    const url = new URL(path, opts.base).href;
    const page = await context.newPage();
    const consoleErrors = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });
    page.on('pageerror', (err) => consoleErrors.push('pageerror: ' + err.message));

    let status = null;
    try {
      const resp = await page.goto(url, { waitUntil: 'load', timeout: 20000 });
      status = resp ? resp.status() : null;
      // 차트(Chart.js 애니메이션 + fetch 동적 로더)가 그려질 시간 확보.
      await page.waitForTimeout(opts.settle);
      // full-page 캡처 전 스크롤로 lazy 렌더 트리거.
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await page.waitForTimeout(400);
      await page.evaluate(() => window.scrollTo(0, 0));
      const file = join(outDir, `${name}.png`);
      await page.screenshot({ path: file, fullPage: true });
      results.push({ name, url, status, file, errors: consoleErrors });
    } catch (e) {
      results.push({ name, url, status, error: String(e), errors: consoleErrors });
    } finally {
      await page.close();
    }
  }

  await browser.close();

  for (const r of results) {
    const tag = r.error ? 'FAIL' : `${r.status}`;
    console.log(`[${tag}] ${r.name.padEnd(18)} ${r.url}`);
    if (r.file) console.log(`         -> ${r.file}`);
    if (r.error) console.log(`         error: ${r.error}`);
    for (const e of r.errors) console.log(`         console: ${e}`);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
