/**
 * storage 페이지 차트 로직.
 *
 * 외부 의존:
 * - ChartUtils (base.html에서 chart-utils.js 로드)
 * - Chart.js (페이지에서 chart.umd.min.js 로드)
 * - body data-server-id (E6 외부화 규약, static-assets.md)
 */
// ChartUtils — /static/js/chart-utils.js (base.html에서 로드)
const { RANGE_LABEL, AUTO_BUCKET, BUCKET_LABEL, BUCKET_MS, COLORS,
        fmtKst, getAnchorEnd, initAnchor,
        makeBucketGrid, joinToGrid, bindToggle, initSse, safeArray,
        fetchRebootEvents, applyRebootMarkers,
        buildAvgMaxDatasets, buildAvgMaxLegend } = ChartUtils;

const SERVER_ID = document.body.dataset.serverId;


// 추이 차트의 분해력 기준 (다중 device x Read/Write 다중 라인 — idle VM에서 0.1 IOPS도 보이도록).
// 진단 리포트(metrics.html)는 다른 정책: PERF_IOPS_SUGGESTED_MAX = 200 (HDD 물리 한계).
// 실데이터가 본 값을 초과하면 자동 확장 (soft ceiling).
const STORAGE_IOPS_SUGGESTED_MAX = 5;

function kbps(kb) {
  if (kb == null) return '—';
  if (kb >= 1024) return (kb / 1024).toFixed(1) + ' MBps';
  return kb.toFixed(1) + ' kBps';
}
function iops(v) { return v == null ? '—' : v.toFixed(1) + ' IOPS'; }

/* ── I/O 현황 스냅샷 ── */
async function loadIoSnapshot() {
  try {
    const res = await fetch(`/api/servers/${SERVER_ID}/metrics/latest`);
    document.getElementById('io-snapshot-loading').style.display = 'none';
    if (res.status === 404) {
      document.getElementById('io-phys-empty').style.display = '';
      return;
    }
    if (!res.ok) return;
    const data = await res.json();

    const physDisks   = data.disk_io_phys || [];

    const row = d => `<tr>
      <td>${d.device}</td>
      <td>${iops(d.read_iops)}</td><td>${iops(d.write_iops)}</td>
      <td>${kbps(d.read_kbps)}</td><td>${kbps(d.write_kbps)}</td>
    </tr>`;

    if (physDisks.length) {
      document.getElementById('io-phys-tbody').innerHTML = physDisks.map(row).join('');
      document.getElementById('io-phys-table').style.display = '';
    } else {
      document.getElementById('io-phys-empty').style.display = '';
    }
    if (data.collected_at)
      document.getElementById('io-snapshot-ts').textContent = '수집 기준: ' + fmtKst(data.collected_at);
  } catch(e) {
    document.getElementById('io-snapshot-loading').textContent = '불러오기 실패';
  }
}

/* ── I/O 추이 차트 ── */
let physRange = '15m';
let physChart = null;
let physSeq   = 0;

const fmtLabel = ChartUtils.fmtLabel;

function makeIoDatasets(avgRows, maxRows, range, anchorEnd) {
  const bucket = AUTO_BUCKET[range];
  const bMs    = BUCKET_MS[bucket];
  const grid   = makeBucketGrid(range, bucket, anchorEnd);
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const datasets = buildAvgMaxDatasets(avgRows, maxRows, bMs, grid);
  return { labels, datasets };
}

