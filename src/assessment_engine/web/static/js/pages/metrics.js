// @ts-check
/**
 * 성능 추이 페이지 차트 로직.
 *
 * 각 상세 페이지(cpu/memory/storage/network)의 추이 차트를 자원별 카드에 모은 종합 뷰 — 4자원(CPU 2·메모리
 * 3·스토리지 2·네트워크 2, 총 9) x 자원별 상세 탭과 동일 차트 목록·OS 분기(#F9 자원 탭 차트 추가 시 본
 * 페이지도 동시 갱신 의무 — 개별 탭에만 추가하고 여기 누락하면 "모아보기"라는 페이지 존재 의미가 깨짐).
 * CPU 는 사용률+실행 큐(코어당) 2축만 — CPU 분류(Nice 포함)·CPU PSI 는 제외(cpu.js 와 동일 사유: 강도 지수
 * 보다 "코어당 실행 큐"가 os-aware 임계로 이미 판정 신호). 메모리는 사용률+구성+압박 여부(이진) 3축 — PSI
 * 대신 mem.paging_pressure(recommendation.mem_pressure_active 와 동일 판정)를 0/1 스텝으로. 스토리지는
 * 처리량(Read/Write kbps)+용량 추이(전체+마운트별 %) 2축 — IOPS·await·PSI 제외. 네트워크는 I/O+이상 여부
 * (이진, net.congested — recommendation.assess_network 의 network_congested 와 동일 판정) 2축 — PPS·
 * TCP 재전송율·패킷 드롭율은 이상 여부로 흡수.
 *
 * 외부 의존:
 * - ChartUtils (base.html에서 chart-utils.js 로드)
 * - Chart.js (페이지에서 chart.umd.min.js 로드)
 * - body data-server-id/data-os-family (E6 외부화 규약, static-assets.md)
 */
/** @typedef {import('../generated/api').components['schemas']['MetricSeriesItem']} Row */
const { AUTO_BUCKET, BUCKET_LABEL, BUCKET_MS,
        fmtKbChart, fmtThroughput, safeArray, bindToggle, renderChipLegend,
        buildAvgMaxDatasets, buildAvgMaxLegend, buildDimDatasets } = ChartUtils;

const SERVER_ID = document.body.dataset.serverId;

const PERF_DISK_KBPS_SUGGESTED_MAX = 10 * 1024;   // 10 MB/s — net 처리량 차트와 동일 절대 기준선
// 처리량 동적 단위(kBps/MBps)는 ChartUtils.fmtThroughput 단일 진실 (storage/detail/환경 추이 공용).

let globalRange = '15m';
/** @type {Record<string, any>} */
const chartInstances = {};
// P4(a) sequence counter — per-chart 분리 (다른 page 와 일관). chart 별 독립 counter.
const seqs = {
  cpu: 0, load: 0,
  mem: 0, memComp: 0, memPaging: 0,
  diskKbps: 0, fs: 0,
  netIo: 0, netCongested: 0,
};

/* ── 유틸 ──
 * P4(b) capture-before-await: 모든 함수가 range/anchor를 인자로 받음.
 */
const _safe = safeArray;
/** @param {string} iso @param {string} range */
const fmtLabel = (iso, range) => ChartUtils.fmtLabel(iso, range);
/** @param {string} range @param {Date|null} anchor */
const makeBucketGrid = (range, anchor) => ChartUtils.makeBucketGrid(range, /** @type {any} */ (AUTO_BUCKET[range]), anchor);
const getAnchorEnd = () => /** @type {any} */ (ChartUtils).getAnchorEnd('anchor-date');

// network.js 의 인터페이스 정렬·RX/TX 인접 병합 — 네트워크 I/O 차트 공용.
// avg/max 가 동일 dimension 순서를 쓰도록 함께 처리 (avg+max 쌍 어긋남 방지).
/** @param {Row[]} rxAvg @param {Row[]} txAvg @param {Row[]} rxMax @param {Row[]} txMax */
function ifaceOrderedRows(rxAvg, txAvg, rxMax, txMax) {
  const ra = _safe(rxAvg), ta = _safe(txAvg), rm = _safe(rxMax), tm = _safe(txMax);
  const ifaces = [...new Set([...ra, ...ta].map(r => r.dimension))].sort();
  /** @param {Row[]} rows @param {string} suffix @param {string|null} iface */
  const pick = (rows, suffix, iface) => rows.filter(r => r.dimension === iface).map(r => ({ ...r, dimension: `${iface} ${suffix}` }));
  const avg = ifaces.flatMap(i => [...pick(ra, 'RX', i), ...pick(ta, 'TX', i)]);
  const max = ifaces.flatMap(i => [...pick(rm, 'RX', i), ...pick(tm, 'TX', i)]);
  return { avg, max };
}

