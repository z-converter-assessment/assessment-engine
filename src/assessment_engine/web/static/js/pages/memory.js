/**
 * memory 페이지 차트 로직.
 *
 * 외부 의존:
 * - ChartUtils (base.html에서 chart-utils.js 로드)
 * - Chart.js (페이지에서 chart.umd.min.js 로드)
 * - SERVER_ID (페이지 inline <script>가 Jinja2로 정의)
 */
// ChartUtils — /static/js/chart-utils.js (base.html에서 로드)
const { RANGE_LABEL, AUTO_BUCKET, BUCKET_LABEL, BUCKET_MS,
        fmtKst, fmtLabel, getAnchorEnd, initAnchor,
        makeBucketGrid, joinToGrid, bindToggle, initSse, safeArray,
        fetchRebootEvents, applyRebootMarkers } = ChartUtils;


// Y축 정책 B (부분절대) — Swap 차트는 낮은 사용률(보통 0~10%)이 의미 큼.
// 25를 ceiling으로 둬서 1~2% swap도 시각적으로 보이게.
const SWAP_Y_SUGGESTED_MAX = 25;

function fmtKb(kb) {
  if (kb == null) return '—';
  if (kb >= 1024 * 1024) return (kb / 1024 / 1024).toFixed(1) + ' GB';
  if (kb >= 1024)        return (kb / 1024).toFixed(1) + ' MB';
  return kb + ' KB';
}

/* ── 스냅샷 ── */
async function loadSnapshot() {
  try {
    const res = await fetch(`/api/v1/servers/${SERVER_ID}/metrics/latest`);
    document.getElementById('snap-loading').style.display = 'none';
    if (res.status === 404) { document.getElementById('snap-empty').style.display = ''; return; }
    if (!res.ok) return;
    const data = await res.json();
    const mem  = data.memory;
    const swap = data.swap;
    if (!mem) { document.getElementById('snap-empty').style.display = ''; return; }

    const usedKb = mem.total_kb != null && mem.available_kb != null ? mem.total_kb - mem.available_kb : null;
    document.getElementById('s-mem-pct').textContent     = mem.usage_pct    != null ? mem.usage_pct.toFixed(1) + '%' : '—';
    document.getElementById('s-mem-total').textContent   = fmtKb(mem.total_kb);
    document.getElementById('s-mem-used').textContent    = fmtKb(usedKb);
    document.getElementById('s-mem-avail').textContent   = fmtKb(mem.available_kb);
    document.getElementById('s-mem-cached').textContent  = fmtKb(mem.cached_kb);
    document.getElementById('s-mem-buffers').textContent = fmtKb(mem.buffers_kb);

    if (swap) {
      const swapUsedKb = swap.total_kb != null && swap.used_kb != null ? swap.used_kb : null;
      document.getElementById('s-swap-pct').textContent   = swap.usage_pct  != null ? swap.usage_pct.toFixed(1) + '%' : '—';
      document.getElementById('s-swap-total').textContent = fmtKb(swap.total_kb);
      document.getElementById('s-swap-used').textContent  = fmtKb(swapUsedKb);
    }

    if (data.collected_at) {
      document.getElementById('snap-ts').textContent = '수집 기준: ' + fmtKst(data.collected_at);
    }
    document.getElementById('snap-body').style.display = '';
  } catch(e) {
    document.getElementById('snap-loading').textContent = '불러오기 실패';
    console.error(e);
  }
}

