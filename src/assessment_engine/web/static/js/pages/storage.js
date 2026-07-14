// @ts-check
/**
 * storage 페이지 차트 로직.
 *
 * 외부 의존:
 * - ChartUtils (base.html에서 chart-utils.js 로드)
 * - Chart.js (페이지에서 chart.umd.min.js 로드)
 * - body data-server-id (E6 외부화 규약, static-assets.md)
 *
 * 시간축: 페이지 단일 range + anchor(#F10) — 2 차트(처리량·스토리지 용량)가 pageTimeControl 하나를 공유해
 * 같은 창·시점으로 그려진다(신호 간 시점 상관).
 */
// ChartUtils — /static/js/chart-utils.js (base.html에서 로드)
const { RANGE_LABEL, AUTO_BUCKET, BUCKET_LABEL, BUCKET_MS,
        makeBucketGrid, pageTimeControl, initAutoRefresh, safeArray,
        buildAvgMaxDatasets, buildAvgMaxLegend, buildDimDatasets, renderChipLegend } = ChartUtils;

const SERVER_ID = document.body.dataset.serverId;

/** @param {number | null | undefined} v */
function pct(v) { return v == null ? '—' : v.toFixed(1) + '%'; }


// 처리량(kBps) 추이 분해력 — idle 환경 작은 처리량도 보이게. 물리 디바이스 수 무관 Read/Write 합산 1선씩이라
// (collapse=true, 디바이스 많아도 안 지저분해짐) 값 자체는 여전히 작을 수 있어 기존 분해력 유지.
// 실데이터가 본 값을 초과하면 자동 확장 (soft ceiling).
const STORAGE_KBPS_SUGGESTED_MAX = 256;

// Read/Write 합산 처리량 차트 공용 메타 — 물리 디바이스 개수 무관 항상 2선.
const IO_DIM_META = {
  read:  { label: 'Read',  color: /** @type {any} */ (ChartUtils.themeColor)() },
  write: { label: 'Write', color: '#f59e0b' },
};

/** @param {number|null|undefined} kb */
function kbps(kb) {
  if (kb == null) return '—';
  // 단위 표기 "kB/s"/"MB/s" 통일 (chart-utils·format_net_rate·detail/network 와 동일 관습).
  if (kb >= 1024) return (kb / 1024).toFixed(1) + ' MB/s';
  return kb.toFixed(1) + ' kB/s';
}
/** @param {number|null|undefined} v */
function iops(v) { return v == null ? '—' : v.toFixed(1) + ' IOPS'; }

/* ── 스냅샷 (전체 사용률·포화·디바이스별 I/O) ── */
async function loadSnapshot() {
  try {
    const res = await fetch(`/api/servers/${SERVER_ID}/metrics/latest`);
    /** @type {HTMLElement} */ (document.getElementById('snap-loading')).style.display = 'none';
    if (res.status === 404) { /** @type {HTMLElement} */ (document.getElementById('snap-empty')).style.display = ''; return; }
    if (!res.ok) return;
    /** @type {import('../generated/api').components['schemas']['MetricDashboard']} */
    const data = await res.json();

    ChartUtils.setValText(document.getElementById('s-disk-usage'), pct(data.disk_usage_pct));

    const physDisks = data.disk_io || [];
    const row = (/** @type {NonNullable<import('../generated/api').components['schemas']['MetricDashboard']['disk_io']>[number]} */ d) => `<tr>
      <td>${d.device}</td>
      <td>${iops(d.read_iops)}</td><td>${iops(d.write_iops)}</td>
      <td>${kbps(d.read_kbps)}</td><td>${kbps(d.write_kbps)}</td>
    </tr>`;

    if (physDisks.length) {
      /** @type {HTMLElement} */ (document.getElementById('io-phys-tbody')).innerHTML = physDisks.map(row).join('');
      /** @type {HTMLElement} */ (document.getElementById('io-phys-table')).style.display = '';
      /** @type {HTMLElement} */ (document.getElementById('io-phys-empty')).style.display = 'none';
    } else {
      /** @type {HTMLElement} */ (document.getElementById('io-phys-table')).style.display = 'none';
      /** @type {HTMLElement} */ (document.getElementById('io-phys-empty')).style.display = '';
    }
    // 포화 스냅샷 신호 — 서버가 os-aware 판정(값·임계·saturated·4상태)을 끝낸 구조화 신호를 공통 렌더만(P4).
    // JS os 분기·임계 재계산 없음(SignalUtils). 근거(metric·임계)는 각 항목 hover.
    SignalUtils.renderSaturation(document.getElementById('disk-sat-signals'), data.disk_saturation);

    /** @type {HTMLElement} */ (document.getElementById('snap-body')).style.display = '';
  } catch(e) {
    /** @type {HTMLElement} */ (document.getElementById('snap-loading')).textContent = '불러오기 실패';
    console.error(e);
  }
}