/** @param {Row[]} rows */
function computePeriodMax(rows) {
  const vals = rows.map(r => r.value).filter(v => v != null);
  return vals.length ? Math.max(...vals) : null;
}

/** @param {string} elId @param {number|null} val @param {(v: number) => string} fmtFn @param {((v: number) => string)|null} colorFn @param {string} [label] */
function updateMaxLabel(elId, val, fmtFn, colorFn, label = '최대') {
  const el = document.getElementById(elId);
  if (!el) return;
  if (val == null) { el.textContent = `${label}: —`; el.style.color = '#94a3b8'; return; }
  el.textContent = `${label}: ` + fmtFn(val);
  el.style.color = colorFn ? colorFn(val) : '#64748b';
}

/**
 * @param {string} metricType @param {string} agg @param {string} range @param {Date|null} anchor
 * @param {boolean} [collapse] dimension(device/iface/mount) 합산 1선 — 스토리지 처리량·용량 등 물리 디바이스
 *   개수 무관 항상 Read/Write 2선(storage.js 와 동일 정책, 개수 많으면 지저분해지는 문제 회피).
 * @returns {Promise<Row[]>}
 */
async function fetchChart(metricType, agg, range, anchor, collapse) {
  const p = new URLSearchParams({
    metric_type: metricType,
    time_range:  range,
    bucket:      AUTO_BUCKET[range],
    agg,
  });
  if (anchor) p.append('end', anchor.toISOString());
  if (collapse) p.append('collapse', 'true');
  const res = await fetch(`/api/servers/${SERVER_ID}/metrics/chart?${p}`);
  if (res.status === 404 || !res.ok) return [];  // P4(d): 데이터 부재 → 빈 배열
  return res.json();
}

// avg+max ghost 차트 옵션 (단일/통합 메트릭)
/** @param {any} yAxisOpts @param {(v: number) => string} fmtFn */
function makePerfOptions(yAxisOpts, fmtFn) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode:'index', intersect:false },
    plugins: {
      legend: { display: false },
      tooltip: {
        filter: /** @param {any} item */ (item) => !item.dataset.label.endsWith('__max'),
        callbacks: {
          label: /** @param {any} ctx */ (ctx) => {
            const avg = ctx.parsed.y;
            const maxDs = ctx.chart.data.datasets[ctx.datasetIndex + 1];
            const realMax = maxDs?.realData?.[ctx.dataIndex];
            const label = ctx.dataset.label ? ` ${ctx.dataset.label}:` : '';
            const avgStr = avg != null ? fmtFn(avg) : '—';
            return realMax != null
              ? `${label} 평균 ${avgStr} | 최대 ${fmtFn(realMax)}`
              : `${label} ${avgStr}`;
          }
        }
      }
    },
    scales: {
      x: { ticks:{ maxTicksLimit:10, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
      y: { ...yAxisOpts, grid:{ color:'#f1f5f9' } },
    }
  };
}

