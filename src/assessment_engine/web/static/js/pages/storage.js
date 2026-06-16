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
        getAnchorEnd, initAnchor,
        makeBucketGrid, joinToGrid, bindToggle, initAutoRefresh, safeArray,
        buildAvgMaxDatasets, buildAvgMaxLegend } = ChartUtils;

const SERVER_ID = document.body.dataset.serverId;


// 추이 차트의 분해력 기준 (다중 device x Read/Write 다중 라인 — idle VM에서 0.1 IOPS도 보이도록).
// 진단 리포트(metrics.html)는 다른 정책: PERF_IOPS_SUGGESTED_MAX = 200 (HDD 물리 한계).
// 실데이터가 본 값을 초과하면 자동 확장 (soft ceiling).
const STORAGE_IOPS_SUGGESTED_MAX = 5;
// 처리량(kBps) 추이 분해력 — idle 환경 작은 처리량도 보이게 (IOPS 와 동일 분해력 우선 정책).
const STORAGE_KBPS_SUGGESTED_MAX = 256;

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
    const stampEl = document.getElementById('metrics-stamp');
    if (stampEl && data.collected_at)
      stampEl.textContent = '30초마다 자동 갱신 · 최근 ' + ChartUtils.fmtKst(data.collected_at);
  } catch(e) {
    document.getElementById('io-snapshot-loading').textContent = '불러오기 실패';
  }
}

/* ── I/O 추이 차트 ── */
let physRange = '15m';
let physChart = null;
let physSeq   = 0;
let kbpsRange = '15m';
let kbpsChart = null;
let kbpsSeq   = 0;

const fmtLabel = ChartUtils.fmtLabel;

function makeIoDatasets(avgRows, maxRows, range, anchorEnd) {
  const bucket = AUTO_BUCKET[range];
  const bMs    = BUCKET_MS[bucket];
  const grid   = makeBucketGrid(range, bucket, anchorEnd);
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const datasets = buildAvgMaxDatasets(avgRows, maxRows, bMs, grid);
  return { labels, datasets };
}

function ioChartOptions(opts) {
  opts = opts || {};
  const yTitle = opts.yTitle || 'IOPS';
  const sMax   = opts.suggestedMax != null ? opts.suggestedMax : STORAGE_IOPS_SUGGESTED_MAX;
  const fmt    = opts.fmt || iops;  // 값+단위 포함 (iops 또는 kbps 동적 단위)
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
              return ` ${ctx.dataset.label}: 평균 ${fmt(avgVal)} / 최대 ${fmt(maxVal)}`;
            return ` ${ctx.dataset.label}: ${fmt(avgVal)}`;
          },
        },
      },
    },
    scales: {
      x: { ticks:{ maxTicksLimit:12, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
      y: {
        ticks: { precision:0, font:{size:11}, color:'#64748b' },
        grid: { color:'#f1f5f9' }, beginAtZero: true, suggestedMax: sMax,
      },
    },
  };
}

function renderIoChartTo(canvasId, emptyId, legendId, avgRows, maxRows, range, chartRef, anchorEnd, opts) {
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
  const chart = new Chart(canvas, { type:'line', data:{labels, datasets}, options: ioChartOptions(opts) });
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

/* ── 디스크 I/O 처리량(kBps) 추이 — 위 IOPS 추이와 동일 포맷, Y축만 KB/s ── */
function updateKbpsBucketLabel() {
  document.getElementById('io-kbps-bucket-label').textContent = BUCKET_LABEL[AUTO_BUCKET[kbpsRange]] || '';
}
async function loadKbpsChart() {
  const seq = ++kbpsSeq;
  const capturedRange = kbpsRange;
  const capturedAnchor = getAnchorEnd('kbps-anchor');
  const bucket = AUTO_BUCKET[capturedRange];
  const mkQ = (type, agg) => {
    const p = new URLSearchParams({ metric_type: type, time_range: capturedRange, bucket, agg, device_category: 'phys' });
    if (capturedAnchor) p.append('end', capturedAnchor.toISOString());
    return p;
  };
  try {
    const [readAvg, readMax, writeAvg, writeMax] = await Promise.all([
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkQ('disk.read_kbps',  'avg')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkQ('disk.read_kbps',  'max')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkQ('disk.write_kbps', 'avg')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkQ('disk.write_kbps', 'max')}`).then(r => r.json()),
    ]);
    if (seq !== kbpsSeq) return;
    const safe = arr => Array.isArray(arr) ? arr : [];
    const kbpsAvgRows = [
      ...safe(readAvg).map(r  => ({ ...r, dimension: `${r.dimension} Read` })),
      ...safe(writeAvg).map(r => ({ ...r, dimension: `${r.dimension} Write` })),
    ];
    const kbpsMaxRows = [
      ...safe(readMax).map(r  => ({ ...r, dimension: `${r.dimension} Read` })),
      ...safe(writeMax).map(r => ({ ...r, dimension: `${r.dimension} Write` })),
    ];
    if (kbpsChart) { kbpsChart.destroy(); kbpsChart = null; }
    kbpsChart = renderIoChartTo('io-kbps-canvas', 'io-kbps-chart-empty', 'io-kbps-legend', kbpsAvgRows, kbpsMaxRows, capturedRange, null, capturedAnchor, { yTitle: 'KB/s', suggestedMax: STORAGE_KBPS_SUGGESTED_MAX, fmt: kbps });
  } catch(e) { console.error(e); }
}

bindToggle('io-kbps-range-btns', v => {
  kbpsRange = v;
  updateKbpsBucketLabel();
  document.getElementById('io-kbps-range-print').textContent = ' — ' + RANGE_LABEL[v];
  loadKbpsChart();
});
initAnchor('kbps-anchor');
document.getElementById('kbps-anchor').addEventListener('change', () => loadKbpsChart());
updateKbpsBucketLabel();
loadKbpsChart();

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
  } catch(e) { console.error(e); }
}

function buildFsLegend() {
  buildAvgMaxLegend('fs-legend', fsChart, { withToggle: true });
}

bindToggle('fs-range-btns', v => { fsRange = v; updateFsBucketLabel(); document.getElementById('fs-range-print').textContent = ' — ' + RANGE_LABEL[v]; loadFsChart(); });

updateFsBucketLabel();
loadFsChart();

/* ── 30초 polling 자동 갱신 (SSE 제거) ── */
initAutoRefresh(loadIoSnapshot);