function ioChartOptions() {
  return {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode:'index', intersect:false },
    plugins: {
      legend: { display: false },
      tooltip: {
        filter: item => item.datasetIndex % 2 === 0,
        callbacks: {
          label: ctx => {
            const avgVal = ctx.parsed.y;
            if (avgVal == null) return null;
            const maxDs  = ctx.chart.data.datasets[ctx.datasetIndex + 1];
            const maxVal = maxDs?.realData?.[ctx.dataIndex];
            if (maxVal != null)
              return ` ${ctx.dataset.label}: 평균 ${avgVal.toFixed(1)} / 최대 ${maxVal.toFixed(1)} IOPS`;
            return ` ${ctx.dataset.label}: ${avgVal.toFixed(1)} IOPS`;
          },
        },
      },
    },
    scales: {
      x: { ticks:{ maxTicksLimit:12, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
      y: {
        title: { display:true, text:'IOPS', font:{size:11}, color:'#94a3b8' },
        ticks: { precision:0, font:{size:11}, color:'#64748b' },
        grid: { color:'#f1f5f9' }, beginAtZero: true, suggestedMax: STORAGE_IOPS_SUGGESTED_MAX,
      },
    },
  };
}

function renderIoChartTo(canvasId, emptyId, legendId, avgRows, maxRows, range, chartRef, anchorEnd) {
  const canvas = document.getElementById(canvasId);
  const empty  = document.getElementById(emptyId);
  if (!avgRows.length) {
    canvas.style.display = 'none'; empty.style.display = '';
    if (chartRef) { chartRef.destroy(); }
    return null;
  }
  canvas.style.display = ''; empty.style.display = 'none';
  const { labels, datasets } = makeIoDatasets(avgRows, maxRows, range, anchorEnd);
  if (chartRef) {
    chartRef.data.labels = labels; chartRef.data.datasets = datasets;
    chartRef.update('none'); return chartRef;
  }
  const chart = new Chart(canvas, { type:'line', data:{labels, datasets}, options: ioChartOptions() });
  buildAvgMaxLegend(legendId, chart, { withToggle: true });
  return chart;
}

function updatePhysBucketLabel() {
  document.getElementById('io-phys-bucket-label').textContent = BUCKET_LABEL[AUTO_BUCKET[physRange]] || '';
}
async function loadPhysChart() {
  const seq = ++physSeq;
  const capturedRange = physRange;
  const capturedAnchor = getAnchorEnd('phys-anchor');
  const bucket = AUTO_BUCKET[capturedRange];
  const mkQ = (type, agg) => {
    const p = new URLSearchParams({ metric_type: type, time_range: capturedRange, bucket, agg, device_category: 'phys' });
    if (capturedAnchor) p.append('end', capturedAnchor.toISOString());
    return p;
  };
  try {
    const [readAvg, readMax, writeAvg, writeMax] = await Promise.all([
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkQ('disk.read_iops',  'avg')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkQ('disk.read_iops',  'max')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkQ('disk.write_iops', 'avg')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkQ('disk.write_iops', 'max')}`).then(r => r.json()),
    ]);
    if (seq !== physSeq) return;
    const safe = arr => Array.isArray(arr) ? arr : [];
    const physAvgRows = [
      ...safe(readAvg).map(r  => ({ ...r, dimension: `${r.dimension} Read` })),
      ...safe(writeAvg).map(r => ({ ...r, dimension: `${r.dimension} Write` })),
    ];
    const physMaxRows = [
      ...safe(readMax).map(r  => ({ ...r, dimension: `${r.dimension} Read` })),
      ...safe(writeMax).map(r => ({ ...r, dimension: `${r.dimension} Write` })),
    ];
    if (physChart) { physChart.destroy(); physChart = null; }
    physChart = renderIoChartTo('io-phys-canvas', 'io-phys-chart-empty', 'io-phys-legend', physAvgRows, physMaxRows, capturedRange, null, capturedAnchor);
    const events = await fetchRebootEvents(SERVER_ID, capturedRange, capturedAnchor);
    if (seq !== physSeq) return;
    const grid = makeBucketGrid(capturedRange, AUTO_BUCKET[capturedRange], capturedAnchor);
    applyRebootMarkers(physChart, events, grid);
  } catch(e) { console.error(e); }
}

bindToggle('io-phys-range-btns', v => {
  physRange = v;
  updatePhysBucketLabel();
  document.getElementById('io-phys-range-print').textContent = ' — ' + RANGE_LABEL[v];
  loadPhysChart();
});
initAnchor('phys-anchor');
initAnchor('fs-anchor');
document.getElementById('phys-anchor').addEventListener('change', () => loadPhysChart());
document.getElementById('fs-anchor').addEventListener('change', () => loadFsChart());

loadIoSnapshot();
updatePhysBucketLabel();
loadPhysChart();

/* ── 파일시스템 사용량 추이 ── */
let fsRange = '15m';
let fsChart = null;
let fsSeq   = 0;

function updateFsBucketLabel() {
  document.getElementById('fs-bucket-label').textContent = BUCKET_LABEL[AUTO_BUCKET[fsRange]] || '';
}

function renderFsChart(avgRows, maxRows, range, anchorEnd) {
  const empty  = document.getElementById('fs-chart-empty');
  const canvas = document.getElementById('fs-chart-canvas');

  if (!avgRows.length) {
    canvas.style.display = 'none'; empty.style.display = '';
    if (fsChart) { fsChart.destroy(); fsChart = null; }
    buildFsLegend();
    return;
  }
  canvas.style.display = ''; empty.style.display = 'none';

  const bucket = AUTO_BUCKET[range];
  const bMs    = BUCKET_MS[bucket];
  const grid   = makeBucketGrid(range, bucket, anchorEnd);
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const datasets = buildAvgMaxDatasets(avgRows, maxRows, bMs, grid);

  if (fsChart) {
    fsChart.data.labels = labels; fsChart.data.datasets = datasets;
    fsChart.update('none');
    buildFsLegend();
    return;
  }

  fsChart = new Chart(canvas, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode:'index', intersect:false },
      plugins: {
        legend: { display: false },
        tooltip: {
          filter: item => !item.dataset.label.endsWith('__max'),
          callbacks: {
            label: ctx => {
              const avg = ctx.parsed.y;
              const maxDs = ctx.chart.data.datasets[ctx.datasetIndex + 1];
              const realMax = maxDs?.realData?.[ctx.dataIndex];
              if (realMax != null)
                return ` ${ctx.dataset.label}: 평균 ${avg?.toFixed(1)}% / 최대 ${realMax?.toFixed(1)}%`;
              return ` ${ctx.dataset.label}: ${avg?.toFixed(1)}%`;
            },
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
  buildFsLegend();
}

async function loadFsChart() {
  const seq = ++fsSeq;
  const capturedRange  = fsRange;
  const capturedAnchor = getAnchorEnd('fs-anchor');
  const mkP = agg => {
    const p = new URLSearchParams({ metric_type: 'fs.usage_percent', time_range: capturedRange, bucket: AUTO_BUCKET[capturedRange], agg });
    if (capturedAnchor) p.append('end', capturedAnchor.toISOString());
    return p;
  };
  try {
    const [avgRows, maxRows] = await Promise.all([
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('avg')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('max')}`).then(r => r.json()),
    ]);
    if (seq !== fsSeq) return;
    if (!Array.isArray(avgRows)) return;
    renderFsChart(avgRows, Array.isArray(maxRows) ? maxRows : [], capturedRange, capturedAnchor);
    const events = await fetchRebootEvents(SERVER_ID, capturedRange, capturedAnchor);
    if (seq !== fsSeq) return;
    const grid = makeBucketGrid(capturedRange, AUTO_BUCKET[capturedRange], capturedAnchor);
    applyRebootMarkers(fsChart, events, grid);
  } catch(e) { console.error(e); }
}

function buildFsLegend() {
  buildAvgMaxLegend('fs-legend', fsChart, { withToggle: true });
}

bindToggle('fs-range-btns', v => { fsRange = v; updateFsBucketLabel(); document.getElementById('fs-range-print').textContent = ' — ' + RANGE_LABEL[v]; loadFsChart(); });

updateFsBucketLabel();
loadFsChart();

/* ── SSE ── */
initSse(SERVER_ID, loadIoSnapshot);
