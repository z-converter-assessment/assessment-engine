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
        buildAvgMaxDatasets, buildDimDatasets, renderChipLegend } = ChartUtils;

const SERVER_ID = document.body.dataset.serverId;
const OS_FAMILY = document.body.dataset.osFamily || '';  // Windows 미측정 메트릭 N/A 분기

// 현재 상태 메모리 측정값 단위 통일 — bytes 입력 -> GB 소숫점1 고정 (인벤토리 '전체 메모리 X.X GB' 와 일관).
function fmtGb(bytes) {
  if (bytes == null) return '—';
  return (bytes / 1024 / 1024 / 1024).toFixed(1) + ' GB';
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
    if (!mem) { document.getElementById('snap-empty').style.display = ''; return; }

    document.getElementById('s-mem-pct').textContent     = mem.usage_pct    != null ? mem.usage_pct.toFixed(1) + '%' : '—';
    document.getElementById('s-mem-used').textContent    = fmtGb(mem.used_bytes);
    document.getElementById('s-mem-avail').textContent   = fmtGb(mem.available_bytes);
    document.getElementById('s-mem-cached').textContent  = ChartUtils.naWindows(OS_FAMILY, 'mem_cached', fmtGb(mem.cached_bytes));
    document.getElementById('s-mem-buffers').textContent = ChartUtils.naWindows(OS_FAMILY, 'mem_buffers', fmtGb(mem.buffered_bytes));

    // 메모리 압박 — 신 모델 포화 신호(스왑 점유율과 별개). 양 OS 공통 하드 페이지 폴트율(mem_pages_input_rate).
    const pressEl = document.getElementById('s-mem-pressure');
    if (pressEl) {
      pressEl.textContent = data.mem_pages_input_rate != null ? data.mem_pages_input_rate.toFixed(0) + '/s (하드폴트)' : '—';
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

/* ── avg+max ghost 차트 (메모리 사용률 추이) ──
 * Y축 정책: mem은 분해력 우선 0~100%.
 */
const PCT_CHARTS = [
  { id: 'mem',  metric: 'mem.usage_percent',  label: '메모리 사용률', color: ChartUtils.themeColor(), yMax: 100 },
];

function makePctLoader(def) {
  const state = { range: '15m', chart: null, seq: 0 };

  function bucketLabel() {
    document.getElementById(def.id + '-bucket-label').textContent = BUCKET_LABEL[AUTO_BUCKET[state.range]] || '';
  }

  function makeYScale() {
    const y = {
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

// Windows 는 Cached/Buffers 미측정(page-cache 세분 개념 부재) — Used/Available 만(빈 라인·범례 방지, OS 분기).
const COMP_META = {
  used:      { label: 'Used',      color: ChartUtils.themeColor() },
  available: { label: 'Available', color: '#8b5cf6' },
  ...(OS_FAMILY === 'windows' ? {} : {
    cached:    { label: 'Cached',    color: '#22c55e' },
    buffers:   { label: 'Buffers',   color: '#f59e0b' },
  }),
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
  const datasets = buildDimDatasets(rows, bMs, grid, COMP_META, { pointRadius: 1 });

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
    // Windows 는 cached/buffers 미측정 — fetch skip(빈 요청·빈 라인 방지, OS 분기).
    const reqs = [
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('mem.usage_percent')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('mem.available_percent')}`).then(r => r.json()),
    ];
    if (OS_FAMILY !== 'windows') {
      reqs.push(fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('mem.cached_percent')}`).then(r => r.json()));
      reqs.push(fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('mem.buffers_percent')}`).then(r => r.json()));
    }
    const [usedRows, availRows, cachedRows, buffersRows] = await Promise.all(reqs);
    if (seq !== compSeq) return;
    const toRows = (arr, dim) => safeArray(arr).map(r => ({ ...r, dimension: dim }));
    const rows = [
      ...toRows(usedRows,    'used'),
      ...toRows(availRows,   'available'),
      ...(OS_FAMILY !== 'windows' ? [...toRows(cachedRows, 'cached'), ...toRows(buffersRows, 'buffers')] : []),
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
