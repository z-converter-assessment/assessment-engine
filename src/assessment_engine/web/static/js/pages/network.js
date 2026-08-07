/**
 * network 페이지 차트 로직.
 *
 * 외부 의존:
 * - ChartUtils (base.html에서 chart-utils.js 로드)
 * - Chart.js (페이지에서 chart.umd.min.js 로드)
 * - body data-server-id (E6 외부화 규약, static-assets.md)
 *
 * 시간축: 페이지 단일 range + anchor(#F10) — 2 차트(I/O·네트워크 이상 여부)가 pageTimeControl 하나를
 * 공유해 같은 창·시점으로 그려진다(신호 간 시점 상관).
 */
// ChartUtils — /static/js/chart-utils.js (base.html에서 로드)
import { RANGE_LABEL, AUTO_BUCKET, BUCKET_LABEL, BUCKET_MS, COLORS, fmtKbChart, pageTimeControl, makeBucketGrid, joinToGrid, initAutoRefresh, safeArray, buildAvgMaxDatasets, buildAvgMaxLegend } from "@/chart-utils";
import * as ChartUtils from "@/chart-utils";
import * as SignalUtils from "@/signal-utils";

const SERVER_ID = document.body.dataset.serverId;

/** @param {number | null | undefined} v */
function fmtKbps(v) {
  if (v == null) return '—';
  // 단위 표기 "kB/s"/"MB/s" — 차트(fmtKbChart)·SSR(format_net_rate)와 통일.
  if (v >= 1024) return (v / 1024).toFixed(1) + ' MB/s';
  return v.toFixed(1) + ' kB/s';
}
/** @param {number | null | undefined} v */
function fmtPps(v) { return v != null ? v.toFixed(1) + ' pps' : '—'; }

async function loadSnapshot() {
  try {
    const res = await fetch(`/api/servers/${SERVER_ID}/metrics/latest`);
    /** @type {HTMLElement} */ (document.getElementById('snap-loading')).style.display = 'none';
    if (res.status === 404) { /** @type {HTMLElement} */ (document.getElementById('snap-empty')).style.display = ''; return; }
    if (!res.ok) return;
    const data = /** @type {import('../generated/api').components['schemas']['MetricDashboard']} */ (await res.json());
    // 포화 스냅샷 신호 — 서버가 os-aware 판정(값·임계·saturated·4상태)을 끝낸 구조화 신호를 공통 렌더만(P4).
    // JS os 분기·임계 재계산 없음(SignalUtils). 근거(metric·임계)는 각 항목 hover. 인터페이스 I/O 유무와 별개라 가드 이전 세팅.
    SignalUtils.renderSaturation(document.getElementById('net-sat-signals'), data.net_saturation);
    const netIo = data.net_io || [];
    if (netIo.length) {
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
      /** @type {HTMLElement} */ (document.getElementById('net-snapshot-empty')).style.display = 'none';
    } else {
      /** @type {HTMLElement} */ (document.getElementById('net-snapshot-table')).style.display = 'none';
      /** @type {HTMLElement} */ (document.getElementById('net-snapshot-empty')).style.display = '';
    }
    /** @type {HTMLElement} */ (document.getElementById('snap-body')).style.display = '';
  } catch(e) {
    /** @type {HTMLElement} */ (document.getElementById('snap-loading')).textContent = '불러오기 실패';
    console.error(e);
  }
}
/** @type {import('chart.js').Chart | null} */
let netChart = null;
let netSeq    = 0;

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
                return ` ${ctx.dataset.label}: 평균 ${spec.fmt(avg)} | 최대 ${spec.fmt(realMax)}`;
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
        },
      },
    },
  }));
}

// 고정 상한 없음 — Chart.js 자동 스케일(beginAtZero 만 강제해 축 최솟값은 항상 0, 최댓값은 실 데이터 범위에
// 맞춰 자동 확장). 이기종 fleet/구간마다 트래픽 규모가 달라 고정 suggestedMax 는 저트래픽 구간에서 추이선이
// 바닥에 눌려 안 보이는 문제(environment-metrics.js Y_NET 과 동일 결정).
const BYTES_SPEC = /** @type {NetChartSpec} */ ({
  canvasId:'net-chart-canvas', emptyId:'net-chart-empty', legendId:'net-legend',
  get:()=>netChart, set:c=>{netChart=c;}, fmt:fmtKbChart, yTitle:'처리량',
});