/* ── I/O 추이 차트 — Read/Write 합산 2선(collapse=true, 물리 디바이스 개수 무관 항상 2선). 개별 디바이스
   활동은 위 실시간 카드 "디바이스별 I/O" 표가 담당(CPU 코어별 그리드와 동일 원칙: 추이=집계, 실시간=개별). ── */
let kbpsChart = /** @type {any} */ (null);
let kbpsSeq   = 0;

const fmtLabel = ChartUtils.fmtLabel;

/**
 * @param {string} canvasId
 * @param {string} emptyId
 * @param {string} legendId
 * @param {any} chartRef
 * @param {any[]} rows
 * @param {string} range
 * @param {Date|null} anchorEnd
 * @param {{suggestedMax: number, fmt: (v: number|null|undefined) => string}} opts
 */
function renderIoDimChart(canvasId, emptyId, legendId, chartRef, rows, range, anchorEnd, opts) {
  const canvas = document.getElementById(canvasId);
  const empty  = document.getElementById(emptyId);
  if (!canvas || !empty) return chartRef;
  if (!rows.length) {
    canvas.style.display = 'none'; empty.style.display = '';
    if (chartRef) { chartRef.destroy(); }
    return null;
  }
  canvas.style.display = ''; empty.style.display = 'none';
  const bucket = AUTO_BUCKET[range];
  const bMs    = BUCKET_MS[bucket];
  const grid   = /** @type {any} */ (makeBucketGrid)(range, bucket, anchorEnd);
  const labels = grid.map((/** @type {number} */ t) => fmtLabel(new Date(t).toISOString(), range));
  const datasets = buildDimDatasets(rows, bMs, grid, IO_DIM_META, { pointRadius: 1 });
  if (chartRef) {
    chartRef.data.labels = labels; chartRef.data.datasets = datasets;
    chartRef.update('none');
    renderChipLegend(document.getElementById(legendId), chartRef);
    return chartRef;
  }
  const chart = new Chart(canvas, /** @type {any} */ ({
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode:'index', intersect:false },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (/** @type {any} */ ctx) => ` ${ctx.dataset.label}: ${opts.fmt(ctx.parsed.y)}` } },
      },
      scales: {
        x: { ticks:{ maxTicksLimit:12, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
        y: {
          ticks: { callback: (/** @type {any} */ v) => opts.fmt(v), font:{size:11}, color:'#64748b' },
          grid: { color:'#f1f5f9' }, beginAtZero: true, suggestedMax: opts.suggestedMax,
        },
      },
    },
  }));
  renderChipLegend(document.getElementById(legendId), chart);
  return chart;
}

async function loadKbpsChart() {
  const seq = ++kbpsSeq;
  const capturedRange = timeCtl.getRange();
  const capturedAnchor = timeCtl.getAnchor();
  const bucket = AUTO_BUCKET[capturedRange];
  const mkQ = (/** @type {string} */ type) => {
    const p = new URLSearchParams({ metric_type: type, time_range: capturedRange, bucket, agg: 'avg', collapse: 'true' });
    if (capturedAnchor) p.append('end', capturedAnchor.toISOString());
    return p;
  };
  try {
    const [readRows, writeRows] =
      /** @type {import('../generated/api').components['schemas']['MetricSeriesItem'][][]} */
      (await Promise.all([
        fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkQ('disk.read_kbps')}`).then(r => r.json()),
        fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkQ('disk.write_kbps')}`).then(r => r.json()),
      ]));
    if (seq !== kbpsSeq) return;
    const rows = [
      ...safeArray(readRows).map(r  => ({ ...r, dimension: 'read' })),
      ...safeArray(writeRows).map(r => ({ ...r, dimension: 'write' })),
    ];
    kbpsChart = renderIoDimChart(
      'io-kbps-canvas', 'io-kbps-chart-empty', 'io-kbps-legend', kbpsChart, rows, capturedRange, capturedAnchor,
      { suggestedMax: STORAGE_KBPS_SUGGESTED_MAX, fmt: kbps },
    );
  } catch(e) { console.error(e); }
}

/* ── 스토리지 용량 추이 ── */
let fsChart = /** @type {any} */ (null);
let fsSeq   = 0;

/**
 * @param {any[]} avgRows
 * @param {any[]} maxRows
 * @param {string} range
 * @param {Date|null} anchorEnd
 */
