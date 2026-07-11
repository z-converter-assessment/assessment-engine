// @ts-check
/**
 * cpu 페이지 차트 로직.
 *
 * 외부 의존:
 * - ChartUtils (base.html에서 chart-utils.js 로드)
 * - Chart.js (페이지에서 chart.umd.min.js 로드)
 * - body data-server-id / data-cpu-cores (E6 외부화 규약, static-assets.md)
 */
// ChartUtils — /static/js/chart-utils.js (base.html에서 로드)
const { RANGE_LABEL, AUTO_BUCKET, BUCKET_LABEL, BUCKET_MS,
        fmtLabel, getAnchorEnd, initAnchor,
        makeBucketGrid, joinToGrid, bindToggle, initAutoRefresh, safeArray,
        buildDimDatasets, renderChipLegend } = /** @type {any} */ (ChartUtils);

// body data attribute 단일 진실 (#E6 inline <script> 금지).
const SERVER_ID = document.body.dataset.serverId;
const CPU_CORES = parseInt(/** @type {string} */ (document.body.dataset.cpuCores), 10) || 4;
const OS_FAMILY = document.body.dataset.osFamily || '';  // Windows 미측정 메트릭 N/A 분기

// 로드 추이는 코어당(load/cpu_cores) 정규화 — 1.0 = 코어당 run queue 1(포화). 환경 추이(Σload/Σcores)와
// 의미 일관. suggestedMax 1.5(포화선 위 여유)로 포화 임계가 시각에 자연 노출되고, 초과 시 자동 확장.

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
    // Steal — 가상화 경합(하이퍼바이저 vCPU 시간 뺏김). Linux 전용, Windows 는 개념 부재로 N/A.
    /** @type {HTMLElement} */ (document.getElementById('s-steal')).textContent = /** @type {string} */ (ChartUtils.naWindows(OS_FAMILY, 'cpu_steal', pct(cpu.steal_pct)));
    // 실행 큐 — os-aware(Linux procs_running / Windows Processor Queue). 코어당 = 큐/코어 (1.0 Linux·2.0 Windows 포화).
    /** @type {HTMLElement} */ (document.getElementById('s-runq')).textContent      = data.cpu_run_queue != null ? data.cpu_run_queue.toFixed(1) : '—';
    /** @type {HTMLElement} */ (document.getElementById('s-runq-core')).textContent = data.cpu_run_queue != null ? (data.cpu_run_queue / CPU_CORES).toFixed(2) : '—';
    const stampEl = document.getElementById('metrics-stamp');
    if (stampEl && data.collected_at) stampEl.textContent = '30초마다 자동 갱신 · 최근 ' + ChartUtils.fmtKst(data.collected_at);
    /** @type {HTMLElement} */ (document.getElementById('snap-body')).style.display = '';
  } catch(e) {
    /** @type {HTMLElement} */ (document.getElementById('snap-loading')).textContent = '불러오기 실패';
    console.error(e);
  }
}

/* ── 단일 라인 차트 (CPU 사용률) ── */
let usageRange = '15m';
let usageAgg   = 'avg';
let usageChart = null;
let usageSeq   = 0;

function updateUsageBucketLabel() {
  /** @type {HTMLElement} */ (document.getElementById('usage-bucket-label')).textContent = BUCKET_LABEL[AUTO_BUCKET[usageRange]] || '';
}

async function loadUsageChart() {
  const seq = ++usageSeq;
  const capturedRange  = usageRange;
  const capturedAgg    = usageAgg;
  const capturedAnchor = getAnchorEnd('usage-anchor');
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
    const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), capturedRange));
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

bindToggle('usage-agg-btns',   v => { usageAgg   = v; loadUsageChart(); });
bindToggle('usage-range-btns', v => { usageRange = v; updateUsageBucketLabel(); /** @type {HTMLElement} */ (document.getElementById('usage-range-print')).textContent = ' — ' + RANGE_LABEL[v]; loadUsageChart(); });

/* ── CPU 분류 추이 (user / system / iowait) ── */
let compRange   = '15m';
let compChart   = null;
let compAllRows = [];
let compSeq     = 0;

function updateCompBucketLabel() {
  /** @type {HTMLElement} */ (document.getElementById('comp-bucket-label')).textContent = BUCKET_LABEL[AUTO_BUCKET[compRange]] || '';
}

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
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
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
  const capturedRange  = compRange;
  const capturedAnchor = getAnchorEnd('comp-anchor');
  const bucket = AUTO_BUCKET[capturedRange];
  const mkP = type => {
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
    const safe = arr => Array.isArray(arr) ? arr : [];
    compAllRows = [
      ...safe(userRows).map(r => ({ ...r, dimension: 'user' })),
      ...safe(sysRows).map(r  => ({ ...r, dimension: 'system' })),
      ...(OS_FAMILY !== 'windows' ? safe(ioRows).map(r => ({ ...r, dimension: 'iowait' })) : []),
    ];
    renderCompChart(capturedRange, capturedAnchor);
    buildCompLegend();
  } catch(e) { console.error(e); }
}

bindToggle('comp-range-btns', v => { compRange = v; updateCompBucketLabel(); /** @type {HTMLElement} */ (document.getElementById('comp-range-print')).textContent = ' — ' + RANGE_LABEL[v]; loadCompChart(); });

/* ── 로드 평균 추이 ── */
let loadRange   = '15m';
let loadChart   = null;
let loadAllRows = [];
let loadSeq     = 0;

function updateLoadBucketLabel() {
  /** @type {HTMLElement} */ (document.getElementById('load-bucket-label')).textContent = BUCKET_LABEL[AUTO_BUCKET[loadRange]] || '';
}

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
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
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
  const capturedRange  = loadRange;
  const capturedAnchor = getAnchorEnd('load-anchor');
  const bucket = AUTO_BUCKET[capturedRange];
  const mkP = type => {
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

bindToggle('load-range-btns', v => { loadRange = v; updateLoadBucketLabel(); /** @type {HTMLElement} */ (document.getElementById('load-range-print')).textContent = ' — ' + RANGE_LABEL[v]; loadLoadChart(); });

/* ── 30초 polling 자동 갱신 (SSE 제거) ── */
initAutoRefresh(loadSnapshot);

/* ── 기준일 초기화 ── */
initAnchor('usage-anchor');
initAnchor('comp-anchor');
/** @type {HTMLElement} */ (document.getElementById('usage-anchor')).addEventListener('change', () => loadUsageChart());
/** @type {HTMLElement} */ (document.getElementById('comp-anchor')).addEventListener('change', () => loadCompChart());

/* ── 초기 로드 ── */
loadSnapshot();
updateUsageBucketLabel();
loadUsageChart();
updateCompBucketLabel();
loadCompChart();

/* 실행 큐 추이 — os-aware(Linux procs_running / Windows Processor Queue), 양 OS 표시. */
initAnchor('load-anchor');
/** @type {HTMLElement} */ (document.getElementById('load-anchor')).addEventListener('change', () => loadLoadChart());
updateLoadBucketLabel();
loadLoadChart();
