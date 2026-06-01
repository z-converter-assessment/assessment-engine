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
        fmtKst, fmtLabel, getAnchorEnd, initAnchor,
        makeBucketGrid, joinToGrid, bindToggle, initSse, safeArray,
        fetchRebootEvents, applyRebootMarkers, renderChipLegend } = ChartUtils;

// body data attribute 단일 진실 (#E6 inline <script> 금지).
const SERVER_ID = document.body.dataset.serverId;
const CPU_CORES = parseInt(document.body.dataset.cpuCores, 10) || 4;
const OS_FAMILY = document.body.dataset.osFamily || '';  // Windows 미측정 메트릭 N/A 분기

// 로드 추이의 분해력+포화 기준 하이브리드 — 작은 값은 그대로 보여주되
// suggestedMax(=cpu_cores)를 두어 "코어수=포화" 임계선이 시각에 자연스럽게 노출.
// 실데이터가 cpu_cores를 초과하면 자동 확장.

function pct(v) { return v == null ? '—' : v.toFixed(1) + '%'; }

/* ── 스냅샷 ── */
async function loadSnapshot() {
  try {
    const res = await fetch(`/api/servers/${SERVER_ID}/metrics/latest`);
    document.getElementById('snap-loading').style.display = 'none';
    if (res.status === 404) { document.getElementById('snap-empty').style.display = ''; return; }
    if (!res.ok) return;
    const data = await res.json();
    const cpu = data.cpu;
    if (!cpu) { document.getElementById('snap-empty').style.display = ''; return; }
    document.getElementById('s-usage').textContent  = pct(cpu.usage_pct);
    document.getElementById('s-user').textContent   = pct(cpu.user_pct);
    document.getElementById('s-system').textContent = pct(cpu.system_pct);
    document.getElementById('s-iowait').textContent = ChartUtils.naWindows(OS_FAMILY, 'cpu_iowait', pct(cpu.iowait_pct));
    document.getElementById('s-load1').textContent  = ChartUtils.naWindows(OS_FAMILY, 'load_1m', data.load_1m  != null ? data.load_1m.toFixed(2)  : '—');
    document.getElementById('s-load5').textContent  = ChartUtils.naWindows(OS_FAMILY, 'load_5m', data.load_5m  != null ? data.load_5m.toFixed(2)  : '—');
    document.getElementById('s-load15').textContent = ChartUtils.naWindows(OS_FAMILY, 'load_15m', data.load_15m != null ? data.load_15m.toFixed(2) : '—');
    if (data.collected_at) document.getElementById('snap-ts').textContent = '수집 기준: ' + fmtKst(data.collected_at);
    document.getElementById('snap-body').style.display = '';
  } catch(e) {
    document.getElementById('snap-loading').textContent = '불러오기 실패';
    console.error(e);
  }
}

/* ── 단일 라인 차트 (CPU 사용률) ── */
let usageRange = '15m';
let usageAgg   = 'avg';
let usageChart = null;
let usageSeq   = 0;

function updateUsageBucketLabel() {
  document.getElementById('usage-bucket-label').textContent = BUCKET_LABEL[AUTO_BUCKET[usageRange]] || '';
}

async function loadUsageChart() {
  const seq = ++usageSeq;
  const capturedRange  = usageRange;
  const capturedAgg    = usageAgg;
  const capturedAnchor = getAnchorEnd('usage-anchor');
  const canvas = document.getElementById('usage-canvas');
  const empty  = document.getElementById('usage-empty');
  const params = new URLSearchParams({
    metric_type: 'cpu.usage_percent',
    time_range: capturedRange,
    bucket: AUTO_BUCKET[capturedRange],
    agg: capturedAgg,
  });
  if (capturedAnchor) params.append('end', capturedAnchor.toISOString());
  try {
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
          borderColor: '#3b82f6',
          backgroundColor: '#3b82f622',
          borderWidth: 2,
          pointRadius: 1,
          pointHoverRadius: 3,
          tension: 0.3,
          fill: true,
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
            title: { display:true, text:'%', font:{size:11}, color:'#94a3b8' },
            ticks: { callback: v => v + '%', font:{size:11}, color:'#64748b' },
            grid:  { color:'#f1f5f9' },
            min: 0, max: 100,
          },
        },
      },
    });
    // reboot/restart vertical marker (P4(a) seq 검사로 stale 응답 방지)
    const events = await fetchRebootEvents(SERVER_ID, capturedRange, capturedAnchor);
    if (seq !== usageSeq) return;
    applyRebootMarkers(usageChart, events, grid);
  } catch(e) { console.error(e); }
}