/* ── 단일 라인 차트 공통 ── */
function makeSingleChart(canvas, opts) {
  return new Chart(canvas, {
    type: 'line',
    data: {
      labels: [],
      datasets: [{
        label: opts.label,
        data: [],
        borderColor: opts.color,
        backgroundColor: opts.color + '22',
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
        tooltip: { callbacks: { label: ctx => ` ${opts.label}: ${ctx.parsed.y?.toFixed(1)}%` } },
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
}

/* ── 메모리 사용률 추이 ── */
let memRange = '15m';
let memChart = null;
let memSeq   = 0;

function updateMemBucketLabel() {
  document.getElementById('mem-bucket-label').textContent = BUCKET_LABEL[AUTO_BUCKET[memRange]] || '';
}

async function loadMemChart() {
  const seq = ++memSeq;
  const capturedRange  = memRange;
  const capturedAnchor = getAnchorEnd('mem-anchor');
  const canvas = document.getElementById('mem-canvas');
  const empty  = document.getElementById('mem-empty');
  const mkP = agg => {
    const p = new URLSearchParams({ metric_type: 'mem.usage_percent', time_range: capturedRange, bucket: AUTO_BUCKET[capturedRange], agg });
    if (capturedAnchor) p.append('end', capturedAnchor.toISOString());
    return p;
  };
  try {
    const [avgRows, maxRows] = await Promise.all([
      fetch(`/api/v1/servers/${SERVER_ID}/metrics/chart?${mkP('avg')}`).then(r => r.json()),
      fetch(`/api/v1/servers/${SERVER_ID}/metrics/chart?${mkP('max')}`).then(r => r.json()),
    ]);
    if (seq !== memSeq) return;
    if (!Array.isArray(avgRows) || !avgRows.length) {
      canvas.style.display = 'none'; empty.style.display = '';
      if (memChart) { memChart.destroy(); memChart = null; }
      return;
    }
    canvas.style.display = ''; empty.style.display = 'none';
    const bMs  = BUCKET_MS[AUTO_BUCKET[capturedRange]];
    const grid = makeBucketGrid(capturedRange, AUTO_BUCKET[capturedRange], capturedAnchor);
    const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), capturedRange));

    const avgMap = {}, maxMap = {};
    for (const r of avgRows) avgMap[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;
    for (const r of maxRows) maxMap[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;

    const avgData = grid.map(t => avgMap[t] ?? null);
    const realMaxData = grid.map(t => maxMap[t] ?? null);
    const bufferedMaxData = grid.map(t => {
      const a = avgMap[t];
      if (a == null) return null;
      return maxMap[t] ?? a;
    });

    if (memChart) {
      memChart.data.labels = labels;
      memChart.data.datasets[0].data = avgData;
      memChart.data.datasets[1].data = bufferedMaxData;
      memChart.data.datasets[1].realData = realMaxData;
      memChart.update('none');
    } else {
      memChart = _newMemChart(canvas, labels, avgData, bufferedMaxData, realMaxData);
    }
    const events = await fetchRebootEvents(SERVER_ID, capturedRange, capturedAnchor);
    if (seq !== memSeq) return;
    applyRebootMarkers(memChart, events, grid);
  } catch(e) { console.error(e); }
}

function _newMemChart(canvas, labels, avgData, bufferedMaxData, realMaxData) {
    return new Chart(canvas, {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: '평균',
            data: avgData,
            borderColor: '#3b82f6',
            backgroundColor: '#3b82f628',
            borderWidth: 2,
            pointRadius: 1,
            pointHoverRadius: 3,
            tension: 0.3,
            fill: '+1',
            spanGaps: false,
          },
          {
            label: '최대',
            data: bufferedMaxData,
            realData: realMaxData,
            borderColor: 'transparent',
            backgroundColor: 'transparent',
            borderWidth: 0,
            pointRadius: 0,
            pointHoverRadius: 0,
            tension: 0.3,
            fill: false,
            spanGaps: false,
          },
        ],
      },
      options: {
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
                const maxDs = memChart?.data.datasets[ctx.datasetIndex + 1];
                const realMax = maxDs?.realData?.[ctx.dataIndex];
                if (realMax != null)
                  return ` 메모리 사용률: 평균 ${avg?.toFixed(1)}% / 최대 ${realMax?.toFixed(1)}%`;
                return ` 메모리 사용률: ${avg?.toFixed(1)}%`;
              }
            },
          },
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
}

bindToggle('mem-range-btns', v => { memRange = v; updateMemBucketLabel(); document.getElementById('mem-range-print').textContent = ' — ' + RANGE_LABEL[v]; loadMemChart(); });

/* ── 메모리 구성 추이 (used / cached / buffers %) ── */
let compRange = '15m';
let compChart = null;
let compSeq   = 0;

function updateCompBucketLabel() {
  document.getElementById('comp-bucket-label').textContent = BUCKET_LABEL[AUTO_BUCKET[compRange]] || '';
}

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

  const COMP_META = {
    used:      { label: 'Used',      color: '#3b82f6' },
    available: { label: 'Available', color: '#8b5cf6' },
    cached:    { label: 'Cached',    color: '#22c55e' },
    buffers:   { label: 'Buffers',   color: '#f59e0b' },
  };
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
  const container = document.getElementById('comp-legend');
  if (!compChart) { container.innerHTML = ''; return; }
  container.innerHTML = compChart.data.datasets.map((ds, i) => `
    <label style="display:flex; align-items:center; gap:6px; font-size:12px; color:#475569; cursor:pointer; user-select:none;">
      <input type="checkbox" data-idx="${i}" checked
        style="accent-color:${ds.borderColor}; width:13px; height:13px; cursor:pointer;">
      <span>${ds.label}</span>
    </label>
  `).join('');
  container.querySelectorAll('input[type=checkbox]').forEach(cb => {
    cb.addEventListener('change', () => {
      compChart.getDatasetMeta(+cb.dataset.idx).hidden = !cb.checked;
      compChart.update();
    });
  });
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
      fetch(`/api/v1/servers/${SERVER_ID}/metrics/chart?${mkP('mem.usage_percent')}`).then(r => r.json()),
      fetch(`/api/v1/servers/${SERVER_ID}/metrics/chart?${mkP('mem.available_percent')}`).then(r => r.json()),
      fetch(`/api/v1/servers/${SERVER_ID}/metrics/chart?${mkP('mem.cached_percent')}`).then(r => r.json()),
      fetch(`/api/v1/servers/${SERVER_ID}/metrics/chart?${mkP('mem.buffers_percent')}`).then(r => r.json()),
    ]);
    if (seq !== compSeq) return;
    const toRows = (arr, dim) => Array.isArray(arr) ? arr.map(r => ({ ...r, dimension: dim })) : [];
    const rows = [
      ...toRows(usedRows,    'used'),
      ...toRows(availRows,   'available'),
      ...toRows(cachedRows,  'cached'),
      ...toRows(buffersRows, 'buffers'),
    ];
    renderCompChart(rows, capturedRange, capturedAnchor);
    const events = await fetchRebootEvents(SERVER_ID, capturedRange, capturedAnchor);
    if (seq !== compSeq) return;
    const grid = makeBucketGrid(capturedRange, AUTO_BUCKET[capturedRange], capturedAnchor);
    applyRebootMarkers(compChart, events, grid);
  } catch(e) { console.error(e); }
}

