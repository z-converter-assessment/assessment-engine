// @ts-check
/**
 * cpu 페이지 차트 로직.
 *
 * 외부 의존:
 * - ChartUtils (base.html에서 chart-utils.js 로드)
 * - Chart.js (페이지에서 chart.umd.min.js 로드)
 * - body data-server-id / data-os-family (E6 외부화 규약, static-assets.md)
 *
 * 시간축: 페이지 단일 range + anchor(#F10) — 3 차트(사용률·분류·실행큐)가 pageTimeControl 하나를 공유해
 * 같은 창·시점으로 그려진다(신호 간 시점 상관). usage 차트만 agg(avg/max/p95) 토글 별도 보유.
 */
const { RANGE_LABEL, AUTO_BUCKET, BUCKET_LABEL, BUCKET_MS,
        fmtLabel, makeBucketGrid, joinToGrid, bindToggle, pageTimeControl,
        initAutoRefresh, buildDimDatasets, renderChipLegend } = /** @type {any} */ (ChartUtils);

// body data attribute 단일 진실 (#E6 inline <script> 금지).
const SERVER_ID = document.body.dataset.serverId;
const OS_FAMILY = document.body.dataset.osFamily || '';  // Windows 미측정 메트릭 N/A 분기

/** @param {number | null | undefined} v */
function pct(v) { return v == null ? '—' : v.toFixed(1) + '%'; }

/* ── 스냅샷 ── */
async function loadSnapshot() {
  try {
    const res = await fetch(`/api/servers/${SERVER_ID}/metrics/latest`);
    /** @type {HTMLElement} */ (document.getElementById('snap-loading')).style.display = 'none';
    if (res.status === 404) { /** @type {HTMLElement} */ (document.getElementById('snap-empty')).style.display = ''; return; }
    if (!res.ok) return;
    /** @type {import('../generated/api').components['schemas']['MetricDashboard']} */
    const data = await res.json();
    const cpu = data.cpu;
    if (!cpu) { /** @type {HTMLElement} */ (document.getElementById('snap-empty')).style.display = ''; return; }
    /** @type {HTMLElement} */ (document.getElementById('s-usage')).textContent  = pct(cpu.usage_pct);
    /** @type {HTMLElement} */ (document.getElementById('s-user')).textContent   = pct(cpu.user_pct);
    /** @type {HTMLElement} */ (document.getElementById('s-system')).textContent = pct(cpu.system_pct);
    /** @type {HTMLElement} */ (document.getElementById('s-iowait')).textContent = /** @type {string} */ (ChartUtils.naWindows(OS_FAMILY, 'cpu_iowait', pct(cpu.iowait_pct)));
    // 포화 스냅샷 신호 — 실행 큐(per-core), Steal, PSI. 서버 os-aware 판정 완료(값/임계/4상태), 공통 렌더만 (P4, E3 재계산 금지).
    SignalUtils.renderSaturation(document.getElementById('cpu-sat-signals'), data.cpu_saturation);
    const stampEl = document.getElementById('metrics-stamp');
    if (stampEl && data.collected_at) stampEl.textContent = '30초마다 자동 갱신 · 최근 ' + ChartUtils.fmtKst(data.collected_at);
    /** @type {HTMLElement} */ (document.getElementById('snap-body')).style.display = '';
  } catch(e) {
    /** @type {HTMLElement} */ (document.getElementById('snap-loading')).textContent = '불러오기 실패';
    console.error(e);
  }
}

/* ── 단일 라인 차트 (CPU 사용률) — usage 만 agg 토글 별도 ── */
let usageAgg   = 'avg';
/** @type {any} */
let usageChart = null;
let usageSeq   = 0;

