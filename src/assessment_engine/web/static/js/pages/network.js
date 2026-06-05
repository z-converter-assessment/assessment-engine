/**
 * network 페이지 차트 로직.
 *
 * 외부 의존:
 * - ChartUtils (base.html에서 chart-utils.js 로드)
 * - Chart.js (페이지에서 chart.umd.min.js 로드)
 * - body data-server-id (E6 외부화 규약, static-assets.md)
 */
// ChartUtils — /static/js/chart-utils.js (base.html에서 로드)
const { RANGE_LABEL, AUTO_BUCKET, BUCKET_LABEL, BUCKET_MS, COLORS,
        fmtKst, fmtKbChart, getAnchorEnd, initAnchor,
        makeBucketGrid, joinToGrid, bindToggle, initSse, safeArray,
        fetchRebootEvents, applyRebootMarkers,
        buildAvgMaxDatasets, buildAvgMaxLegend } = ChartUtils;

const SERVER_ID = document.body.dataset.serverId;

// 추이 차트의 분해력 기준 (다중 interface x RX/TX 다중 라인 — idle 환경 트래픽도 보이도록).
// 진단 리포트(metrics.html)는 다른 정책: PERF_NET_SUGGESTED_MAX = 10 MB/s (1 Gbps의 8%).
// 실데이터가 본 값을 초과하면 자동 확장 (soft ceiling). Y축 단위는 fmtKbChart로 동적 표기.
const NET_Y_SUGGESTED_MAX = 2048; // B/s ≈ 2 kB/s

function fmtKbps(v) {
  if (v == null) return '—';
  if (v >= 1024) return (v / 1024).toFixed(1) + ' MBps';
  return v.toFixed(1) + ' kBps';
}
function fmtPps(v) { return v != null ? v.toFixed(1) + ' pps' : '—'; }

async function loadNetSnapshot() {
  try {
    const res = await fetch(`/api/servers/${SERVER_ID}/metrics/latest`);
    document.getElementById('net-snapshot-loading').style.display = 'none';
    if (res.status === 404) {
      document.getElementById('net-snapshot-empty').style.display = '';
      return;
    }
    if (!res.ok) return;
    const data = await res.json();
    const netIo = data.net_io || [];
    if (!netIo.length) {
      document.getElementById('net-snapshot-empty').style.display = '';
      return;
    }
    document.getElementById('net-snapshot-tbody').innerHTML = netIo.map(iface => `
      <tr>
        <td>${iface.interface}</td>
        <td>${fmtKbps(iface.rx_kbps)}</td>
        <td>${fmtKbps(iface.tx_kbps)}</td>
        <td>${fmtPps(iface.rx_pps)}</td>
        <td>${fmtPps(iface.tx_pps)}</td>
      </tr>
    `).join('');
    document.getElementById('net-snapshot-table').style.display = '';
    if (data.collected_at) document.getElementById('net-snapshot-ts').textContent = '수집 기준: ' + fmtKst(data.collected_at);
  } catch(e) { console.error(e); }
}
let netRange = '15m';
let netPpsRange = '15m';
let netChart = null;
let netPpsChart = null;
let netSeq    = 0;
let netPpsSeq = 0;

function updateNetBucketLabel() {
  document.getElementById('net-bucket-label').textContent = BUCKET_LABEL[AUTO_BUCKET[netRange]] || '';
}
function updateNetPpsBucketLabel() {
  document.getElementById('net-pps-bucket-label').textContent = BUCKET_LABEL[AUTO_BUCKET[netPpsRange]] || '';
}

const fmtNetLabel = ChartUtils.fmtLabel;
const safeArr = arr => Array.isArray(arr) ? arr : [];

// avg·max 를 인터페이스별 RX/TX 인접 순으로 정렬 (모든 인터페이스 유지 — 데이터 숨김 X).
// avg/max 가 동일 인터페이스 집합을 쓰도록 함께 처리 (avg+max 쌍 어긋남 방지).
function ifaceOrderedRows(rxAvg, txAvg, rxMax, txMax) {
  const ra = safeArr(rxAvg), ta = safeArr(txAvg), rm = safeArr(rxMax), tm = safeArr(txMax);
  const ifaces = [...new Set([...ra, ...ta].map(r => r.dimension))].sort();
  const pick = (rows, suffix, iface) => rows.filter(r => r.dimension === iface).map(r => ({ ...r, dimension: `${iface} ${suffix}` }));
  const avg = ifaces.flatMap(i => [...pick(ra, 'RX', i), ...pick(ta, 'TX', i)]);
  const max = ifaces.flatMap(i => [...pick(rm, 'RX', i), ...pick(tm, 'TX', i)]);
  return { avg, max };
}

// bytes/pps 공용 차트 렌더 — spec 으로 단위(fmt)·Y축 제목·chart 인스턴스 참조 분기.
function renderNetChartOne(spec, avgRows, maxRows, range, anchorEnd) {
  const empty  = document.getElementById(spec.emptyId);
  const canvas = document.getElementById(spec.canvasId);
  const cur    = spec.get();
  if (!avgRows.length) {
    canvas.style.display = 'none';
    empty.style.display  = '';
    if (cur) { cur.destroy(); spec.set(null); }
    return;
  }
  canvas.style.display = '';
  empty.style.display  = 'none';
  const bMs    = BUCKET_MS[AUTO_BUCKET[range]];
  const grid   = makeBucketGrid(range, AUTO_BUCKET[range], anchorEnd);
  const labels = grid.map(t => fmtNetLabel(new Date(t).toISOString(), range));
  const datasets = buildAvgMaxDatasets(avgRows, maxRows, bMs, grid);
  if (cur) {
    cur.data.labels = labels;
    cur.data.datasets = datasets;
    cur.update('none');
    return;
  }
  spec.set(new Chart(canvas, {
    type: 'line',
    data: { labels, datasets },
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
              const c = spec.get();
              const maxDs = c?.data.datasets[ctx.datasetIndex + 1];
              const realMax = maxDs?.realData?.[ctx.dataIndex];
              if (realMax != null)
                return ` ${ctx.dataset.label}: 평균 ${spec.fmt(avg)} / 최대 ${spec.fmt(realMax)}`;
              return ` ${ctx.dataset.label}: ${spec.fmt(avg)}`;
            }
          },
        },
      },
      scales: {
        x: { ticks:{ maxTicksLimit:12, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
        y: {
          title: { display:true, text:spec.yTitle, font:{size:11}, color:'#94a3b8' },
          ticks: { callback: v => spec.fmt(v), font:{size:11}, color:'#64748b' },
          grid:  { color:'#f1f5f9' },
          beginAtZero: true,
          suggestedMax: spec.suggestedMax,
        },
      },
    },
  }));
}