// 다중 dimension 라인 옵션 (avg-only — 메모리 구성, % Y축). memory.js renderCompChart 동일.
function makeMultiDimOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode:'index', intersect:false },
    plugins: {
      legend: { display: false },
      tooltip: { callbacks: { label: /** @param {any} ctx */ (ctx) => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? ctx.parsed.y.toFixed(1) : '—'}%` } },
    },
    scales: {
      x: { ticks:{ maxTicksLimit:10, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
      y: {
        ticks: { callback: /** @param {number} v */ (v) => Number(v).toFixed(1) + '%', font:{size:11}, color:'#64748b' },
        grid:  { color:'#f1f5f9' }, beginAtZero: true,
      },
    },
  };
}

/** @param {Row[]} avgRows @param {Row[]} maxRows @param {number} bMs @param {number[]} grid @param {string|null} labelOverride */
function buildDatasets(avgRows, maxRows, bMs, grid, labelOverride) {
  return buildAvgMaxDatasets(avgRows, maxRows, bMs, grid, { label: labelOverride, pointRadius: 0 });
}

// 단일/통합(avg+max) 차트 렌더 — 데이터 없으면 chart-empty(중앙) 표시.
/** @param {string} canvasId @param {string} emptyId @param {Row[]} avgRows @param {any} yAxisOpts @param {(v: number) => string} fmtFn @param {any[]} datasets @param {any[]} labels */
function setChart(canvasId, emptyId, avgRows, yAxisOpts, fmtFn, datasets, labels) {
  const canvas = /** @type {HTMLElement} */ (document.getElementById(canvasId));
  const empty  = /** @type {HTMLElement} */ (document.getElementById(emptyId));
  if (chartInstances[canvasId]) { chartInstances[canvasId].destroy(); delete chartInstances[canvasId]; }
  if (!avgRows.length) {
    canvas.style.display = 'none'; empty.style.display = 'flex';
    return null;
  }
  canvas.style.display = ''; empty.style.display = 'none';
  const chart = new Chart(canvas, /** @type {any} */ ({
    type: 'line',
    data: { labels, datasets },
    options: makePerfOptions(yAxisOpts, fmtFn),
  }));
  chartInstances[canvasId] = chart;
  return chart;
}

// 다중 dimension 라인(avg-only) 렌더 — 메모리 구성. renderChipLegend.
/** @param {string} canvasId @param {string} emptyId @param {string} legendId @param {Row[]} rows @param {string} range @param {Date|null} anchor @param {any} metaMap */
function renderMultiDimChart(canvasId, emptyId, legendId, rows, range, anchor, metaMap) {
  const canvas = /** @type {HTMLElement} */ (document.getElementById(canvasId));
  const empty  = /** @type {HTMLElement} */ (document.getElementById(emptyId));
  if (chartInstances[canvasId]) { chartInstances[canvasId].destroy(); delete chartInstances[canvasId]; }
  if (!rows.length) {
    canvas.style.display = 'none'; empty.style.display = 'flex';
    renderChipLegend(document.getElementById(legendId), null);
    return;
  }
  canvas.style.display = ''; empty.style.display = 'none';
  const bMs    = BUCKET_MS[AUTO_BUCKET[range]];
  const grid   = makeBucketGrid(range, anchor);
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const datasets = /** @type {any} */ (buildDimDatasets(rows, bMs, grid, metaMap));
  const chart = new Chart(canvas, /** @type {any} */ ({ type: 'line', data: { labels, datasets }, options: makeMultiDimOptions() }));
  chartInstances[canvasId] = chart;
  renderChipLegend(document.getElementById(legendId), chart);
}

// 다중 dimension 라인(avg-only, 커스텀 Y축/포맷) — 디스크 처리량(Read/Write 2선, collapse=true) 공용.
// renderMultiDimChart 와 달리 % 고정이 아니라 yAxisOpts/fmtFn 을 받는다.
/** @param {string} canvasId @param {string} emptyId @param {string} legendId @param {Row[]} rows
 *  @param {string} range @param {Date|null} anchor @param {any} metaMap @param {any} yAxisOpts @param {(v: number) => string} fmtFn */
function renderDimLineChart(canvasId, emptyId, legendId, rows, range, anchor, metaMap, yAxisOpts, fmtFn) {
  const canvas = /** @type {HTMLElement} */ (document.getElementById(canvasId));
  const empty  = /** @type {HTMLElement} */ (document.getElementById(emptyId));
  if (chartInstances[canvasId]) { chartInstances[canvasId].destroy(); delete chartInstances[canvasId]; }
  if (!rows.length) {
    canvas.style.display = 'none'; empty.style.display = 'flex';
    renderChipLegend(document.getElementById(legendId), null);
    return;
  }
  canvas.style.display = ''; empty.style.display = 'none';
  const bMs    = BUCKET_MS[AUTO_BUCKET[range]];
  const grid   = makeBucketGrid(range, anchor);
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const datasets = /** @type {any} */ (buildDimDatasets(rows, bMs, grid, metaMap, { pointRadius: 0 }));
  const chart = new Chart(canvas, /** @type {any} */ ({
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode:'index', intersect:false },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: /** @param {any} ctx */ (ctx) => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? fmtFn(ctx.parsed.y) : '—'}` } },
      },
      scales: {
        x: { ticks:{ maxTicksLimit:10, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
        y: { ...yAxisOpts, grid:{ color:'#f1f5f9' } },
      },
    },
  }));
  chartInstances[canvasId] = chart;
  renderChipLegend(document.getElementById(legendId), chart);
}

