// 14d는 right-sizing 윈도우(right_sizing.WINDOW_DAYS)와 동일 — 보고서·대시보드·차트 일관.
// 14일 → 6시간 버킷 자동 매핑 (14*24/6 = 56 데이터 포인트, 가독성·표시 부담 균형).
/** @type {Record<string, string>} */
const RANGE_LABEL  = { '15m':'15분', '1h':'1시간', '6h':'6시간', '24h':'1일', '7d':'7일', '14d':'14일', '30d':'30일' };
/** @type {Record<string, string>} */
const AUTO_BUCKET  = { '15m':'1m', '1h':'5m', '6h':'15m', '24h':'30m', '7d':'3h', '14d':'6h', '30d':'12h' };
/** @type {Record<string, string>} */
const BUCKET_LABEL = { '1m':'1분 집계', '5m':'5분 집계', '15m':'15분 집계', '30m':'30분 집계',
                       '1h':'1시간 집계', '3h':'3시간 집계', '6h':'6시간 집계', '12h':'12시간 집계', '1d':'1일 집계' };
/** @type {Record<string, number>} */
const RANGE_MS  = { '15m':9e5, '1h':36e5, '6h':216e5, '24h':864e5, '7d':6048e5, '14d':12096e5, '30d':2592e6 };
/** @type {Record<string, number>} */
const BUCKET_MS = { '1m':6e4, '5m':3e5, '15m':9e5, '30m':18e5, '1h':36e5, '3h':108e5, '6h':216e5, '12h':432e5, '1d':864e5 };

// 테마색1 (base.html :root --color-title) — JS 차트 시리즈가 CSS 변수를 추종.
// getComputedStyle 실패·빈 값 시 #2563eb fallback (--color-title 현행값과 동일 — 회귀 0 보장).
function themeColor() {
  try {
    var v = getComputedStyle(document.documentElement).getPropertyValue('--color-title').trim();
    return v || '#2563eb';
  } catch (e) { return '#2563eb'; }
}
const COLORS = [themeColor(),'#f59e0b','#22c55e','#ef4444','#8b5cf6','#06b6d4','#f97316','#ec4899'];

/** @param {string} isoStr */
function fmtKst(isoStr) {
  const d = new Date(isoStr);
  const kst = new Date(d.getTime() + 9 * 60 * 60 * 1000);
  return kst.toISOString().replace('T', ' ').slice(0, 19);
}

/**
 * @param {string} ts
 * @param {string} range
 */
function fmtLabel(ts, range) {
  const d = new Date(new Date(ts).getTime() + 9 * 60 * 60 * 1000);
  const MM = String(d.getUTCMonth() + 1).padStart(2, '0');
  const DD = String(d.getUTCDate()).padStart(2, '0');
  const HH = String(d.getUTCHours()).padStart(2, '0');
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  if (range === '30d') return `${MM}/${DD} ${HH}:00`;
  if (range === '7d')  return `${MM}/${DD} ${HH}:${mm}`;
  return `${HH}:${mm}`;
}

/** @param {number | null} v */
function fmtKbChart(v) {
  if (v == null) return '';
  if (v >= 1024 * 1024) return (v / 1024 / 1024).toFixed(1) + ' MB/s';
  if (v >= 1024) return (v / 1024).toFixed(1) + ' kB/s';
  return v.toFixed(0) + ' B/s';
}

/** @param {string} inputId */
function getAnchorEnd(inputId) {
  const val = /** @type {HTMLInputElement} */ (document.getElementById(inputId)).value;
  if (!val) return null;
  const d = new Date(val + ':00+09:00');
  return d >= new Date() ? null : d;
}

/** @param {string} inputId */
function initAnchor(inputId) {
  const input = /** @type {HTMLInputElement} */ (document.getElementById(inputId));
  const kstNow = new Date().toLocaleString('sv-SE', { timeZone: 'Asia/Seoul' }).slice(0, 16).replace(' ', 'T');
  input.max = kstNow;
  input.value = kstNow;
}

