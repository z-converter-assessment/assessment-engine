// @ts-check
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
        fmtKbChart, getAnchorEnd, initAnchor,
        makeBucketGrid, joinToGrid, bindToggle, initAutoRefresh, safeArray,
        buildAvgMaxDatasets, buildAvgMaxLegend } = ChartUtils;

const SERVER_ID = document.body.dataset.serverId;

// 추이 차트의 분해력 기준 (다중 interface x RX/TX 다중 라인 — idle 환경 트래픽도 보이도록).
// 진단 리포트(metrics.html)는 다른 정책: PERF_NET_SUGGESTED_MAX = 10 MB/s (1 Gbps의 8%).
// 실데이터가 본 값을 초과하면 자동 확장 (soft ceiling). Y축 단위는 fmtKbChart로 동적 표기.
const NET_Y_SUGGESTED_MAX = 2048; // B/s ≈ 2 kB/s

/** @param {number | null | undefined} v */
function fmtKbps(v) {
  if (v == null) return '—';
  // 단위 표기 "kB/s"/"MB/s" — 차트(fmtKbChart)·SSR(format_net_rate)와 통일.
  if (v >= 1024) return (v / 1024).toFixed(1) + ' MB/s';
  return v.toFixed(1) + ' kB/s';
}
/** @param {number | null | undefined} v */
function fmtPps(v) { return v != null ? v.toFixed(1) + ' pps' : '—'; }

async function loadNetSnapshot() {
  try {
    const res = await fetch(`/api/servers/${SERVER_ID}/metrics/latest`);
    /** @type {HTMLElement} */ (document.getElementById('net-snapshot-loading')).style.display = 'none';
    if (res.status === 404) {
      /** @type {HTMLElement} */ (document.getElementById('net-snapshot-empty')).style.display = '';
      return;
    }
    if (!res.ok) return;
    const data = /** @type {import('../generated/api').components['schemas']['MetricDashboard']} */ (await res.json());
    // TCP 재전송율 — 네트워크 품질 신호(양 OS 공통, 1% 초과 혼잡). 인터페이스 I/O 유무와 별개라 가드 이전 세팅.
    const retransEl = document.getElementById('s-net-retrans');
    if (retransEl) retransEl.textContent = data.net_retrans_pct != null ? data.net_retrans_pct.toFixed(2) + '%' : '—';
    const netIo = data.net_io || [];
    if (!netIo.length) {
      /** @type {HTMLElement} */ (document.getElementById('net-snapshot-empty')).style.display = '';
      return;
    }
    /** @type {HTMLElement} */ (document.getElementById('net-snapshot-tbody')).innerHTML = netIo.map(iface => `
      <tr>
        <td>${iface.interface}</td>
        <td>${fmtKbps(iface.rx_kbps)}</td>
        <td>${fmtKbps(iface.tx_kbps)}</td>
        <td>${fmtPps(iface.rx_pps)}</td>
        <td>${fmtPps(iface.tx_pps)}</td>
      </tr>
    `).join('');
    /** @type {HTMLElement} */ (document.getElementById('net-snapshot-table')).style.display = '';
    const stampEl = document.getElementById('metrics-stamp');
    if (stampEl && data.collected_at) stampEl.textContent = '30초마다 자동 갱신 · 최근 ' + ChartUtils.fmtKst(data.collected_at);
  } catch(e) { console.error(e); }
}
let netRange = '15m';
let netPpsRange = '15m';
/** @type {import('chart.js').Chart | null} */
let netChart = null;
/** @type {import('chart.js').Chart | null} */
let netPpsChart = null;
let netSeq    = 0;
let netPpsSeq = 0;

function updateNetBucketLabel() {
  /** @type {HTMLElement} */ (document.getElementById('net-bucket-label')).textContent = BUCKET_LABEL[AUTO_BUCKET[netRange]] || '';
}
function updateNetPpsBucketLabel() {
  /** @type {HTMLElement} */ (document.getElementById('net-pps-bucket-label')).textContent = BUCKET_LABEL[AUTO_BUCKET[netPpsRange]] || '';
}

/** @typedef {import('../generated/api').components['schemas']['MetricSeriesItem']} MetricSeriesItem */

const fmtNetLabel = ChartUtils.fmtLabel;
/** @type {(arr: any) => any[]} */
const safeArr = arr => Array.isArray(arr) ? arr : [];