// 이진(0/1) 스텝 차트 — 메모리 압박 여부·네트워크 이상 여부 공용. cpu.saturation_hosts 류(환경 스코프,
// count) 와 동일 시각 모델을 서버 1대 단일 시계열로 적용(memory.js/network.js 와 동일 패턴).
/** @param {string} metricType @param {string} canvasId @param {string} emptyId @param {'memPaging'|'netCongested'} seqKey
 *  @param {string} label @param {string} hiText @param {string} loText @param {string} range @param {Date|null} anchor */
async function loadBinaryChart(metricType, canvasId, emptyId, seqKey, label, hiText, loText, range, anchor) {
  const seq = ++seqs[seqKey];
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const rows = _safe(await fetchChart(metricType, 'avg', range, anchor));
  if (seq !== seqs[seqKey]) return;
  const canvas = /** @type {HTMLElement} */ (document.getElementById(canvasId));
  const empty  = /** @type {HTMLElement} */ (document.getElementById(emptyId));
  if (chartInstances[canvasId]) { chartInstances[canvasId].destroy(); delete chartInstances[canvasId]; }
  if (!rows.length) {
    canvas.style.display = 'none'; empty.style.display = 'flex';
    return;
  }
  canvas.style.display = ''; empty.style.display = 'none';
  /** @type {Record<number, number|null>} */
  const map = {};
  for (const r of rows) map[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const data   = grid.map(t => map[t] ?? null);
  chartInstances[canvasId] = new Chart(canvas, /** @type {any} */ ({
    type: 'line',
    data: { labels, datasets: [{
      label, data,
      borderColor: /** @type {any} */ (ChartUtils).themeColor(),
      borderWidth: 2, pointRadius: 0, pointHoverRadius: 3, tension: 0, fill: false, spanGaps: false, stepped: 'before',
    }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode:'index', intersect:false },
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: /** @param {any} ctx */ (ctx) => ` ${ctx.parsed.y >= 1 ? hiText : loText}` } } },
      scales: {
        x: { ticks:{ maxTicksLimit:10, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
        y: { ticks:{ stepSize:1, callback: /** @param {number} v */ (v) => Number(v) >= 1 ? hiText : loText, font:{size:11}, color:'#64748b' }, grid:{ color:'#f1f5f9' }, min:0, max:1 },
      },
    },
  }));
}

/* ── Y축 설정 ── */
const pctTicks = { callback: /** @param {number} v */ (v) => Number(v).toFixed(1) + '%', font:{size:11}, color:'#64748b' };
const Y_PCT   = { min:0, max:100, ticks: pctTicks };
const Y_RUNQ  = { beginAtZero:true, suggestedMax: 2, ticks:{ font:{size:11}, color:'#64748b' } };
const Y_DISK_KBPS = { beginAtZero:true, suggestedMax: PERF_DISK_KBPS_SUGGESTED_MAX, ticks:{ callback: /** @param {number} v */ (v) => fmtThroughput(v), font:{size:11}, color:'#64748b' } };
const Y_NET   = { beginAtZero:true, ticks:{ callback: /** @param {number} v */ (v) => fmtKbChart(v), font:{size:11}, color:'#64748b' } };
// 스토리지 용량 추이 — 하단 0% 고정 + 상단 자동(storage.js 와 동일, 마운트가 여러 개일 때 각자 사용률 밴드가
// 다 눌려 보이는 문제 방지).
const Y_STORAGE_PCT = { beginAtZero:true, ticks: pctTicks };

/* ── 개별 차트 로더 (P4(a) per-chart seq) ── */