/**
 * @param {string} rangeKey
 * @param {string} bucketKey
 * @param {Date | null} [anchorEnd]
 */
function makeBucketGrid(rangeKey, bucketKey, anchorEnd) {
  const endMs = (anchorEnd || new Date()).getTime();
  const bMs   = BUCKET_MS[bucketKey];
  const start = endMs - RANGE_MS[rangeKey];
  const first = Math.ceil(start / bMs) * bMs;
  const grid  = [];
  for (let t = first; t <= endMs; t += bMs) grid.push(t);
  return grid;
}

/**
 * @param {number[]} grid
 * @param {any[]} rows
 * @param {number} bMs
 */
function joinToGrid(grid, rows, bMs) {
  const map = /** @type {Record<number, any>} */ ({});
  for (const r of rows) {
    const t = Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs;
    map[t] = r.value;
  }
  return grid.map(t => map[t] ?? null);
}

/**
 * @param {any[]} rows
 * @param {number} bMs
 * @param {number[]} grid
 * @param {any} [metaMap]
 * @param {any} [opts]
 */
function buildDimDatasets(rows, bMs, grid, metaMap = {}, opts = {}) {
  /** @type {(v: any) => any} */
  const valueFn = opts.valueFn || (v => v);
  const pointRadius = opts.pointRadius ?? 0;
  const byDim = /** @type {Record<string, any[]>} */ ({});
  for (const r of rows) { (byDim[r.dimension] = byDim[r.dimension] || []).push(r); }
  return Object.entries(byDim).map(([dim, pts]) => {
    const map = /** @type {Record<number, any>} */ ({});
    for (const p of pts) { map[Math.floor(new Date(p.collected_at).getTime() / bMs) * bMs] = valueFn(p.value); }
    const meta  = metaMap[dim] || { label: dim, color: '#8b5cf6' };
    const color = meta.color || '#8b5cf6';
    return {
      label: meta.label || dim,
      data: grid.map(t => map[t] ?? null),
      borderColor: color, backgroundColor: color + '22',
      borderWidth: 2, pointRadius, pointHoverRadius: 3,
      tension: 0.3, fill: false, spanGaps: false,
    };
  });
}

// 입력 단위는 kB/s이며 fmtKbChart의 B/s 계약과 다르다.
/** @param {number | null | undefined} kb */
function fmtThroughput(kb) {
  if (kb == null) return '—';
  return kb >= 1024 ? (kb / 1024).toFixed(1) + ' MB/s' : kb.toFixed(1) + ' kB/s';
}

/**
 * @param {string} groupId
 * @param {(val: any) => void} onChange
 */
function bindToggle(groupId, onChange) {
  const el = document.getElementById(groupId);
  if (!el) return;
  if (el.tagName === 'SELECT') {
    el.addEventListener('change', e => onChange(/** @type {HTMLSelectElement} */ (e.target).value));
    return;
  }
  el.addEventListener('click', e => {
    const btn = /** @type {Element} */ (e.target).closest('.toggle');
    if (!btn) return;
    el.querySelectorAll('.toggle').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    onChange(/** @type {HTMLElement} */ (btn).dataset.val);
  });
}

// 한 range 토글 + 한 anchor 가 페이지의 모든 차트를 구동 — 차트별 파편 컨트롤 대체. 신호 간 시점 상관
// 관측(이 호스트를 이 창·이 시점으로). anchor 미입력=live now, 입력=고정(과거 사건 조사). 변경 시 onChange 로 전체 reload.
/**
 * @param {string} rangeBtnsId
 * @param {string} anchorId
 * @param {string} defaultRange
 * @param {() => void} onChange
 * @returns {{ getRange: () => string, getAnchor: () => Date | null }}
 */