// avg·max 를 인터페이스별 RX/TX 인접 순으로 정렬 (모든 인터페이스 유지 — 데이터 숨김 X).
// avg/max 가 동일 인터페이스 집합을 쓰도록 함께 처리 (avg+max 쌍 어긋남 방지).
/**
 * @param {MetricSeriesItem[]} rxAvg
 * @param {MetricSeriesItem[]} txAvg
 * @param {MetricSeriesItem[]} rxMax
 * @param {MetricSeriesItem[]} txMax
 */
function ifaceOrderedRows(rxAvg, txAvg, rxMax, txMax) {
  const ra = safeArr(rxAvg), ta = safeArr(txAvg), rm = safeArr(rxMax), tm = safeArr(txMax);
  const ifaces = [...new Set([...ra, ...ta].map(r => r.dimension))].sort();
  /** @type {(rows: any[], suffix: string, iface: string) => any[]} */
  const pick = (rows, suffix, iface) => rows.filter(r => r.dimension === iface).map(r => ({ ...r, dimension: `${iface} ${suffix}` }));
  const avg = ifaces.flatMap(i => [...pick(ra, 'RX', i), ...pick(ta, 'TX', i)]);
  const max = ifaces.flatMap(i => [...pick(rm, 'RX', i), ...pick(tm, 'TX', i)]);
  return { avg, max };
}

/**
 * @typedef {object} NetChartSpec
 * @property {string} canvasId
 * @property {string} emptyId
 * @property {string} legendId
 * @property {() => any} get
 * @property {(c: import('chart.js').Chart | null) => void} set
 * @property {(v: any) => string} fmt
 * @property {string} yTitle
 * @property {number} suggestedMax
 */

// bytes/pps 공용 차트 렌더 — spec 으로 단위(fmt)·Y축 제목·chart 인스턴스 참조 분기.
/**
 * @param {NetChartSpec} spec
 * @param {any[]} avgRows
 * @param {any[]} maxRows
 * @param {string} range
 * @param {Date | null} anchorEnd
 */
function renderNetChartOne(spec, avgRows, maxRows, range, anchorEnd) {
  const empty  = /** @type {HTMLElement} */ (document.getElementById(spec.emptyId));
  const canvas = /** @type {HTMLElement} */ (document.getElementById(spec.canvasId));
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
  // globals.d.ts makeBucketGrid 선언(number,number,number)이 실제(rangeKey,bucketKey,anchorEnd)와 불일치 — 로컬 캐스트.
  const grid   = /** @type {(rangeKey: string, bucketKey: string, anchorEnd: Date | null) => number[]} */ (/** @type {unknown} */ (makeBucketGrid))(range, AUTO_BUCKET[range], anchorEnd);
  const labels = grid.map(t => fmtNetLabel(new Date(t).toISOString(), range));
  const datasets = /** @type {any} */ (buildAvgMaxDatasets(avgRows, maxRows, bMs, grid));
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
          ticks: { callback: v => spec.fmt(v), font:{size:11}, color:'#64748b' },
          grid:  { color:'#f1f5f9' },
          beginAtZero: true,
          suggestedMax: spec.suggestedMax,
        },
      },
    },
  }));
}

/** @type {(v: number) => string} */
const fmtPpsChart = v => v.toFixed(1) + ' pps';
const BYTES_SPEC = /** @type {NetChartSpec} */ ({
  canvasId:'net-chart-canvas', emptyId:'net-chart-empty', legendId:'net-legend',
  get:()=>netChart, set:c=>{netChart=c;}, fmt:fmtKbChart, yTitle:'처리량', suggestedMax:NET_Y_SUGGESTED_MAX,
});
const PPS_SPEC = /** @type {NetChartSpec} */ ({
  canvasId:'net-pps-canvas', emptyId:'net-pps-empty', legendId:'net-pps-legend',
  get:()=>netPpsChart, set:c=>{netPpsChart=c;}, fmt:fmtPpsChart, yTitle:'pps', suggestedMax:10,
});