bindToggle('comp-range-btns', v => { compRange = v; updateCompBucketLabel(); document.getElementById('comp-range-print').textContent = ' — ' + RANGE_LABEL[v]; loadCompChart(); });

/* ── 스왑 사용률 추이 ── */
let swapRange = '15m';
let swapChart = null;
let swapSeq   = 0;

function updateSwapBucketLabel() {
  document.getElementById('swap-bucket-label').textContent = BUCKET_LABEL[AUTO_BUCKET[swapRange]] || '';
}

async function loadSwapChart() {
  const seq = ++swapSeq;
  const capturedRange  = swapRange;
  const capturedAnchor = getAnchorEnd('swap-anchor');
  const canvas = document.getElementById('swap-canvas');
  const empty  = document.getElementById('swap-empty');
  const mkP = agg => {
    const p = new URLSearchParams({ metric_type: 'swap.usage_percent', time_range: capturedRange, bucket: AUTO_BUCKET[capturedRange], agg });
    if (capturedAnchor) p.append('end', capturedAnchor.toISOString());
    return p;
  };
  try {
    const [avgRows, maxRows] = await Promise.all([
      fetch(`/api/v1/servers/${SERVER_ID}/metrics/chart?${mkP('avg')}`).then(r => r.json()),
      fetch(`/api/v1/servers/${SERVER_ID}/metrics/chart?${mkP('max')}`).then(r => r.json()),
    ]);
    if (seq !== swapSeq) return;
    if (!Array.isArray(avgRows) || !avgRows.length) {
      canvas.style.display = 'none'; empty.style.display = '';
      if (swapChart) { swapChart.destroy(); swapChart = null; }
      return;
    }
    canvas.style.display = ''; empty.style.display = 'none';
    const bMs  = BUCKET_MS[AUTO_BUCKET[capturedRange]];
    const grid = makeBucketGrid(capturedRange, AUTO_BUCKET[capturedRange], capturedAnchor);
    const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), capturedRange));

    const avgMap = {}, maxMap = {};
    for (const r of avgRows) avgMap[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;
    for (const r of maxRows) maxMap[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;

    const avgData = grid.map(t => avgMap[t] ?? null);
    const realMaxData = grid.map(t => maxMap[t] ?? null);
    const bufferedMaxData = grid.map(t => {
      const a = avgMap[t];
      if (a == null) return null;
      return maxMap[t] ?? a;
    });

    if (swapChart) {
      swapChart.data.labels = labels;
      swapChart.data.datasets[0].data = avgData;
      swapChart.data.datasets[1].data = bufferedMaxData;
      swapChart.data.datasets[1].realData = realMaxData;
      swapChart.update('none');
    } else {
      swapChart = new Chart(canvas, {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: '평균',
            data: avgData,
            borderColor: '#ef4444',
            backgroundColor: '#ef444428',
            borderWidth: 2,
            pointRadius: 1,
            pointHoverRadius: 3,
            tension: 0.3,
            fill: '+1',
            spanGaps: false,
          },
          {
            label: '최대',
            data: bufferedMaxData,
            realData: realMaxData,
            borderColor: 'transparent',
            backgroundColor: 'transparent',
            borderWidth: 0,
            pointRadius: 0,
            pointHoverRadius: 0,
            tension: 0.3,
            fill: false,
            spanGaps: false,
          },
        ],
      },
      options: {
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
                const maxDs = swapChart?.data.datasets[ctx.datasetIndex + 1];
                const realMax = maxDs?.realData?.[ctx.dataIndex];
                if (realMax != null)
                  return ` 스왑 사용률: 평균 ${avg?.toFixed(1)}% / 최대 ${realMax?.toFixed(1)}%`;
                return ` 스왑 사용률: ${avg?.toFixed(1)}%`;
              }
            },
          },
        },
        scales: {
          x: { ticks:{ maxTicksLimit:12, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
          y: {
            title: { display:true, text:'%', font:{size:11}, color:'#94a3b8' },
            ticks: { callback: v => v + '%', font:{size:11}, color:'#64748b' },
            grid:  { color:'#f1f5f9' },
            beginAtZero: true, suggestedMax: SWAP_Y_SUGGESTED_MAX,
          },
        },
      },
    });
    }
    const events = await fetchRebootEvents(SERVER_ID, capturedRange, capturedAnchor);
    if (seq !== swapSeq) return;
    applyRebootMarkers(swapChart, events, grid);
  } catch(e) { console.error(e); }
}

bindToggle('swap-range-btns', v => { swapRange = v; updateSwapBucketLabel(); document.getElementById('swap-range-print').textContent = ' — ' + RANGE_LABEL[v]; loadSwapChart(); });

/* ── SSE ── */
initSse(SERVER_ID, loadSnapshot);

/* ── 기준일 초기화 ── */
initAnchor('mem-anchor');
initAnchor('comp-anchor');
initAnchor('swap-anchor');
document.getElementById('mem-anchor').addEventListener('change', () => loadMemChart());
document.getElementById('comp-anchor').addEventListener('change', () => loadCompChart());
document.getElementById('swap-anchor').addEventListener('change', () => loadSwapChart());

/* ── 초기 로드 ── */
loadSnapshot();
updateMemBucketLabel();
loadMemChart();
updateCompBucketLabel();
loadCompChart();
updateSwapBucketLabel();
loadSwapChart();