function pageTimeControl(rangeBtnsId, anchorId, defaultRange, onChange) {
  let range = defaultRange;
  initAnchor(anchorId);
  bindToggle(rangeBtnsId, (/** @type {string} */ v) => { range = v; onChange(); });
  const anchorEl = document.getElementById(anchorId);
  if (anchorEl) anchorEl.addEventListener('change', () => onChange());
  return { getRange: () => range, getAnchor: () => getAnchorEnd(anchorId) };
}

// 탭 비활성(document.hidden) 시 tick skip — 다중 탭에서 누적 폴링 요청 차단(서버 부하 감소).
// 숨겨졌다 다시 보이면 즉시 1회 refresh 해 멈춰있던 화면 보정 (loader 의 seq 가드가 중복 응답 흡수).
/**
 * @param {() => void} onRefresh
 * @param {number} [intervalMs]
 */
function initAutoRefresh(onRefresh, intervalMs = 30_000) {
  const id = setInterval(() => { if (!document.hidden) onRefresh(); }, intervalMs);
  const onVisible = () => { if (!document.hidden) onRefresh(); };
  document.addEventListener('visibilitychange', onVisible);
  window.addEventListener('pagehide', () => {
    clearInterval(id);
    document.removeEventListener('visibilitychange', onVisible);
  });
  return id;
}

/** @param {any} arr */
function safeArray(arr) { return Array.isArray(arr) ? arr : []; }

// Windows 에이전트가 측정하지 않는 축은 값과 무관하게 N/A로 표시한다.
const WIN_NA_KEYS = new Set(['cpu_iowait', 'cpu_steal', 'cpu_nice', 'mem_buffers', 'mem_cached']);
/**
 * @param {string | null} osFamily
 * @param {string} key
 * @param {string} formatted
 */
function naWindows(osFamily, key, formatted) {
  return osFamily === 'windows' && WIN_NA_KEYS.has(key) ? 'N/A' : formatted;
}

const _NOT_MEASURED = new Set(['—', 'N/A']);
/**
 * @param {HTMLElement | null} el
 * @param {string} text
 */
function setValText(el, text) {
  if (!el) return;
  el.textContent = text;
  el.classList.toggle('sat-val-muted', _NOT_MEASURED.has(text));
}

/**
 * @param {HTMLElement | null} el
 * @param {string | null} osFamily
 * @param {string} key
 * @param {string} formatted
 */
function setNaText(el, osFamily, key, formatted) {
  setValText(el, naWindows(osFamily, key, formatted));
}

/**
 * @param {any[]} avgRows
 * @param {any[]} maxRows
 * @param {number} bMs
 * @param {number[]} grid
 * @param {any} [opts]
 */