function renderFsChart(avgRows, maxRows, range, anchorEnd) {
  const empty  = document.getElementById('fs-chart-empty');
  const canvas = document.getElementById('fs-chart-canvas');
  if (!canvas || !empty) return;

  if (!avgRows.length) {
    canvas.style.display = 'none'; empty.style.display = '';
    if (fsChart) { fsChart.destroy(); fsChart = null; }
    buildFsLegend();
    return;
  }
  canvas.style.display = ''; empty.style.display = 'none';

  const bucket = AUTO_BUCKET[range];
  const bMs    = BUCKET_MS[bucket];
  const grid   = /** @type {any} */ (makeBucketGrid)(range, bucket, anchorEnd);
  const labels = grid.map((/** @type {number} */ t) => fmtLabel(new Date(t).toISOString(), range));
  const datasets = buildAvgMaxDatasets(avgRows, maxRows, bMs, grid);

  if (fsChart) {
    fsChart.data.labels = labels; fsChart.data.datasets = datasets;
    fsChart.update('none');
    buildFsLegend();
    return;
  }

  fsChart = new Chart(canvas, /** @type {any} */ ({
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode:'index', intersect:false },
      plugins: {
        legend: { display: false },
        tooltip: {
          filter: (/** @type {any} */ item) => !item.dataset.label.endsWith('__max'),
          callbacks: {
            label: (/** @type {any} */ ctx) => {
              const avg = ctx.parsed.y;
              const maxDs = ctx.chart.data.datasets[ctx.datasetIndex + 1];
              const realMax = maxDs?.realData?.[ctx.dataIndex];
              if (realMax != null)
                return ` ${ctx.dataset.label}: 평균 ${avg?.toFixed(1)}% | 최대 ${realMax?.toFixed(1)}%`;
              return ` ${ctx.dataset.label}: ${avg?.toFixed(1)}%`;
            },
          },
        },
      },
      scales: {
        x: { ticks:{ maxTicksLimit:12, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
        // Y축 하단 0% 고정 + 상단 자동 — 데이터 실제 범위에 맞춰 분해력 확보(마운트가 여러 개일 때 각자
        // 사용률 밴드가 다 눌려 보이는 문제 방지), 최솟값만 0 고정해 "사용률" 축임은 항상 명확.
        y: {
          ticks: { callback: (/** @type {any} */ v) => Number(v).toFixed(1) + '%', font:{size:11}, color:'#64748b' },
          grid:  { color:'#f1f5f9' },
          beginAtZero: true,
        },
      },
    },
  }));
  buildFsLegend();
}

async function loadFsChart() {
  const seq = ++fsSeq;
  const capturedRange  = timeCtl.getRange();
  const capturedAnchor = timeCtl.getAnchor();
  const mkP = (/** @type {string} */ agg, /** @type {boolean} */ collapse) => {
    const p = new URLSearchParams({ metric_type: 'fs.usage_percent', time_range: capturedRange, bucket: AUTO_BUCKET[capturedRange], agg, collapse: String(collapse) });
    if (capturedAnchor) p.append('end', capturedAnchor.toISOString());
    return p;
  };
  try {
    // 전체 사용률(collapse=true, 마운트 합산 1선) + 마운트별 사용률(collapse=false) 함께 — "전체" 선이 실제
    // 서버 스토리지 사용률(fs_total_gb 대비), 마운트별 선은 세부 breakdown.
    const [ovAvg, ovMax, mtAvg, mtMax] =
      /** @type {import('../generated/api').components['schemas']['MetricSeriesItem'][][]} */
      (await Promise.all([
        fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('avg', true)}`).then(r => r.json()),
        fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('max', true)}`).then(r => r.json()),
        fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('avg', false)}`).then(r => r.json()),
        fetch(`/api/servers/${SERVER_ID}/metrics/chart?${mkP('max', false)}`).then(r => r.json()),
      ]));
    if (seq !== fsSeq) return;
    const tag = (/** @type {any} */ arr, /** @type {string} */ dim) => safeArray(arr).map((/** @type {any} */ r) => ({ ...r, dimension: dim }));
    const avgRows = [...tag(ovAvg, '전체'), ...safeArray(mtAvg)];
    const maxRows = [...tag(ovMax, '전체'), ...safeArray(mtMax)];
    renderFsChart(avgRows, maxRows, capturedRange, capturedAnchor);
  } catch(e) { console.error(e); }
}

function buildFsLegend() {
  buildAvgMaxLegend('fs-legend', fsChart, { withToggle: true });
}

/* ── 페이지 단일 시간축 버킷 라벨·print range (모든 차트 공통 range) ── */
function updateBucketLabels() {
  const r = timeCtl.getRange();
  const pr = ' — ' + RANGE_LABEL[r];
  /** @param {string} id @param {string} t */
  const set = (id, t) => { const el = document.getElementById(id); if (el) el.textContent = t; };
  // 버킷은 2 차트 공통(단일 range/anchor) — 환경 성능 추이·CPU/메모리 상세와 동일 전역 라벨 1개.
  set('bucket-label', BUCKET_LABEL[AUTO_BUCKET[r]] || '');
  set('io-kbps-range-print', pr); set('fs-range-print', pr);
}

/* ── 전체 차트 reload (페이지 range/anchor 변경 시) ── */
function reloadAllCharts() {
  updateBucketLabels();
  loadKbpsChart();
  loadFsChart();
}

// 페이지 단일 시간축 컨트롤러 — range 토글 + anchor 가 2 차트 전체 구동(#F10).
const timeCtl = pageTimeControl('page-range-btns', 'page-anchor', '15m', reloadAllCharts);

/* ── 초기 로드 ── */
loadSnapshot();
reloadAllCharts();

/* ── 30초 polling 자동 갱신 (스냅샷만) ── */
initAutoRefresh(loadSnapshot);