async function loadNetChart() {
  const seq = ++netSeq;
  const capturedRange = netRange;
  // globals.d.ts getAnchorEnd 선언(()->string|null)이 실제((inputId)->Date|null)와 불일치 — 로컬 캐스트.
  const capturedAnchor = /** @type {(inputId: string) => (Date | null)} */ (getAnchorEnd)('net-anchor');
  const bucket = AUTO_BUCKET[capturedRange];
  /** @type {(type: string, agg: string) => URLSearchParams} */
  const mkP = (type, agg) => {
    const p = new URLSearchParams({ metric_type: type, time_range: capturedRange, bucket, agg });
    if (capturedAnchor) p.append('end', capturedAnchor.toISOString());
    return p;
  };
  try {
    const [rxAvg, rxMax, txAvg, txMax] = /** @type {import('../generated/api').components['schemas']['MetricSeriesItem'][][]} */ (await Promise.all([
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('net.rx_bytes_per_sec', 'avg')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('net.rx_bytes_per_sec', 'max')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('net.tx_bytes_per_sec', 'avg')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('net.tx_bytes_per_sec', 'max')}`).then(r => r.json()),
    ]));
    if (seq !== netSeq) return;
    const bytesRows = ifaceOrderedRows(rxAvg, txAvg, rxMax, txMax);
    renderNetChartOne(BYTES_SPEC, bytesRows.avg, bytesRows.max, capturedRange, capturedAnchor);
    buildAvgMaxLegend(BYTES_SPEC.legendId, netChart, { withToggle: true });
  } catch(e) {
    console.error(e);
  }
}

async function loadNetPpsChart() {
  const seq = ++netPpsSeq;
  const capturedRange = netPpsRange;
  const capturedAnchor = /** @type {(inputId: string) => (Date | null)} */ (getAnchorEnd)('net-pps-anchor');
  const bucket = AUTO_BUCKET[capturedRange];
  /** @type {(type: string, agg: string) => URLSearchParams} */
  const mkP = (type, agg) => {
    const p = new URLSearchParams({ metric_type: type, time_range: capturedRange, bucket, agg });
    if (capturedAnchor) p.append('end', capturedAnchor.toISOString());
    return p;
  };
  try {
    const [prxAvg, prxMax, ptxAvg, ptxMax] = /** @type {import('../generated/api').components['schemas']['MetricSeriesItem'][][]} */ (await Promise.all([
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('net.rx_packets_per_sec', 'avg')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('net.rx_packets_per_sec', 'max')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('net.tx_packets_per_sec', 'avg')}`).then(r => r.json()),
      fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('net.tx_packets_per_sec', 'max')}`).then(r => r.json()),
    ]));
    if (seq !== netPpsSeq) return;
    const ppsRows = ifaceOrderedRows(prxAvg, ptxAvg, prxMax, ptxMax);
    renderNetChartOne(PPS_SPEC, ppsRows.avg, ppsRows.max, capturedRange, capturedAnchor);
    buildAvgMaxLegend(PPS_SPEC.legendId, netPpsChart, { withToggle: true });
  } catch(e) {
    console.error(e);
  }
}

bindToggle('net-range-btns', v => { netRange = v; updateNetBucketLabel(); /** @type {HTMLElement} */ (document.getElementById('net-range-print')).textContent = ' — ' + RANGE_LABEL[v]; loadNetChart(); });
bindToggle('net-pps-range-btns', v => { netPpsRange = v; updateNetPpsBucketLabel(); /** @type {HTMLElement} */ (document.getElementById('net-pps-range-print')).textContent = ' — ' + RANGE_LABEL[v]; loadNetPpsChart(); });
/** @type {HTMLElement} */ (document.getElementById('net-anchor')).addEventListener('change', () => loadNetChart());
/** @type {HTMLElement} */ (document.getElementById('net-pps-anchor')).addEventListener('change', () => loadNetPpsChart());

/* ── 30초 polling 자동 갱신 (SSE 제거) ── */
initAutoRefresh(loadNetSnapshot);

// globals.d.ts initAnchor 선언(onChange fn)이 실제((inputId)->void)와 불일치 — 로컬 캐스트.
/** @type {(inputId: string) => void} */ (/** @type {unknown} */ (initAnchor))('net-anchor');
/** @type {(inputId: string) => void} */ (/** @type {unknown} */ (initAnchor))('net-pps-anchor');
updateNetBucketLabel();
updateNetPpsBucketLabel();
loadNetSnapshot();
loadNetChart();
loadNetPpsChart();
