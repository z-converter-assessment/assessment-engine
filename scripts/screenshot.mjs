// 렌더된 웹페이지를 headless chromium 으로 캡처 (dev/검증 전용 — 빌드/런타임 아님).
//
// 표현계층(P2 mapper 파생 -> P3 template -> P4 chart JS) 이 실제 브라우저에서 어떻게 그려지는지
// PNG 로 확인하기 위한 도구. Chart.js·fetch 동적 차트가 그려질 시간을 준 뒤 full-page 캡처하고,
// 페이지 콘솔 에러도 함께 수집해 표시 계층 회귀를 눈으로 검증한다.
//
// 사용:
//   node scripts/screenshot.mjs <outDir> [옵션] [url ...]
// 옵션:
//   --base <url>      기준 오리진 (기본 http://localhost:8000)
//   --server <id>     public_id — 주면 표준 페이지 세트(개요·목록·환경·상세 탭) 일괄 캡처
//   --vw <n>          뷰포트 너비 (기본 1440)
//   --vh <n>          뷰포트 높이 (기본 1000, full-page 라 스크롤 전체 캡처)
//   --settle <ms>     load 후 차트 렌더 대기 (기본 2500)
//   --scale <n>       deviceScaleFactor (기본 2 — 텍스트 선명)
// url 을 직접 나열하면 그 경로만 캡처 (--server 표준 세트 대신).

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
