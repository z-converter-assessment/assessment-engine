// @ts-check
/**
 * memory 페이지 차트 로직.
 *
 * 외부 의존:
 * - ChartUtils (base.html에서 chart-utils.js 로드)
 * - Chart.js (페이지에서 chart.umd.min.js 로드)
 * - body data-server-id (E6 외부화 규약, static-assets.md)
 *
 * 시간축: 페이지 단일 range + anchor(#F10) — 3 차트(사용률·구성·PSI)가 pageTimeControl 하나를 공유해
 * 같은 창·시점으로 그려진다(신호 간 시점 상관).
 */

/** @typedef {import('../generated/api').components['schemas']['MetricDashboard']} MetricDashboard */
/** @typedef {import('../generated/api').components['schemas']['MetricSeriesItem']} MetricSeriesItem */

const { RANGE_LABEL, AUTO_BUCKET, BUCKET_LABEL, BUCKET_MS,
        fmtLabel, pageTimeControl,
        makeBucketGrid, joinToGrid, initAutoRefresh, safeArray,
        buildAvgMaxDatasets, buildDimDatasets, renderChipLegend } = ChartUtils;

const SERVER_ID = document.body.dataset.serverId;
const OS_FAMILY = document.body.dataset.osFamily || '';  // Windows 미측정 메트릭 N/A 분기

// 현재 상태 메모리 측정값 단위 통일 — bytes 입력 -> GB 소숫점1 고정 (인벤토리 '전체 메모리 X.X GB' 와 일관).
/** @param {number | null | undefined} bytes */
function fmtGb(bytes) {
  if (bytes == null) return '—';
  return (bytes / 1024 / 1024 / 1024).toFixed(1) + ' GB';
}

/* ── 스냅샷 ── */
async function loadSnapshot() {
  try {
    const res = await fetch(`/api/servers/${SERVER_ID}/metrics/latest`);
    /** @type {HTMLElement} */ (document.getElementById('snap-loading')).style.display = 'none';
    if (res.status === 404) { /** @type {HTMLElement} */ (document.getElementById('snap-empty')).style.display = ''; return; }
    if (!res.ok) return;
    const data = /** @type {MetricDashboard} */ (await res.json());
    const mem  = data.memory;
    if (!mem) { /** @type {HTMLElement} */ (document.getElementById('snap-empty')).style.display = ''; return; }

    /** @type {HTMLElement} */ (document.getElementById('s-mem-pct')).textContent     = mem.usage_pct    != null ? mem.usage_pct.toFixed(1) + '%' : '—';
    /** @type {HTMLElement} */ (document.getElementById('s-mem-used')).textContent    = fmtGb(mem.used_bytes);
    /** @type {HTMLElement} */ (document.getElementById('s-mem-avail')).textContent   = fmtGb(mem.available_bytes);
    /** @type {HTMLElement} */ (document.getElementById('s-mem-cached')).textContent  = /** @type {string} */ (ChartUtils.naWindows(OS_FAMILY, 'mem_cached', fmtGb(mem.cached_bytes)));
    /** @type {HTMLElement} */ (document.getElementById('s-mem-buffers')).textContent = /** @type {string} */ (ChartUtils.naWindows(OS_FAMILY, 'mem_buffers', fmtGb(mem.buffered_bytes)));

    // 포화 스냅샷 신호 — 서버가 os-aware 판정(값·임계·saturated·4상태)을 끝낸 구조화 신호를 공통 렌더만(P4).
    // JS os 분기·임계 재계산 없음(SignalUtils). 근거(metric·임계)는 각 항목 hover.
    SignalUtils.renderSaturation(document.getElementById('mem-sat-signals'), data.mem_saturation);

    /** @type {HTMLElement} */ (document.getElementById('snap-body')).style.display = '';
  } catch(e) {
    /** @type {HTMLElement} */ (document.getElementById('snap-loading')).textContent = '불러오기 실패';
    console.error(e);
  }
}

/* ── avg+max ghost 차트 (메모리 사용률 추이) ──
 * Y축 정책: mem은 분해력 우선 0~100%.
 */
const PCT_CHARTS = [
  { id: 'mem',  metric: 'mem.usage_percent',  label: '메모리 사용률', color: /** @type {any} */ (ChartUtils.themeColor)(), yMax: 100 },
];

/** @typedef {{ id: string, metric: string, label: string, color: any, yMax?: number, ySuggestedMax?: number }} PctChartDef */

