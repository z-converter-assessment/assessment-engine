// 외부 참조 사이트의 테마를 흡수 (dev/디자인 전용) — OpenStack Horizon 로그인 후 렌더 캡처 + 디자인 토큰 추출.
//
// 우리 Assessment 포털을 참조 대시보드(Horizon)의 룩에 맞추기 위한 도구. 로그인 -> 대표 페이지 스크린샷 +
// computed style 에서 팔레트·타이포·컴포넌트 토큰을 뽑아 theme spec 초안을 만든다.
//
// 사용:
//   node scripts/theme-capture.mjs <outDir> --base http://HOST [--user U --pass P] [path ...]

import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

function parseArgs(argv) {
  const opts = { base: null, user: 'assessment', pass: 'assessment', vw: 1440, vh: 1000, settle: 2500, scale: 2 };
  const paths = [];
  let outDir = null;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--base') opts.base = argv[++i];
    else if (a === '--user') opts.user = argv[++i];
    else if (a === '--pass') opts.pass = argv[++i];
    else if (a === '--vw') opts.vw = Number(argv[++i]);
    else if (a === '--vh') opts.vh = Number(argv[++i]);
    else if (!outDir) outDir = a;
    else paths.push(a);
  }
  return { opts, outDir, paths };
}

const EXTRACT = () => {
  const out = {};
  const cs = getComputedStyle(document.body);
  out.body = { fontFamily: cs.fontFamily, fontSize: cs.fontSize, lineHeight: cs.lineHeight, color: cs.color, background: cs.backgroundColor };

  // :root / 규칙 내 CSS custom property 수집.
  const vars = {};
  for (const sheet of Array.from(document.styleSheets)) {
    let rules;
    try { rules = sheet.cssRules; } catch { continue; }
    if (!rules) continue;
    for (const rule of Array.from(rules)) {
      if (rule.style) {
        for (const prop of Array.from(rule.style)) {
          if (prop.startsWith('--')) vars[prop] = rule.style.getPropertyValue(prop).trim();
        }
      }
    }
  }
  out.cssVars = vars;

  // 팔레트 빈도 — 실제로 많이 쓰이는 색을 상위 추출.
  const colorCount = {}, bgCount = {}, borderCount = {}, fontCount = {};
  let n = 0;
  for (const el of document.querySelectorAll('*')) {
    if (n++ > 6000) break;
    const s = getComputedStyle(el);
    const add = (o, v) => { if (v && v !== 'rgba(0, 0, 0, 0)' && v !== 'transparent') o[v] = (o[v] || 0) + 1; };
    add(colorCount, s.color);
    add(bgCount, s.backgroundColor);
    if (s.borderTopWidth !== '0px') add(borderCount, s.borderTopColor);
    if (s.fontFamily) fontCount[s.fontFamily] = (fontCount[s.fontFamily] || 0) + 1;
  }
  const top = (o, k = 14) => Object.entries(o).sort((a, b) => b[1] - a[1]).slice(0, k);
  out.topColors = top(colorCount);
  out.topBackgrounds = top(bgCount);
  out.topBorders = top(borderCount);
  out.topFonts = top(fontCount, 6);

  const sample = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const s = getComputedStyle(el);
    return {
      sel, color: s.color, background: s.backgroundColor,
      border: `${s.borderTopWidth} ${s.borderTopStyle} ${s.borderTopColor}`,
      radius: s.borderRadius, boxShadow: s.boxShadow,
      font: s.fontFamily, size: s.fontSize, weight: s.fontWeight,
      padding: s.padding, margin: s.margin,
    };
  };
  out.components = {
    link: sample('a'),
    button: sample('button, .btn'),
    primaryBtn: sample('.btn-primary, button[type=submit], .btn.btn-primary'),
    h1: sample('h1, .page-header h1, .h1'),
    h2: sample('h2, .h2'),
    navbar: sample('.navbar, header.navbar, #main_content .navbar, nav.navbar'),
    sidebar: sample('.nav-sidebar, #sidebar, .sidebar, nav#sidebar'),
    tableHeader: sample('table thead th, .table thead th, th'),
    tableRow: sample('table tbody td, .table tbody td, td'),
    panel: sample('.panel, .card, .dashboard-block, .detail'),
    panelHeading: sample('.panel-heading, .card-header, .panel-title'),
    badge: sample('.label, .badge, .status_up, .status_down'),
    input: sample('input.form-control, .form-control, input[type=text]'),
  };
  return out;
};

async function main() {
  const { opts, outDir, paths } = parseArgs(process.argv.slice(2));
  if (!outDir || !opts.base) {
    console.error('usage: node scripts/theme-capture.mjs <outDir> --base http://HOST [--user U --pass P] [path ...]');
    process.exit(2);
  }
  mkdirSync(outDir, { recursive: true });

  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: opts.vw, height: opts.vh }, deviceScaleFactor: opts.scale });
  const page = await context.newPage();
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));

  // 로그인 (Horizon Django 폼) — CSRF 는 폼 로드로 자동 처리.
  await page.goto(new URL('/auth/login/', opts.base).href, { waitUntil: 'load', timeout: 20000 });
  await page.fill('#id_username', opts.user);
  await page.fill('#id_password', opts.pass);
  await Promise.all([
    page.waitForLoadState('load', { timeout: 20000 }).catch(() => {}),
    page.click('button[type=submit], input[type=submit], #loginBtn'),
  ]);
  await page.waitForTimeout(opts.settle);

  const shots = [];
  const targets = paths.length ? paths : ['/'];
  for (const p of targets) {
    const url = new URL(p, opts.base).href;
    try {
      await page.goto(url, { waitUntil: 'load', timeout: 20000 });
      await page.waitForTimeout(opts.settle);
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await page.waitForTimeout(300);
      await page.evaluate(() => window.scrollTo(0, 0));
      const name = (p.replace(/^\/+/, '').replace(/\/+$/, '').replace(/[^a-zA-Z0-9]+/g, '-') || 'landing');
      const file = join(outDir, `ref-${name}.png`);
      await page.screenshot({ path: file, fullPage: true });
      shots.push({ url, file });
    } catch (e) {
      shots.push({ url, error: String(e) });
    }
  }

  // 토큰은 landing(첫 페이지)에서 추출 — nav·sidebar·table·card 가 다 있는 화면.
  const tokens = await page.evaluate(EXTRACT);
  const tokenFile = join(outDir, 'tokens.json');
  writeFileSync(tokenFile, JSON.stringify({ base: opts.base, finalUrl: page.url(), tokens, errors }, null, 2));

  await browser.close();

  console.log('final url:', page.url());
  for (const s of shots) console.log(s.error ? `FAIL ${s.url} ${s.error}` : `shot ${s.file}`);
  console.log('tokens:', tokenFile);
  console.log('--- body ---', JSON.stringify(tokens.body));
  console.log('--- topBackgrounds ---', JSON.stringify(tokens.topBackgrounds));
  console.log('--- topColors ---', JSON.stringify(tokens.topColors));
  console.log('--- topFonts ---', JSON.stringify(tokens.topFonts));
  console.log('--- cssVars (count) ---', Object.keys(tokens.cssVars).length);
}

main().catch((e) => { console.error(e); process.exit(1); });