async function loadUsageChart() {
  const seq = ++usageSeq;
  const capturedRange  = timeCtl.getRange();
  const capturedAgg    = usageAgg;
  const capturedAnchor = timeCtl.getAnchor();
  const canvas = /** @type {HTMLElement} */ (document.getElementById('usage-canvas'));
  const empty  = /** @type {HTMLElement} */ (document.getElementById('usage-empty'));
  const params = new URLSearchParams({
    metric_type: 'cpu.usage_percent',
    time_range: capturedRange,
    bucket: AUTO_BUCKET[capturedRange],
    agg: capturedAgg,
  });
  if (capturedAnchor) params.append('end', capturedAnchor.toISOString());
  try {
    /** @type {import('../generated/api').components['schemas']['MetricSeriesItem'][]} */
    const rows = await fetch(`/api/servers/${SERVER_ID}/metrics/chart?${params}`).then(r => r.json());
    if (seq !== usageSeq) return;
    if (!Array.isArray(rows) || !rows.length) {
      canvas.style.display = 'none'; empty.style.display = '';
      if (usageChart) { usageChart.destroy(); usageChart = null; }
      return;
    }
    canvas.style.display = ''; empty.style.display = 'none';
    const bMs    = BUCKET_MS[AUTO_BUCKET[capturedRange]];
    const grid   = makeBucketGrid(capturedRange, AUTO_BUCKET[capturedRange], capturedAnchor);
    const labels = grid.map((/** @type {number} */ t) => fmtLabel(new Date(t).toISOString(), capturedRange));
    const data   = joinToGrid(grid, rows, bMs);
    if (usageChart) {
      usageChart.data.labels      = labels;
      usageChart.data.datasets[0].data = data;
      usageChart.update('none');
      return;
    }
    usageChart = new Chart(canvas, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: 'CPU 사용률',
          data,
          borderColor: /** @type {any} */ (ChartUtils).themeColor(),
          borderWidth: 2,
          pointRadius: 1,
          pointHoverRadius: 3,
          tension: 0.3,
          fill: false,
          spanGaps: false,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode:'index', intersect:false },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: ctx => ` CPU 사용률: ${ctx.parsed.y?.toFixed(1)}%` } },
        },
        scales: {
          x: { ticks:{ maxTicksLimit:12, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
          y: {
            ticks: { callback: v => v + '%', font:{size:11}, color:'#64748b' },
            grid:  { color:'#f1f5f9' },
            min: 0, max: 100,
          },
        },
      },
    });
  } catch(e) { console.error(e); }
}

bindToggle('usage-agg-btns', (/** @type {string} */ v) => { usageAgg = v; loadUsageChart(); });

/* ── CPU 분류 추이 (user / system / iowait) ── */
/** @type {any} */
let compChart   = null;
/** @type {any[]} */
let compAllRows = [];
let compSeq     = 0;

/**
 * @param {string} range
 * @param {Date | null} anchorEnd
 */