const fmtPpsChart = v => v.toFixed(1) + ' pps';
const BYTES_SPEC = {
  canvasId:'net-chart-canvas', emptyId:'net-chart-empty', legendId:'net-legend',
  get:()=>netChart, set:c=>{netChart=c;}, fmt:fmtKbChart, yTitle:'처리량', suggestedMax:NET_Y_SUGGESTED_MAX,
};
const PPS_SPEC = {
  canvasId:'net-pps-canvas', emptyId:'net-pps-empty', legendId:'net-pps-legend',
  get:()=>netPpsChart, set:c=>{netPpsChart=c;}, fmt:fmtPpsChart, yTitle:'pps', suggestedMax:10,
};

async function loadNetChart() {
  const seq = ++netSeq;
  const capturedRange = netRange;
  const capturedAnchor = getAnchorEnd('net-anchor');
  const bucket = AUTO_BUCKET[capturedRange];
  const mkP = (type, agg) => {
    const p = new URLSearchParams({ metric_type: type, time_range: capturedRange, bucket, agg });
    if (capturedAnchor) p.append('end', capturedAnchor.toISOString());
    return p;
  };
  try {
    const [rxAvg, rxMax, txAvg, txMax] = await Promise.all([
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('net.rx_bytes_per_sec', 'avg')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('net.rx_bytes_per_sec', 'max')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('net.tx_bytes_per_sec', 'avg')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('net.tx_bytes_per_sec', 'max')}`).then(r => r.json()),
    ]);
    if (seq !== netSeq) return;
    const bytesRows = ifaceOrderedRows(rxAvg, txAvg, rxMax, txMax);
    renderNetChartOne(BYTES_SPEC, bytesRows.avg, bytesRows.max, capturedRange, capturedAnchor);
    buildAvgMaxLegend(BYTES_SPEC.legendId, netChart, { withToggle: true });
    const events = await fetchRebootEvents(SERVER_ID, capturedRange, capturedAnchor);
    if (seq !== netSeq) return;
    const grid = makeBucketGrid(capturedRange, AUTO_BUCKET[capturedRange], capturedAnchor);
    applyRebootMarkers(netChart, events, grid);
  } catch(e) {
    console.error(e);
  }
}

async function loadNetPpsChart() {
  const seq = ++netPpsSeq;
  const capturedRange = netPpsRange;
  const capturedAnchor = getAnchorEnd('net-pps-anchor');
  const bucket = AUTO_BUCKET[capturedRange];
  const mkP = (type, agg) => {
    const p = new URLSearchParams({ metric_type: type, time_range: capturedRange, bucket, agg });
    if (capturedAnchor) p.append('end', capturedAnchor.toISOString());
    return p;
  };
  try {
    const [prxAvg, prxMax, ptxAvg, ptxMax] = await Promise.all([
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('net.rx_packets_per_sec', 'avg')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('net.rx_packets_per_sec', 'max')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('net.tx_packets_per_sec', 'avg')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('net.tx_packets_per_sec', 'max')}`).then(r => r.json()),
    ]);
    if (seq !== netPpsSeq) return;
    const ppsRows = ifaceOrderedRows(prxAvg, ptxAvg, prxMax, ptxMax);
    renderNetChartOne(PPS_SPEC, ppsRows.avg, ppsRows.max, capturedRange, capturedAnchor);
    buildAvgMaxLegend(PPS_SPEC.legendId, netPpsChart, { withToggle: true });
    const events = await fetchRebootEvents(SERVER_ID, capturedRange, capturedAnchor);
    if (seq !== netPpsSeq) return;
    const grid = makeBucketGrid(capturedRange, AUTO_BUCKET[capturedRange], capturedAnchor);
    applyRebootMarkers(netPpsChart, events, grid);
  } catch(e) {
    console.error(e);
  }
}

bindToggle('net-range-btns', v => { netRange = v; updateNetBucketLabel(); document.getElementById('net-range-print').textContent = ' — ' + RANGE_LABEL[v]; loadNetChart(); });
bindToggle('net-pps-range-btns', v => { netPpsRange = v; updateNetPpsBucketLabel(); document.getElementById('net-pps-range-print').textContent = ' — ' + RANGE_LABEL[v]; loadNetPpsChart(); });
document.getElementById('net-anchor').addEventListener('change', () => loadNetChart());
document.getElementById('net-pps-anchor').addEventListener('change', () => loadNetPpsChart());

/* ── SSE ── */
initSse(SERVER_ID, loadNetSnapshot);

initAnchor('net-anchor');
initAnchor('net-pps-anchor');
updateNetBucketLabel();
updateNetPpsBucketLabel();
loadNetSnapshot();
loadNetChart();
loadNetPpsChart();