function buildAvgMaxDatasets(avgRows, maxRows, bMs, grid, opts = {}) {
  // 1분 버킷은 max와 avg가 같아 음영을 만들지 않는다.
  if (bMs <= BUCKET_MS['1m']) maxRows = [];
  const dims = [...new Set([...avgRows, ...maxRows].map(r => r.dimension || ''))];
  const datasets = /** @type {any[]} */ ([]);
  dims.forEach((dim, i) => {
    const color = opts.color || COLORS[i % COLORS.length];
    const dash  = opts.dashFn ? opts.dashFn(dim) : [];
    const avgMap = /** @type {Record<number, any>} */ ({}), maxMap = /** @type {Record<number, any>} */ ({});
    for (const r of avgRows) if ((r.dimension || '') === dim)
      avgMap[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;
    for (const r of maxRows) if ((r.dimension || '') === dim)
      maxMap[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;
    const avgData         = grid.map(t => avgMap[t] ?? null);
    const realMaxData     = grid.map(t => maxMap[t] ?? null);
    const bufferedMaxData = grid.map(t => avgMap[t] == null ? null : (maxMap[t] ?? avgMap[t]));
    const baseLabel = opts.label || dim || 'Value';
    datasets.push({
      label: baseLabel, data: avgData,
      borderColor: color, backgroundColor: color + '28',
      borderWidth: 2, borderDash: dash,
      pointRadius: opts.pointRadius ?? 1, pointHoverRadius: 3,
      tension: 0.3, fill: '+1', spanGaps: false,
    });
    datasets.push({
      label: baseLabel + '__max', data: bufferedMaxData, realData: realMaxData,
      borderColor: 'transparent', backgroundColor: 'transparent',
      borderWidth: 0, pointRadius: 0, pointHoverRadius: 0,
      fill: false, spanGaps: false,
    });
  });
  return datasets;
}

/**
 * @param {string} containerId
 * @param {any} chart
 * @param {any} [opts]
 */
function buildAvgMaxLegend(containerId, chart, opts = {}) {
  const el = document.getElementById(containerId);
  if (!el || !chart) { if (el) el.innerHTML = ''; return; }
  const avgDatasets = /** @type {any[]} */ (chart.data.datasets).filter((_, i) => i % 2 === 0);
  if (opts.withToggle) {
    el.innerHTML = avgDatasets.map((ds, i) => `
      <button type="button" class="legend-chip" data-avg="${i * 2}" aria-pressed="true">
        <span class="legend-dot" style="background:${ds.borderColor};"></span>${ds.label}
      </button>
    `).join('');
    el.querySelectorAll('.legend-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const avgIdx = +(/** @type {any} */ (chip)).dataset.avg;
        const hidden = !chart.getDatasetMeta(avgIdx).hidden;
        chart.getDatasetMeta(avgIdx).hidden     = hidden;
        chart.getDatasetMeta(avgIdx + 1).hidden = hidden;
        chip.setAttribute('aria-pressed', String(!hidden));
        chart.update();
      });
    });
    return;
  }
  el.innerHTML = avgDatasets.map(ds => {
    const isDash = ds.borderDash && ds.borderDash.length > 0;
    const lineHtml = isDash
      ? `<svg width="20" height="3" style="flex-shrink:0;"><line x1="0" y1="1.5" x2="20" y2="1.5" stroke="${ds.borderColor}" stroke-width="2" stroke-dasharray="4 2"/></svg>`
      : `<span class="legend-line" style="width:20px; height:3px; border-radius:2px; background:${ds.borderColor}; flex-shrink:0;"></span>`;
    const labelHtml = opts.codeLabel
      ? `<code>${ds.label}</code>`
      : `<span class="text-label">${ds.label}</span>`;
    return `<span style="display:flex; align-items:center; gap:5px;">${lineHtml}${labelHtml}</span>`;
  }).join('');
}

/**
 * @param {any} container
 * @param {any} chart
 */
function renderChipLegend(container, chart) {
  if (!container) return;
  if (!chart) { container.innerHTML = ''; return; }
  container.innerHTML = /** @type {any[]} */ (chart.data.datasets).map((ds, i) => `
    <button type="button" class="legend-chip" data-idx="${i}" aria-pressed="true">
      <span class="legend-dot" style="background:${ds.borderColor};"></span>${ds.label}
    </button>
  `).join('');
  container.querySelectorAll('.legend-chip').forEach((/** @type {any} */ chip) => {
    chip.addEventListener('click', () => {
      const meta = chart.getDatasetMeta(+chip.dataset.idx);
      meta.hidden = !meta.hidden;
      chip.setAttribute('aria-pressed', String(!meta.hidden));
      chart.update();
    });
  });
}

export {
  RANGE_LABEL, AUTO_BUCKET, BUCKET_LABEL, RANGE_MS, BUCKET_MS, COLORS, themeColor,
  fmtKst, fmtLabel, fmtKbChart,
  getAnchorEnd, initAnchor,
  makeBucketGrid, joinToGrid, buildDimDatasets, fmtThroughput,
  bindToggle, pageTimeControl, initAutoRefresh, safeArray, naWindows, setNaText, setValText,
  buildAvgMaxDatasets, buildAvgMaxLegend,
  renderChipLegend,
};