bindToggle('usage-agg-btns',   v => { usageAgg   = v; loadUsageChart(); });
bindToggle('usage-range-btns', v => { usageRange = v; updateUsageBucketLabel(); document.getElementById('usage-range-print').textContent = ' — ' + RANGE_LABEL[v]; loadUsageChart(); });

/* ── CPU 분류 추이 (user / system / iowait) ── */
let compRange   = '15m';
let compChart   = null;
let compAllRows = [];
let compSeq     = 0;

function updateCompBucketLabel() {
  document.getElementById('comp-bucket-label').textContent = BUCKET_LABEL[AUTO_BUCKET[compRange]] || '';
}

function renderCompChart(range, anchorEnd) {
  const canvas = document.getElementById('comp-canvas');
  const empty  = document.getElementById('comp-empty');
  const rows   = compAllRows;
  if (!rows.length) {
    canvas.style.display = 'none'; empty.style.display = '';
    if (compChart) { compChart.destroy(); compChart = null; }
    return;
  }
  canvas.style.display = ''; empty.style.display = 'none';

  const COMP_LABELS = { 'user':'User', 'system':'System', 'iowait':'I/O Wait' };
  const COMP_COLORS = { 'user':'#3b82f6', 'system':'#f59e0b', 'iowait':'#ef4444' };
  const bMs    = BUCKET_MS[AUTO_BUCKET[range]];
  const grid   = makeBucketGrid(range, AUTO_BUCKET[range], anchorEnd);
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const byDim  = {};
  for (const r of rows) { (byDim[r.dimension] = byDim[r.dimension] || []).push(r); }
  const datasets = Object.entries(byDim).map(([dim, pts]) => {
    const map = {};
    for (const p of pts) { map[Math.floor(new Date(p.collected_at).getTime() / bMs) * bMs] = p.value; }
    const color = COMP_COLORS[dim] || '#8b5cf6';
    return {
      label: COMP_LABELS[dim] || dim,
      data: grid.map(t => map[t] ?? null),
      borderColor: color,
      backgroundColor: color + '22',
      borderWidth: 2,
      pointRadius: 1,
      pointHoverRadius: 3,
      tension: 0.3,
      fill: false,
      spanGaps: false,
    };
  });

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
          title: { display:true, text:'%', font:{size:11}, color:'#94a3b8' },
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
    const [userRows, sysRows, ioRows] = await Promise.all([
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('cpu.user_percent')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('cpu.system_percent')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('cpu.iowait_percent')}`).then(r => r.json()),
    ]);
    if (seq !== compSeq) return;
    const safe = arr => Array.isArray(arr) ? arr : [];
    compAllRows = [
      ...safe(userRows).map(r => ({ ...r, dimension: 'user' })),
      ...safe(sysRows).map(r  => ({ ...r, dimension: 'system' })),
      ...safe(ioRows).map(r   => ({ ...r, dimension: 'iowait' })),
    ];
    renderCompChart(capturedRange, capturedAnchor);
    buildCompLegend();
    const events = await fetchRebootEvents(SERVER_ID, capturedRange, capturedAnchor);
    if (seq !== compSeq) return;
    const grid = makeBucketGrid(capturedRange, AUTO_BUCKET[capturedRange], capturedAnchor);
    applyRebootMarkers(compChart, events, grid);
  } catch(e) { console.error(e); }
}

bindToggle('comp-range-btns', v => { compRange = v; updateCompBucketLabel(); document.getElementById('comp-range-print').textContent = ' — ' + RANGE_LABEL[v]; loadCompChart(); });

/* ── 로드 평균 추이 ── */
let loadRange   = '15m';
let loadChart   = null;
let loadAllRows = [];
let loadSeq     = 0;

function updateLoadBucketLabel() {
  document.getElementById('load-bucket-label').textContent = BUCKET_LABEL[AUTO_BUCKET[loadRange]] || '';
}

function renderLoadChart(range, anchorEnd) {
  const canvas = document.getElementById('load-canvas');
  const empty  = document.getElementById('load-empty');
  const rows   = loadAllRows;
  if (!rows.length) {
    canvas.style.display = 'none'; empty.style.display = '';
    if (loadChart) { loadChart.destroy(); loadChart = null; }
    return;
  }
  canvas.style.display = ''; empty.style.display = 'none';

  const LOAD_LABELS = { 'load1':'Load 1m', 'load5':'Load 5m', 'load15':'Load 15m' };
  const LOAD_COLORS = { 'load1':'#3b82f6', 'load5':'#22c55e', 'load15':'#f59e0b' };
  const bMs    = BUCKET_MS[AUTO_BUCKET[range]];
  const grid   = makeBucketGrid(range, AUTO_BUCKET[range], anchorEnd);
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const byDim  = {};
  for (const r of rows) { (byDim[r.dimension] = byDim[r.dimension] || []).push(r); }
  const datasets = Object.entries(byDim).map(([dim, pts]) => {
    const map = {};
    for (const p of pts) { map[Math.floor(new Date(p.collected_at).getTime() / bMs) * bMs] = p.value; }
    const color = LOAD_COLORS[dim] || '#8b5cf6';
    return {
      label: LOAD_LABELS[dim] || dim,
      data: grid.map(t => map[t] ?? null),
      borderColor: color,
      backgroundColor: color + '22',
      borderWidth: 2,
      pointRadius: 1,
      pointHoverRadius: 3,
      tension: 0.3,
      fill: false,
      spanGaps: false,
    };
  });

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
          title: { display:true, text:'Load', font:{size:11}, color:'#94a3b8' },
          ticks: { font:{size:11}, color:'#64748b' },
          grid:  { color:'#f1f5f9' },
          beginAtZero: true, suggestedMax: CPU_CORES,
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
    const [r1, r5, r15] = await Promise.all([
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('load.1m')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('load.5m')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('load.15m')}`).then(r => r.json()),
    ]);
    if (seq !== loadSeq) return;
    const safe = arr => Array.isArray(arr) ? arr : [];
    loadAllRows = [
      ...safe(r1).map(r  => ({ ...r, dimension: 'load1' })),
      ...safe(r5).map(r  => ({ ...r, dimension: 'load5' })),
      ...safe(r15).map(r => ({ ...r, dimension: 'load15' })),
    ];
    if (loadChart) { loadChart.destroy(); loadChart = null; }
    renderLoadChart(capturedRange, capturedAnchor);
    buildLoadLegend();
    const events = await fetchRebootEvents(SERVER_ID, capturedRange, capturedAnchor);
    if (seq !== loadSeq) return;
    const grid = makeBucketGrid(capturedRange, AUTO_BUCKET[capturedRange], capturedAnchor);
    applyRebootMarkers(loadChart, events, grid);
  } catch(e) { console.error(e); }
}

bindToggle('load-range-btns', v => { loadRange = v; updateLoadBucketLabel(); document.getElementById('load-range-print').textContent = ' — ' + RANGE_LABEL[v]; loadLoadChart(); });

/* ── SSE ── */
initSse(SERVER_ID, loadSnapshot);

/* ── 기준일 초기화 ── */
initAnchor('usage-anchor');
initAnchor('comp-anchor');
document.getElementById('usage-anchor').addEventListener('change', () => loadUsageChart());
document.getElementById('comp-anchor').addEventListener('change', () => loadCompChart());

/* ── 초기 로드 ── */
loadSnapshot();
updateUsageBucketLabel();
loadUsageChart();
updateCompBucketLabel();
loadCompChart();

/* 로드 평균 — Windows 는 load average 미측정이라 차트·컨트롤·초기화 전부 생략 (SSR 안내로 대체). */
if (OS_FAMILY !== 'windows') {
  initAnchor('load-anchor');
  document.getElementById('load-anchor').addEventListener('change', () => loadLoadChart());
  updateLoadBucketLabel();
  loadLoadChart();
}