/** @param {PctChartDef} def */
function makePctLoader(def) {
  /** @type {{ chart: any, seq: number }} */
  const state = { chart: null, seq: 0 };

  function makeYScale() {
    /** @type {Record<string, unknown>} */
    const y = {
      ticks: { callback: (/** @type {any} */ v) => Number(v).toFixed(1) + '%', font:{size:11}, color:'#64748b' },
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
          filter: (/** @type {any} */ item) => item.datasetIndex % 2 === 0,
          callbacks: {
            label: (/** @type {any} */ ctx) => {
              const avg = ctx.parsed.y;
              const maxDs = state.chart?.data.datasets[ctx.datasetIndex + 1];
              const realMax = maxDs?.realData?.[ctx.dataIndex];
              if (realMax != null)
                return ` ${def.label}: 평균 ${avg?.toFixed(1)}% | 최대 ${realMax?.toFixed(1)}%`;
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
    const capturedRange  = timeCtl.getRange();
    const capturedAnchor = timeCtl.getAnchor();
    const canvas = /** @type {HTMLElement} */ (document.getElementById(def.id + '-canvas'));
    const empty  = /** @type {HTMLElement} */ (document.getElementById(def.id + '-empty'));
    const bucket = AUTO_BUCKET[capturedRange];
    const mkP = (/** @type {string} */ agg) => {
      const p = new URLSearchParams({ metric_type: def.metric, time_range: capturedRange, bucket, agg });
      if (capturedAnchor) p.append('end', capturedAnchor.toISOString());
      return p;
    };
    try {
      const [avgRows, maxRows] = /** @type {[MetricSeriesItem[], MetricSeriesItem[]]} */ (await Promise.all([
        fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('avg')}`).then(r => r.json()),
        fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('max')}`).then(r => r.json()),
      ]));
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
      const grid   = /** @type {any} */ (makeBucketGrid)(capturedRange, bucket, capturedAnchor);
      const labels = grid.map((/** @type {any} */ t) => fmtLabel(new Date(t).toISOString(), capturedRange));
      const datasets = /** @type {any[]} */ (buildAvgMaxDatasets(avg, max, bMs, grid, { label: def.label, color: def.color }));

      if (state.chart) {
        state.chart.data.labels = labels;
        state.chart.data.datasets[0].data = datasets[0].data;
        state.chart.data.datasets[1].data = datasets[1].data;
        state.chart.data.datasets[1].realData = datasets[1].realData;
        state.chart.update('none');
      } else {
        state.chart = new Chart(canvas, /** @type {any} */ ({
          type: 'line',
          data: { labels, datasets },
          options: makeOptions(),
        }));
      }
    } catch(e) { console.error(e); }
  }

  return { state, load };
}

const pctLoaders = PCT_CHARTS.map(makePctLoader);

/* ── 메모리 구성 추이 (used / available / cached / buffers %) — multi-dim ── */
/** @type {any} */
let compChart = null;
let compSeq   = 0;

// Windows 는 Cached/Buffers 미측정(page-cache 세분 개념 부재) — Used/Available 만(빈 라인·범례 방지, OS 분기).
const COMP_META = {
  used:      { label: 'Used',      color: /** @type {any} */ (ChartUtils.themeColor)() },
  available: { label: 'Available', color: '#8b5cf6' },
  ...(OS_FAMILY === 'windows' ? {} : {
    cached:    { label: 'Cached',    color: '#22c55e' },
    buffers:   { label: 'Buffers',   color: '#f59e0b' },
  }),
};

/**
 * @param {any[]} rows
 * @param {string} range
 * @param {Date | null | undefined} anchorEnd
 */
function renderCompChart(rows, range, anchorEnd) {
  const canvas = /** @type {HTMLElement} */ (document.getElementById('comp-canvas'));
  const empty  = /** @type {HTMLElement} */ (document.getElementById('comp-empty'));
  if (!rows.length) {
    canvas.style.display = 'none'; empty.style.display = '';
    if (compChart) { compChart.destroy(); compChart = null; }
    buildCompLegend();
    return;
  }
  canvas.style.display = ''; empty.style.display = 'none';

  const bMs    = BUCKET_MS[AUTO_BUCKET[range]];
  const grid   = /** @type {any} */ (makeBucketGrid)(range, AUTO_BUCKET[range], anchorEnd);
  const labels = grid.map((/** @type {any} */ t) => fmtLabel(new Date(t).toISOString(), range));
  const datasets = buildDimDatasets(rows, bMs, grid, COMP_META, { pointRadius: 1 });

  if (compChart) {
    compChart.data.labels = labels; compChart.data.datasets = datasets;
    compChart.update('none');
    buildCompLegend();
    return;
  }
  compChart = new Chart(canvas, /** @type {any} */ ({
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode:'index', intersect:false },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (/** @type {any} */ ctx) => ` ${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(1)}%` } },
      },
      scales: {
        x: { ticks:{ maxTicksLimit:12, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
        y: {
          ticks: { callback: (/** @type {any} */ v) => Number(v).toFixed(1) + '%', font:{size:11}, color:'#64748b' },
          grid:  { color:'#f1f5f9' },
          beginAtZero: true,
        },
      },
    },
  }));
  buildCompLegend();
}

function buildCompLegend() {
  renderChipLegend(document.getElementById('comp-legend'), compChart);
}