/** @param {string} range @param {Date|null} anchor */
async function loadCpuChart(range, anchor) {
  const seq = ++seqs.cpu;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const [avgRows, maxRows] = await Promise.all([fetchChart('cpu.usage_percent','avg', range, anchor), fetchChart('cpu.usage_percent','max', range, anchor)]);
  if (seq !== seqs.cpu) return;
  const safeAvg = _safe(avgRows), safeMax = _safe(maxRows);
  const datasets = buildDatasets(safeAvg, safeMax, bMs, grid, 'CPU');
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  setChart('cpu-canvas', 'cpu-empty', safeAvg, Y_PCT, v => v.toFixed(1)+'%', datasets, labels);
  updateMaxLabel('cpu-max', computePeriodMax(safeMax), v => v.toFixed(1)+'%', null);
}

// 실행 큐 추이 (cpu.run_queue os-aware 코어당) — cpu.js 의 renderLoadChart/loadLoadChart 와 동일 단일선
// 모델(D-state 는 이 카드에서 제외 — cpu.js 와 동일 사유, 근본원인은 자원 상세 탭의 "자원 이용률·포화" 카드가 전담).
const RUNQ_META = { runq: { label: '실행 큐 (코어당)', color: /** @type {any} */ (ChartUtils).themeColor() } };
/** @param {string} range @param {Date|null} anchor */
async function loadLoadChart(range, anchor) {
  const seq = ++seqs.load;
  const [runqAvg, runqMax] = await Promise.all([fetchChart('cpu.run_queue','avg', range, anchor), fetchChart('cpu.run_queue','max', range, anchor)]);
  if (seq !== seqs.load) return;
  const rows = _safe(runqAvg).map(r => ({ ...r, dimension: 'runq' }));
  renderDimLineChart('load-canvas', 'load-empty', 'load-legend', rows, range, anchor, RUNQ_META, Y_RUNQ, v => v.toFixed(2));
  updateMaxLabel('load-max', computePeriodMax(_safe(runqMax)), v => v.toFixed(2), null);
}

/** @param {string} range @param {Date|null} anchor */
async function loadMemChart(range, anchor) {
  const seq = ++seqs.mem;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const [avgRows, maxRows] = await Promise.all([fetchChart('mem.usage_percent','avg', range, anchor), fetchChart('mem.usage_percent','max', range, anchor)]);
  if (seq !== seqs.mem) return;
  const safeAvg = _safe(avgRows), safeMax = _safe(maxRows);
  const datasets = buildDatasets(safeAvg, safeMax, bMs, grid, '메모리');
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  setChart('mem-canvas', 'mem-empty', safeAvg, Y_PCT, v => v.toFixed(1)+'%', datasets, labels);
  updateMaxLabel('mem-max', computePeriodMax(safeMax), v => v.toFixed(1)+'%', null);
}

const MEMCOMP_META = {
  used:      { label: 'Used',      color: /** @type {any} */ (ChartUtils).themeColor() },
  available: { label: 'Available', color: '#8b5cf6' },
  cached:    { label: 'Cached',    color: '#22c55e' },  // Windows 미측정 → 빈 라인
  buffers:   { label: 'Buffers',   color: '#f59e0b' },  // Windows 미측정 → 빈 라인
};
/** @param {string} range @param {Date|null} anchor */
async function loadMemCompChart(range, anchor) {
  const seq = ++seqs.memComp;
  const [used, avail, cached, buffers] = await Promise.all([
    fetchChart('mem.usage_percent','avg', range, anchor),
    fetchChart('mem.available_percent','avg', range, anchor),
    fetchChart('mem.cached_percent','avg', range, anchor),
    fetchChart('mem.buffers_percent','avg', range, anchor),
  ]);
  if (seq !== seqs.memComp) return;
  const rows = [
    ..._safe(used).map(r => ({ ...r, dimension: 'used' })),
    ..._safe(avail).map(r => ({ ...r, dimension: 'available' })),
    ..._safe(cached).map(r => ({ ...r, dimension: 'cached' })),
    ..._safe(buffers).map(r => ({ ...r, dimension: 'buffers' })),
  ];
  renderMultiDimChart('memcomp-canvas', 'memcomp-empty', 'memcomp-legend', rows, range, anchor, MEMCOMP_META);
}

// 메모리 압박 여부 추이 (이진 0/1) — recommendation.mem_pressure_active 와 동일 판정을 SQL 이식한
// backend mem.paging_pressure(서버 상세 단일 시계열, 환경 mem.paging_pressure_hosts 와 동일 원자료·임계).
/** @param {string} range @param {Date|null} anchor */
async function loadMemPagingChart(range, anchor) {
  await loadBinaryChart('mem.paging_pressure', 'mempaging-canvas', 'mempaging-empty', 'memPaging',
    '메모리 압박', '압박', '정상', range, anchor);
}

