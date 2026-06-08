/**
 * memory 페이지 차트 로직.
 *
 * 외부 의존:
 * - ChartUtils (base.html에서 chart-utils.js 로드)
 * - Chart.js (페이지에서 chart.umd.min.js 로드)
 * - body data-server-id (E6 외부화 규약, static-assets.md)
 */
const { RANGE_LABEL, AUTO_BUCKET, BUCKET_LABEL, BUCKET_MS,
        fmtLabel, getAnchorEnd, initAnchor,
        makeBucketGrid, bindToggle, initAutoRefresh, safeArray,
        buildAvgMaxDatasets, renderChipLegend } = ChartUtils;

const SERVER_ID = document.body.dataset.serverId;
const OS_FAMILY = document.body.dataset.osFamily || '';  // Windows 미측정 메트릭 N/A 분기

// 현재 상태 메모리/스왑 측정값 단위 통일 — KB 입력 -> GB 소숫점1 고정 (인벤토리 '전체 메모리 X.X GB' 와 일관).
function fmtGb(kb) {
  if (kb == null) return '—';
  return (kb / 1024 / 1024).toFixed(1) + ' GB';
}

/* ── 스냅샷 ── */
async function loadSnapshot() {
  try {
    const res = await fetch(`/api/servers/${SERVER_ID}/metrics/latest`);
    document.getElementById('snap-loading').style.display = 'none';
    if (res.status === 404) { document.getElementById('snap-empty').style.display = ''; return; }
    if (!res.ok) return;
    const data = await res.json();
    const mem  = data.memory;
    const swap = data.swap;
    if (!mem) { document.getElementById('snap-empty').style.display = ''; return; }

    const usedKb = mem.total_kb != null && mem.available_kb != null ? mem.total_kb - mem.available_kb : null;
    document.getElementById('s-mem-pct').textContent     = mem.usage_pct    != null ? mem.usage_pct.toFixed(1) + '%' : '—';
    document.getElementById('s-mem-used').textContent    = fmtGb(usedKb);
    document.getElementById('s-mem-avail').textContent   = fmtGb(mem.available_kb);
    document.getElementById('s-mem-cached').textContent  = ChartUtils.naWindows(OS_FAMILY, 'mem_cached', fmtGb(mem.cached_kb));
    document.getElementById('s-mem-buffers').textContent = ChartUtils.naWindows(OS_FAMILY, 'mem_buffers', fmtGb(mem.buffers_kb));

    if (swap) {
      const swapUsedKb = swap.total_kb != null && swap.used_kb != null ? swap.used_kb : null;
      document.getElementById('s-swap-pct').textContent   = swap.usage_pct  != null ? swap.usage_pct.toFixed(1) + '%' : '—';
      document.getElementById('s-swap-used').textContent  = fmtGb(swapUsedKb);
    }

    const stampEl = document.getElementById('metrics-stamp');
    if (stampEl && data.collected_at) {
      stampEl.textContent = '30초마다 자동 갱신 · 최근 ' + ChartUtils.fmtKst(data.collected_at);
    }
    document.getElementById('snap-body').style.display = '';
  } catch(e) {
    document.getElementById('snap-loading').textContent = '불러오기 실패';
    console.error(e);
  }
}

/* ── avg+max ghost 차트 (mem·swap 공통) ──
 * Y축 정책: mem은 분해력 우선 0~100%, swap은 부분절대 suggestedMax=25 (낮은 사용률 0~10%도 시각화).
 */
const PCT_CHARTS = [
  { id: 'mem',  metric: 'mem.usage_percent',  label: '메모리 사용률', color: '#3b82f6', yMax: 100 },
  { id: 'swap', metric: 'swap.usage_percent', label: '스왑 사용률',   color: '#ef4444', ySuggestedMax: 25 },
];

function makePctLoader(def) {
  const state = { range: '15m', chart: null, seq: 0 };

  function bucketLabel() {
    document.getElementById(def.id + '-bucket-label').textContent = BUCKET_LABEL[AUTO_BUCKET[state.range]] || '';
  }

  function makeYScale() {
    const y = {
      title: { display:true, text:'%', font:{size:11}, color:'#94a3b8' },
      ticks: { callback: v => v + '%', font:{size:11}, color:'#64748b' },
      grid:  { color:'#f1f5f9' },
      min: 0,
    };
    if (def.yMax) y.max = def.yMax;
    if (def.ySuggestedMax) { y.beginAtZero = true; y.suggestedMax = def.ySuggestedMax; }
    return y;
  }

  function makeOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode:'index', intersect:false },
      plugins: {
        legend: { display: false },
        tooltip: {
          filter: item => item.datasetIndex % 2 === 0,
          callbacks: {
            label: ctx => {
              const avg = ctx.parsed.y;
              const maxDs = state.chart?.data.datasets[ctx.datasetIndex + 1];
              const realMax = maxDs?.realData?.[ctx.dataIndex];
              if (realMax != null)
                return ` ${def.label}: 평균 ${avg?.toFixed(1)}% / 최대 ${realMax?.toFixed(1)}%`;
              return ` ${def.label}: ${avg?.toFixed(1)}%`;
            },
          },
        },
      },
      scales: {
        x: { ticks:{ maxTicksLimit:12, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
        y: makeYScale(),
      },
    };
  }

  async function load() {
    const seq = ++state.seq;
    const capturedRange  = state.range;
    const capturedAnchor = getAnchorEnd(def.id + '-anchor');
    const canvas = document.getElementById(def.id + '-canvas');
    const empty  = document.getElementById(def.id + '-empty');
    const bucket = AUTO_BUCKET[capturedRange];
    const mkP = agg => {
      const p = new URLSearchParams({ metric_type: def.metric, time_range: capturedRange, bucket, agg });
      if (capturedAnchor) p.append('end', capturedAnchor.toISOString());
      return p;
    };
    try {
      const [avgRows, maxRows] = await Promise.all([
        fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('avg')}`).then(r => r.json()),
        fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('max')}`).then(r => r.json()),
      ]);
      if (seq !== state.seq) return;
      const avg = safeArray(avgRows);
      const max = safeArray(maxRows);
      if (!avg.length) {
        canvas.style.display = 'none'; empty.style.display = '';
        if (state.chart) { state.chart.destroy(); state.chart = null; }
        return;
      }
      canvas.style.display = ''; empty.style.display = 'none';

      const bMs    = BUCKET_MS[bucket];
      const grid   = makeBucketGrid(capturedRange, bucket, capturedAnchor);
      const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), capturedRange));
      const datasets = buildAvgMaxDatasets(avg, max, bMs, grid, { label: def.label, color: def.color });

      if (state.chart) {
        state.chart.data.labels = labels;
        state.chart.data.datasets[0].data = datasets[0].data;
        state.chart.data.datasets[1].data = datasets[1].data;
        state.chart.data.datasets[1].realData = datasets[1].realData;
        state.chart.update('none');
      } else {
        state.chart = new Chart(canvas, {
          type: 'line',
          data: { labels, datasets },
          options: makeOptions(),
        });
      }
    } catch(e) { console.error(e); }
  }

  return { state, load, bucketLabel };
}