async function loadCompChart() {
  const seq = ++compSeq;
  const capturedRange  = timeCtl.getRange();
  const capturedAnchor = timeCtl.getAnchor();
  const bucket = AUTO_BUCKET[capturedRange];
  const mkP = (/** @type {string} */ type) => {
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
    const [usedRows, availRows, cachedRows, buffersRows] = /** @type {MetricSeriesItem[][]} */ (await Promise.all(reqs));
    if (seq !== compSeq) return;
    const toRows = (/** @type {any} */ arr, /** @type {string} */ dim) => safeArray(arr).map((/** @type {any} */ r) => ({ ...r, dimension: dim }));
    const rows = [
      ...toRows(usedRows,    'used'),
      ...toRows(availRows,   'available'),
      ...(OS_FAMILY !== 'windows' ? [...toRows(cachedRows, 'cached'), ...toRows(buffersRows, 'buffers')] : []),
    ];
    renderCompChart(rows, capturedRange, capturedAnchor);
  } catch(e) { console.error(e); }
}

/* ── 메모리 PSI 추이 (some 정체율 %, 단일선 — Linux 전용, Windows 는 빈 결과 -> empty state) ── */
/** @type {any} */
let memPsiChart = null;
let memPsiSeq   = 0;

async function loadMemPsiChart() {
  const seq = ++memPsiSeq;
  const range  = timeCtl.getRange();
  const anchor = timeCtl.getAnchor();
  const p = new URLSearchParams({ metric_type: 'mem.psi', time_range: range, bucket: AUTO_BUCKET[range], agg: 'avg' });
  if (anchor) p.append('end', anchor.toISOString());
  const canvas = /** @type {HTMLElement} */ (document.getElementById('mem-psi-canvas'));
  const empty  = /** @type {HTMLElement} */ (document.getElementById('mem-psi-empty'));
  try {
    /** @type {import('../generated/api').components['schemas']['MetricSeriesItem'][]} */
    const rows = await fetch(`/api/servers/${SERVER_ID}/metrics/chart?${p}`).then(r => r.json());
    if (seq !== memPsiSeq) return;
    if (!Array.isArray(rows) || !rows.length) {
      // PSI 는 Windows 구조적 미지원(어느 기간을 골라도 항상 empty) — "해당 기간 데이터 없음"(일시적 뉘앙스)
      // 대신 N/A(영구 사실). Linux 는 진짜 미수집 케이스라 기존 문구 유지.
      empty.textContent = OS_FAMILY === 'windows' ? 'N/A (Windows 미지원)' : '해당 기간에 수집된 데이터가 없습니다.';
      canvas.style.display = 'none'; empty.style.display = '';
      if (memPsiChart) { memPsiChart.destroy(); memPsiChart = null; }
      return;
    }
    canvas.style.display = ''; empty.style.display = 'none';
    const bMs    = BUCKET_MS[AUTO_BUCKET[range]];
    const grid   = makeBucketGrid(range, AUTO_BUCKET[range], anchor);
    const labels = grid.map((/** @type {number} */ t) => fmtLabel(new Date(t).toISOString(), range));
    const data   = joinToGrid(grid, rows, bMs);
    if (memPsiChart) {
      memPsiChart.data.labels = labels; memPsiChart.data.datasets[0].data = data;
      memPsiChart.update('none'); return;
    }
    memPsiChart = new Chart(canvas, {
      type: 'line',
      data: { labels, datasets: [{
        label: 'PSI (정체율)', data,
        borderColor: /** @type {any} */ (ChartUtils).themeColor(),
        borderWidth: 2, pointRadius: 1, pointHoverRadius: 3, tension: 0.3, fill: false, spanGaps: false,
      }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode:'index', intersect:false },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (/** @type {any} */ ctx) => ` PSI: ${ctx.parsed.y?.toFixed(1)} %` } } },
        scales: {
          x: { ticks:{ maxTicksLimit:12, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
          y: { ticks:{ callback: v => Number(v).toFixed(1) + '%', font:{size:11}, color:'#64748b' }, grid:{ color:'#f1f5f9' }, beginAtZero: true, suggestedMax: 20 },
        },
      },
    });
  } catch(e) { console.error(e); }
}

/* ── 페이지 단일 시간축 버킷 라벨·print range (모든 차트 공통 range) ── */
function updateBucketLabels() {
  const r = timeCtl.getRange();
  const pr = ' — ' + RANGE_LABEL[r];
  /** @param {string} id @param {string} t */
  const set = (id, t) => { const el = document.getElementById(id); if (el) el.textContent = t; };
  // 버킷은 3 차트 공통(단일 range/anchor) — 환경 성능 추이·CPU 상세와 동일 전역 라벨 1개.
  set('bucket-label', BUCKET_LABEL[AUTO_BUCKET[r]] || '');
  set('mem-range-print', pr); set('comp-range-print', pr); set('mem-psi-range-print', pr);
}

/* ── 전체 차트 reload (페이지 range/anchor 변경 시) ── */
function reloadAllCharts() {
  updateBucketLabels();
  pctLoaders.forEach(loader => loader.load());
  loadCompChart();
  loadMemPsiChart();
}

// 페이지 단일 시간축 컨트롤러 — range 토글 + anchor 가 3 차트 전체 구동(#F10).
const timeCtl = pageTimeControl('page-range-btns', 'page-anchor', '15m', reloadAllCharts);

/* ── 30초 polling 자동 갱신 (스냅샷만) ── */
initAutoRefresh(loadSnapshot);

/* ── 초기 로드 ── */
loadSnapshot();
reloadAllCharts();