// 디스크 처리량(kBps) — Read/Write 합산 2선(collapse=true, 물리 디바이스 개수 무관 항상 2선 — storage.js 와
// 동일 정책, 개별 디바이스 활동은 자원 상세 탭 자체의 실시간 카드에서만 확인).
const IO_DIM_META = {
  read:  { label: 'Read',  color: /** @type {any} */ (ChartUtils).themeColor() },
  write: { label: 'Write', color: '#f59e0b' },
};
/** @param {string} range @param {Date|null} anchor */
async function loadDiskKbpsChart(range, anchor) {
  const seq = ++seqs.diskKbps;
  const [readRows, writeRows] = await Promise.all([
    fetchChart('disk.read_kbps','avg', range, anchor, true),
    fetchChart('disk.write_kbps','avg', range, anchor, true),
  ]);
  if (seq !== seqs.diskKbps) return;
  const rows = [
    ..._safe(readRows).map(r => ({ ...r, dimension: 'read' })),
    ..._safe(writeRows).map(r => ({ ...r, dimension: 'write' })),
  ];
  renderDimLineChart('diskkbps-canvas', 'diskkbps-empty', 'diskkbps-legend', rows, range, anchor, IO_DIM_META, Y_DISK_KBPS, fmtThroughput);
}

// 스토리지 용량 추이 — 전체 사용률(collapse=true, 마운트 합산 1선) + 마운트별 사용률(collapse=false) 함께,
// storage.js loadFsChart 와 동일(#F9 자원 탭 정합). "전체" 선이 실제 서버 스토리지 사용률.
/** @param {string} range @param {Date|null} anchor */
async function loadFsChart(range, anchor) {
  const seq = ++seqs.fs;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const [ovAvg, ovMax, mtAvg, mtMax] = await Promise.all([
    fetchChart('fs.usage_percent', 'avg', range, anchor, true),
    fetchChart('fs.usage_percent', 'max', range, anchor, true),
    fetchChart('fs.usage_percent', 'avg', range, anchor, false),
    fetchChart('fs.usage_percent', 'max', range, anchor, false),
  ]);
  if (seq !== seqs.fs) return;
  const tag = (/** @type {any[]} */ arr, /** @type {string} */ dim) => _safe(arr).map(r => ({ ...r, dimension: dim }));
  const avgRows = [...tag(ovAvg, '전체'), ..._safe(mtAvg)];
  const maxRows = [...tag(ovMax, '전체'), ..._safe(mtMax)];
  const datasets = buildDatasets(avgRows, maxRows, bMs, grid, null);
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const chart = setChart('fs-canvas', 'fs-empty', avgRows, Y_STORAGE_PCT, v => v.toFixed(1)+'%', datasets, labels);
  buildAvgMaxLegend('fs-legend', chart, { withToggle: true });
  updateMaxLabel('fs-max', computePeriodMax(_safe(ovMax)), v => v.toFixed(1)+'%', null);
}

// 네트워크 I/O — iface x RX/TX 통합 bytes (network.js 모델). 고정 상한 없음 — Chart.js 자동 스케일
// (beginAtZero 만 강제, network.js 와 동일 — 이기종 fleet 트래픽 규모 차이로 고정 suggestedMax 는 저트래픽
// 환경에서 추이선이 바닥에 눌려 안 보이는 문제).
/** @param {string} range @param {Date|null} anchor */
async function loadNetIoChart(range, anchor) {
  const seq = ++seqs.netIo;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const [rxA, rxM, txA, txM] = await Promise.all([
    fetchChart('net.rx_bytes_per_sec','avg', range, anchor), fetchChart('net.rx_bytes_per_sec','max', range, anchor),
    fetchChart('net.tx_bytes_per_sec','avg', range, anchor), fetchChart('net.tx_bytes_per_sec','max', range, anchor),
  ]);
  if (seq !== seqs.netIo) return;
  const { avg, max } = ifaceOrderedRows(rxA, txA, rxM, txM);
  const datasets = buildDatasets(avg, max, bMs, grid, null);
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const chart = setChart('netio-canvas', 'netio-empty', avg, Y_NET, fmtKbChart, datasets, labels);
  buildAvgMaxLegend('netio-legend', chart, { withToggle: true });
  const rxRows = _safe(rxM).length ? _safe(rxM) : _safe(rxA);
  const txRows = _safe(txM).length ? _safe(txM) : _safe(txA);
  updateMaxLabel('netio-rx-max', computePeriodMax(rxRows), fmtKbChart, null, 'RX 최대');
  updateMaxLabel('netio-tx-max', computePeriodMax(txRows), fmtKbChart, null, 'TX 최대');
}