async function loadNetChart() {
  const seq = ++netSeq;
  const capturedRange = timeCtl.getRange();
  const capturedAnchor = timeCtl.getAnchor();
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

/* -- 네트워크 이상 여부 추이 (이진 0/1 스텝 — right_sizing.assess_network 의 network_congested 와 동일 판정) --
 * 재전송율(>1%)·드롭율(>0.5%, 둘 다 저트래픽 게이트)·conntrack 고갈(>=0.8, 게이트 없음) OR 판정을 버킷별
 * bool_or 로 0/1 통일. backend net.congested(서버 상세 단일 시계열, 환경 net.congested_hosts 와 동일
 * 원자료·임계). TCP 재전송율·패킷 드롭율 2개 % 라인이 시각적으로 거의 겹쳐 구분 안 되던 문제도 해결.
 */
/** @type {any} */
let netCongestedChart = null;
let netCongestedSeq   = 0;

async function loadNetCongestedChart() {
  const seq = ++netCongestedSeq;
  const range  = timeCtl.getRange();
  const anchor = timeCtl.getAnchor();
  const p = new URLSearchParams({ metric_type: 'net.congested', time_range: range, bucket: AUTO_BUCKET[range], agg: 'avg' });
  if (anchor) p.append('end', anchor.toISOString());
  const canvas = /** @type {HTMLElement} */ (document.getElementById('net-congested-canvas'));
  const empty  = /** @type {HTMLElement} */ (document.getElementById('net-congested-empty'));
  try {
    /** @type {import('../generated/api').components['schemas']['MetricSeriesItem'][]} */
    const rows = await fetch(`/api/servers/${SERVER_ID}/metrics/chart?${p}`).then(r => r.json());
    if (seq !== netCongestedSeq) return;
    if (!Array.isArray(rows) || !rows.length) {
      canvas.style.display = 'none'; empty.style.display = '';
      if (netCongestedChart) { netCongestedChart.destroy(); netCongestedChart = null; }
      return;
    }
    canvas.style.display = ''; empty.style.display = 'none';
    const bMs    = BUCKET_MS[AUTO_BUCKET[range]];
    const grid   = makeBucketGrid(range, AUTO_BUCKET[range], anchor);
    const labels = grid.map((/** @type {number} */ t) => fmtNetLabel(new Date(t).toISOString(), range));
    const data   = joinToGrid(grid, rows, bMs);
    if (netCongestedChart) {
      netCongestedChart.data.labels = labels; netCongestedChart.data.datasets[0].data = data;
      netCongestedChart.update('none'); return;
    }
    netCongestedChart = new Chart(canvas, {
      type: 'line',
      data: { labels, datasets: [{
        label: '네트워크 이상', data,
        borderColor: /** @type {any} */ (ChartUtils).themeColor(),
        borderWidth: 2, pointRadius: 0, pointHoverRadius: 3, tension: 0, fill: false, spanGaps: false, stepped: 'before',
      }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode:'index', intersect:false },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (/** @type {any} */ ctx) => ` ${ctx.parsed.y >= 1 ? '이상' : '정상'}` } } },
        scales: {
          x: { ticks:{ maxTicksLimit:12, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
          y: { ticks:{ stepSize:1, callback: v => Number(v) >= 1 ? '이상' : '정상', font:{size:11}, color:'#64748b' }, grid:{ color:'#f1f5f9' }, min:0, max:1 },
        },
      },
    });
  } catch(e) { console.error(e); }
}

function updateBucketLabels() {
  const r = timeCtl.getRange();
  const pr = ' — ' + RANGE_LABEL[r];
  /** @param {string} id @param {string} t */
  const set = (id, t) => { const el = document.getElementById(id); if (el) el.textContent = t; };
  // 버킷은 2 차트 공통(단일 range/anchor) — 환경 성능 추이·CPU/메모리/스토리지 상세와 동일 전역 라벨 1개.
  set('bucket-label', BUCKET_LABEL[AUTO_BUCKET[r]] || '');
  set('net-range-print', pr); set('net-congested-range-print', pr);
}

/* -- 전체 차트 reload (페이지 range/anchor 변경 시) -- */
function reloadAllCharts() {
  updateBucketLabels();
  loadNetChart();
  loadNetCongestedChart();
}

// 페이지 단일 시간축 컨트롤러 — range 토글 + anchor 가 2 차트 전체 구동(#F10).
const timeCtl = pageTimeControl('page-range-btns', 'page-anchor', '15m', reloadAllCharts);

/* -- 30초 polling 자동 갱신 (스냅샷만) -- */
initAutoRefresh(loadSnapshot);

/* -- 초기 로드 -- */
loadSnapshot();
reloadAllCharts();