function renderCompChart(range, anchorEnd) {
  const canvas = /** @type {HTMLElement} */ (document.getElementById('comp-canvas'));
  const empty  = /** @type {HTMLElement} */ (document.getElementById('comp-empty'));
  const rows   = compAllRows;
  if (!rows.length) {
    canvas.style.display = 'none'; empty.style.display = '';
    if (compChart) { compChart.destroy(); compChart = null; }
    return;
  }
  canvas.style.display = ''; empty.style.display = 'none';

  // Windows 는 cpu_stat 이 user/system/idle 만(iowait 개념 부재) — iowait 축 제외(빈 라인·범례 방지, OS 분기).
  const COMP_META = {
    user:   { label: 'User',     color: /** @type {any} */ (ChartUtils).themeColor() },
    system: { label: 'System',   color: '#f59e0b' },
    ...(OS_FAMILY === 'windows' ? {} : { iowait: { label: 'I/O Wait', color: '#ef4444' } }),
  };
  const bMs    = BUCKET_MS[AUTO_BUCKET[range]];
  const grid   = makeBucketGrid(range, AUTO_BUCKET[range], anchorEnd);
  const labels = grid.map((/** @type {number} */ t) => fmtLabel(new Date(t).toISOString(), range));
  const datasets = buildDimDatasets(rows, bMs, grid, COMP_META, { pointRadius: 1 });

  if (compChart) {
    compChart.data.labels = labels; compChart.data.datasets = datasets;
    compChart.update('none'); return;
  }
  compChart = new Chart(canvas, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode:'index', intersect:false },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(1)}%` } },
      },
      scales: {
        x: { ticks:{ maxTicksLimit:12, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
        y: {
          ticks: { callback: v => v + '%', font:{size:11}, color:'#64748b' },
          grid:  { color:'#f1f5f9' },
          beginAtZero: true,
        },
      },
    },
  });
}

function buildCompLegend() {
  renderChipLegend(document.getElementById('comp-legend'), compChart);
}

async function loadCompChart() {
  const seq = ++compSeq;
  const capturedRange  = timeCtl.getRange();
  const capturedAnchor = timeCtl.getAnchor();
  const bucket = AUTO_BUCKET[capturedRange];
  const mkP = (/** @type {string} */ type) => {
    const p = new URLSearchParams({ metric_type: type, time_range: capturedRange, bucket, agg: 'avg' });
    if (capturedAnchor) p.append('end', capturedAnchor.toISOString());
    return p;
  };
  try {
    // Windows 는 iowait 개념 부재 — fetch 자체 skip(빈 요청·빈 라인 방지, OS 분기).
    /** @type {Promise<import('../generated/api').components['schemas']['MetricSeriesItem'][]>[]} */
    const reqs = [
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('cpu.user_percent')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('cpu.system_percent')}`).then(r => r.json()),
    ];
    if (OS_FAMILY !== 'windows') {
      reqs.push(fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('cpu.iowait_percent')}`).then(r => r.json()));
    }
    const [userRows, sysRows, ioRows] = await Promise.all(reqs);
    if (seq !== compSeq) return;
    const safe = (/** @type {any} */ arr) => Array.isArray(arr) ? arr : [];
    compAllRows = [
      ...safe(userRows).map(r => ({ ...r, dimension: 'user' })),
      ...safe(sysRows).map(r  => ({ ...r, dimension: 'system' })),
      ...(OS_FAMILY !== 'windows' ? safe(ioRows).map(r => ({ ...r, dimension: 'iowait' })) : []),
    ];
    renderCompChart(capturedRange, capturedAnchor);
    buildCompLegend();
  } catch(e) { console.error(e); }
}

/* ── 실행 큐 추이 (os-aware: Linux procs_running / Windows Processor Queue, 코어당) ── */
/** @type {any} */
let loadChart   = null;
/** @type {any[]} */
let loadAllRows = [];
let loadSeq     = 0;

/**
 * @param {string} range
 * @param {Date | null} anchorEnd
 */
function renderLoadChart(range, anchorEnd) {
  const canvas = /** @type {HTMLElement} */ (document.getElementById('load-canvas'));
  const empty  = /** @type {HTMLElement} */ (document.getElementById('load-empty'));
  const rows   = loadAllRows;
  if (!rows.length) {
    canvas.style.display = 'none'; empty.style.display = '';
    if (loadChart) { loadChart.destroy(); loadChart = null; }
    return;
  }
  canvas.style.display = ''; empty.style.display = 'none';

  const RUNQ_META = {
    runq: { label: '실행 큐 (코어당)', color: /** @type {any} */ (ChartUtils).themeColor() },
  };
  const bMs    = BUCKET_MS[AUTO_BUCKET[range]];
  const grid   = makeBucketGrid(range, AUTO_BUCKET[range], anchorEnd);
  const labels = grid.map((/** @type {number} */ t) => fmtLabel(new Date(t).toISOString(), range));
  // cpu.run_queue 는 backend 가 이미 Σ실행큐/Σcores(코어당) 반환 — valueFn 불요. 1.0 Linux·2.0 Windows 포화.
  const datasets = buildDimDatasets(rows, bMs, grid, RUNQ_META, { pointRadius: 1 });

  if (loadChart) {
    loadChart.data.labels = labels; loadChart.data.datasets = datasets;
    loadChart.update('none'); return;
  }
  loadChart = new Chart(canvas, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode:'index', intersect:false },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(2)}` } },
      },
      scales: {
        x: { ticks:{ maxTicksLimit:12, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
        y: {
          ticks: { font:{size:11}, color:'#64748b' },
          grid:  { color:'#f1f5f9' },
          beginAtZero: true, suggestedMax: 2,
        },
      },
    },
  });
}

function buildLoadLegend() {
  renderChipLegend(document.getElementById('load-legend'), loadChart);
}