const pctLoaders = PCT_CHARTS.map(makePctLoader);
pctLoaders.forEach((loader, i) => {
  const def = PCT_CHARTS[i];
  bindToggle(def.id + '-range-btns', v => {
    loader.state.range = v;
    loader.bucketLabel();
    document.getElementById(def.id + '-range-print').textContent = ' — ' + RANGE_LABEL[v];
    loader.load();
  });
  initAnchor(def.id + '-anchor');
  document.getElementById(def.id + '-anchor').addEventListener('change', () => loader.load());
});

/* ── 메모리 구성 추이 (used / available / cached / buffers %) — multi-dim ── */
let compRange = '15m';
let compChart = null;
let compSeq   = 0;

function updateCompBucketLabel() {
  document.getElementById('comp-bucket-label').textContent = BUCKET_LABEL[AUTO_BUCKET[compRange]] || '';
}

const COMP_META = {
  used:      { label: 'Used',      color: '#3b82f6' },
  available: { label: 'Available', color: '#8b5cf6' },
  cached:    { label: 'Cached',    color: '#22c55e' },
  buffers:   { label: 'Buffers',   color: '#f59e0b' },
};

function renderCompChart(rows, range, anchorEnd) {
  const canvas = document.getElementById('comp-canvas');
  const empty  = document.getElementById('comp-empty');
  if (!rows.length) {
    canvas.style.display = 'none'; empty.style.display = '';
    if (compChart) { compChart.destroy(); compChart = null; }
    buildCompLegend();
    return;
  }
  canvas.style.display = ''; empty.style.display = 'none';

  const bMs    = BUCKET_MS[AUTO_BUCKET[range]];
  const grid   = makeBucketGrid(range, AUTO_BUCKET[range], anchorEnd);
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const byDim  = {};
  for (const r of rows) { (byDim[r.dimension] = byDim[r.dimension] || []).push(r); }
  const datasets = Object.entries(byDim).map(([dim, pts]) => {
    const map = {};
    for (const p of pts) { map[Math.floor(new Date(p.collected_at).getTime() / bMs) * bMs] = p.value; }
    const meta = COMP_META[dim] || { label: dim, color: '#8b5cf6' };
    return {
      label: meta.label,
      data: grid.map(t => map[t] ?? null),
      borderColor: meta.color,
      backgroundColor: meta.color + '22',
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
    compChart.update('none');
    buildCompLegend();
    return;
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
  buildCompLegend();
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
    const [usedRows, availRows, cachedRows, buffersRows] = await Promise.all([
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('mem.usage_percent')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('mem.available_percent')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('mem.cached_percent')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('mem.buffers_percent')}`).then(r => r.json()),
    ]);
    if (seq !== compSeq) return;
    const toRows = (arr, dim) => safeArray(arr).map(r => ({ ...r, dimension: dim }));
    const rows = [
      ...toRows(usedRows,    'used'),
      ...toRows(availRows,   'available'),
      ...toRows(cachedRows,  'cached'),
      ...toRows(buffersRows, 'buffers'),
    ];
    renderCompChart(rows, capturedRange, capturedAnchor);
  } catch(e) { console.error(e); }
}

bindToggle('comp-range-btns', v => {
  compRange = v;
  updateCompBucketLabel();
  document.getElementById('comp-range-print').textContent = ' — ' + RANGE_LABEL[v];
  loadCompChart();
});

/* ── 30초 polling 자동 갱신 (SSE 제거) ── */
initAutoRefresh(loadSnapshot);

/* ── 기준일 초기화 ── */
initAnchor('comp-anchor');
document.getElementById('comp-anchor').addEventListener('change', () => loadCompChart());

/* ── 초기 로드 ── */
loadSnapshot();
pctLoaders.forEach(loader => { loader.bucketLabel(); loader.load(); });
updateCompBucketLabel();
loadCompChart();