// 네트워크 이상 여부 추이 (이진 0/1) — recommendation.assess_network 의 network_congested 와 동일 판정을
// SQL 이식한 backend net.congested(서버 상세 단일 시계열, 환경 net.congested_hosts 와 동일 원자료·임계).
/** @param {string} range @param {Date|null} anchor */
async function loadNetCongestedChart(range, anchor) {
  await loadBinaryChart('net.congested', 'netcongested-canvas', 'netcongested-empty', 'netCongested',
    '네트워크 이상', '이상', '정상', range, anchor);
}

/* ── 전체 로드 ── */
/** @param {string} range */
function updateBucketLabel(range) {
  /** @type {HTMLElement} */ (document.getElementById('bucket-label')).textContent = BUCKET_LABEL[AUTO_BUCKET[range]] || '';
}

// 구간·앵커 select/input 은 인쇄에서 no-print(조작 컨트롤이라 무의미) — 인쇄 텍스트 대체 표시(range-print·
// anchor-print). 앵커는 getAnchorEnd 가 null(=현재 시각 이후 선택, 라이브)이면 "실시간", 과거 지정이면
// KST 값(입력 자체가 이미 KST) 그대로 표시.
/** @param {string} range @param {Date | null} anchor */
function updatePrintControlsLabel(range, anchor) {
  const select = /** @type {HTMLSelectElement} */ (document.getElementById('global-range-btns'));
  const rangeText = select.options[select.selectedIndex]?.text || range;
  /** @type {HTMLElement} */ (document.getElementById('range-print')).textContent = `구간: ${rangeText}`;
  const anchorInput = /** @type {HTMLInputElement} */ (document.getElementById('anchor-date'));
  const anchorText = anchor ? anchorInput.value.replace('T', ' ') : '실시간';
  /** @type {HTMLElement} */ (document.getElementById('anchor-print')).textContent = `기준: ${anchorText}`;
}

async function loadAllCharts() {
  // P4(b) capture-before-await: 호출 시점의 range/anchor를 캡처해 모든 로더에 전달.
  const range  = globalRange;
  const anchor = getAnchorEnd();
  updateBucketLabel(range);
  updatePrintControlsLabel(range, anchor);
  await Promise.all([
    loadCpuChart(range, anchor),        loadLoadChart(range, anchor),
    loadMemChart(range, anchor),        loadMemCompChart(range, anchor),
    loadMemPagingChart(range, anchor),
    loadDiskKbpsChart(range, anchor),   loadFsChart(range, anchor),
    loadNetIoChart(range, anchor),      loadNetCongestedChart(range, anchor),
  ]);
}

/* ── 인쇄 리사이즈 ──
 * Chart.js 캔버스는 화면 컨테이너 폭 기준 렌더. 인쇄로 전환되면 레이아웃 폭(portrait 2열)이 달라
 * 캔버스가 인쇄 영역에 안 맞아 꺾은선이 plot 경계를 넘는다. 인쇄 직전/직후 명시 리사이즈로 보정. */
function resizeAllCharts() {
  for (const c of Object.values(chartInstances)) { if (c) c.resize(); }
}
window.addEventListener('beforeprint', resizeAllCharts);
window.addEventListener('afterprint', resizeAllCharts);

/* ── 날짜 인풋 초기화 + 컨트롤 바인딩 ── */
/** @type {any} */ (ChartUtils).initAnchor('anchor-date');
bindToggle('global-range-btns', val => { globalRange = val; loadAllCharts(); });
// 앵커 변경 즉시 반영 — 구간 토글·상세 차트(cpu/network/storage)와 동일 (적용 버튼 없이 change 로 갱신).
/** @type {HTMLElement} */ (document.getElementById('anchor-date')).addEventListener('change', () => loadAllCharts());

loadAllCharts();