async function loadLoadChart() {
  const seq = ++loadSeq;
  const capturedRange  = timeCtl.getRange();
  const capturedAnchor = timeCtl.getAnchor();
  const bucket = AUTO_BUCKET[capturedRange];
  const mkP = (/** @type {string} */ type) => {
    const p = new URLSearchParams({ metric_type: type, time_range: capturedRange, bucket, agg: 'avg' });
    if (capturedAnchor) p.append('end', capturedAnchor.toISOString());
    return p;
  };
  try {
    /** @type {import('../generated/api').components['schemas']['MetricSeriesItem'][]} */
    const rows = await fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('cpu.run_queue')}`).then(r => r.json());
    if (seq !== loadSeq) return;
    loadAllRows = (Array.isArray(rows) ? rows : []).map(r => ({ ...r, dimension: 'runq' }));
    if (loadChart) { loadChart.destroy(); loadChart = null; }
    renderLoadChart(capturedRange, capturedAnchor);
    buildLoadLegend();
  } catch(e) { console.error(e); }
}

/* ── CPU PSI 추이 (some 정체율 %, 단일선 — Linux 전용, Windows 빈 결과 -> empty) ── */
/** @type {any} */
let psiChart = null;
let psiSeq   = 0;

async function loadPsiChart() {
  const seq = ++psiSeq;
  const range  = timeCtl.getRange();
  const anchor = timeCtl.getAnchor();
  const p = new URLSearchParams({ metric_type: 'cpu.psi', time_range: range, bucket: AUTO_BUCKET[range], agg: 'avg' });
  if (anchor) p.append('end', anchor.toISOString());
  const canvas = /** @type {HTMLElement} */ (document.getElementById('cpu-psi-canvas'));
  const empty  = /** @type {HTMLElement} */ (document.getElementById('cpu-psi-empty'));
  try {
    /** @type {import('../generated/api').components['schemas']['MetricSeriesItem'][]} */
    const rows = await fetch(`/api/servers/${SERVER_ID}/metrics/chart?${p}`).then(r => r.json());
    if (seq !== psiSeq) return;
    if (!Array.isArray(rows) || !rows.length) {
      canvas.style.display = 'none'; empty.style.display = '';
      if (psiChart) { psiChart.destroy(); psiChart = null; }
      return;
    }
    canvas.style.display = ''; empty.style.display = 'none';
    const bMs    = BUCKET_MS[AUTO_BUCKET[range]];
    const grid   = makeBucketGrid(range, AUTO_BUCKET[range], anchor);
    const labels = grid.map((/** @type {number} */ t) => fmtLabel(new Date(t).toISOString(), range));
    const data   = joinToGrid(grid, rows, bMs);
    if (psiChart) {
      psiChart.data.labels = labels; psiChart.data.datasets[0].data = data;
      psiChart.update('none'); return;
    }
    psiChart = new Chart(canvas, {
      type: 'line',
      data: { labels, datasets: [{
        label: 'PSI (정체율)', data,
        borderColor: /** @type {any} */ (ChartUtils).themeColor(),
        borderWidth: 2, pointRadius: 1, pointHoverRadius: 3, tension: 0.3, fill: false, spanGaps: false,
      }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode:'index', intersect:false },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (/** @type {any} */ ctx) => ` PSI: ${ctx.parsed.y?.toFixed(1)}%` } } },
        scales: {
          x: { ticks:{ maxTicksLimit:12, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
          y: { ticks:{ callback: v => v + '%', font:{size:11}, color:'#64748b' }, grid:{ color:'#f1f5f9' }, beginAtZero: true, suggestedMax: 20 },
        },
      },
    });
  } catch(e) { console.error(e); }
}

/* ── 페이지 단일 시간축 버킷 라벨·print range (모든 차트 공통 range) ── */
function updateBucketLabels() {
  const r = timeCtl.getRange();
  const bl = BUCKET_LABEL[AUTO_BUCKET[r]] || '';
  const pr = ' — ' + RANGE_LABEL[r];
  /** @param {string} id @param {string} t */
  const set = (id, t) => { const el = document.getElementById(id); if (el) el.textContent = t; };
  set('usage-bucket-label', bl); set('comp-bucket-label', bl); set('load-bucket-label', bl); set('cpu-psi-bucket-label', bl);
  set('usage-range-print', pr); set('comp-range-print', pr); set('load-range-print', pr); set('cpu-psi-range-print', pr);
}

/* ── 전체 차트 reload (페이지 range/anchor 변경 시) ── */
function reloadAllCharts() {
  updateBucketLabels();
  loadUsageChart();
  loadCompChart();
  loadLoadChart();
  loadPsiChart();
}

// 페이지 단일 시간축 컨트롤러 — range 토글 + anchor 가 3 차트 전체 구동(#F10).
const timeCtl = pageTimeControl('page-range-btns', 'page-anchor', '15m', reloadAllCharts);

/* ── 30초 polling 자동 갱신 (스냅샷만) ── */
initAutoRefresh(loadSnapshot);

/* ── 초기 로드 ── */
loadSnapshot();
reloadAllCharts();
