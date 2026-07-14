// @ts-check
/**
 * 성능 추이 페이지 차트 로직.
 *
 * 각 상세 페이지(cpu/memory/storage/network)의 추이 차트를 자원별 카드에 모은 종합 뷰 — 4자원(CPU/메모리/
 * 스토리지/네트워크) x 최대 5개, 자원별 상세 탭과 동일 차트 목록·OS 분기(#F9 자원 탭 차트 추가 시 본 페이지도
 * 동시 갱신 의무 — 개별 탭에만 추가하고 여기 누락하면 "모아보기"라는 페이지 존재 의미가 깨짐).
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
const OS_FAMILY = document.body.dataset.osFamily || '';  // Windows 미측정 메트릭 N/A 분기(PSI 등)

const PERF_IOPS_SUGGESTED_MAX = 200;              // HDD 랜덤 I/O 한계(~100–200 IOPS) 기준
const PERF_NET_SUGGESTED_MAX  = 10 * 1024 * 1024; // 10 MB/s — 1 Gbps 이더넷의 약 8%
const PERF_PPS_SUGGESTED_MAX  = 10;               // pps soft ceiling (idle 환경도 보이도록)
const PERF_DISK_KBPS_SUGGESTED_MAX = 10 * 1024;   // 10 MB/s — net 처리량 차트와 동일 절대 기준선
const PERF_DISK_AWAIT_SUGGESTED_MAX = 20;         // 디스크 await ms — 20ms 이상 I/O 포화, 양 OS 실측
// 처리량 동적 단위(kBps/MBps)는 ChartUtils.fmtThroughput 단일 진실 (storage/detail/환경 추이 공용).

// 색상 임계값 — backend mappers._USAGE_*_PCT 단일 진실, body data-attribute 로 주입 (#E1 P4).
// 파일시스템 사용률 게이지만 임계 색 사용. 그 외 추이 차트는 단색(검정) 통일.
const USAGE_DANGER_PCT = parseFloat(/** @type {string} */ (document.body.dataset.usageDangerPct)) || 90;
const USAGE_WARN_PCT   = parseFloat(/** @type {string} */ (document.body.dataset.usageWarnPct))   || 75;
const COLOR_NEUTRAL = '#64748b';
const COLOR_WARN    = '#f59e0b';
const COLOR_DANGER  = '#ef4444';
/** @param {number} v @returns {string} */
const colorByMountPct = v => v >= USAGE_DANGER_PCT ? COLOR_DANGER : v >= USAGE_WARN_PCT ? COLOR_WARN : COLOR_NEUTRAL;

let globalRange = '15m';
/** @type {Record<string, any>} */
const chartInstances = {};
// P4(a) sequence counter — per-chart 분리 (다른 page 와 일관). chart 별 독립 counter.
const seqs = {
  cpu: 0, cpuClass: 0, load: 0, cpuPsi: 0,
  mem: 0, memComp: 0, memPsi: 0,
  physIo: 0, diskKbps: 0, diskQueue: 0, diskPsi: 0, fs: 0,
  netIo: 0, netPps: 0, retrans: 0, drop: 0,
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

// network.js 의 인터페이스 정렬·RX/TX 인접 병합 — 통합 네트워크 차트(I/O·PPS) 공용.
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

/** @param {string} elId @param {number|null} val @param {(v: number) => string} fmtFn @param {((v: number) => string)|null} colorFn */
function updateMaxLabel(elId, val, fmtFn, colorFn) {
  const el = document.getElementById(elId);
  if (!el) return;
  if (val == null) { el.textContent = '최대: —'; el.style.color = '#94a3b8'; return; }
  el.textContent = '최대: ' + fmtFn(val);
  el.style.color = colorFn ? colorFn(val) : '#64748b';
}

/**
 * @param {string} metricType @param {string} agg @param {string} range @param {Date|null} anchor
 * @param {boolean} [collapse] dimension(device/iface) 합산 1선 — 스토리지 IOPS·처리량 등 물리 디바이스
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

// 다중 dimension 라인 옵션 (avg-only — CPU 분류·메모리 구성, % Y축). cpu.js/memory.js renderCompChart 동일.
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

// 다중 dimension 라인(avg-only) 렌더 — CPU 분류·메모리 구성. renderChipLegend.
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

// 다중 dimension 라인(avg-only, 커스텀 Y축/포맷) — 실행 큐·D-state / 디스크 IOPS·처리량(Read/Write 2선,
// collapse=true) 공용. renderMultiDimChart 와 달리 % 고정이 아니라 yAxisOpts/fmtFn 을 받는다.
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

// 단일선 차트(avg-only) — PSI(cpu/mem/disk)·TCP 재전송율·패킷 드롭율·디스크 await 공용. winEmptyText 있으면
// Windows 는 빈 결과를 "N/A(구조적 미지원)"으로(no_data 와 구분 — cpu.js/memory.js/storage.js 동일 원칙),
// 없으면 일반 "해당 기간에 수집된 데이터가 없습니다."
/** @param {string} metricType @param {string} canvasId @param {string} emptyId @param {'cpuPsi'|'memPsi'|'diskPsi'|'diskQueue'|'retrans'|'drop'} seqKey
 *  @param {string} label @param {any} yAxisOpts @param {(v: number) => string} fmtFn @param {string|null} winEmptyText
 *  @param {string} range @param {Date|null} anchor */
async function loadSingleLineChart(metricType, canvasId, emptyId, seqKey, label, yAxisOpts, fmtFn, winEmptyText, range, anchor) {
  const seq = ++seqs[seqKey];
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const rows = _safe(await fetchChart(metricType, 'avg', range, anchor));
  if (seq !== seqs[seqKey]) return;
  const canvas = /** @type {HTMLElement} */ (document.getElementById(canvasId));
  const empty  = /** @type {HTMLElement} */ (document.getElementById(emptyId));
  if (chartInstances[canvasId]) { chartInstances[canvasId].destroy(); delete chartInstances[canvasId]; }
  if (!rows.length) {
    if (winEmptyText) empty.textContent = OS_FAMILY === 'windows' ? winEmptyText : '해당 기간에 수집된 데이터가 없습니다.';
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
      borderWidth: 2, pointRadius: 0, pointHoverRadius: 3, tension: 0.3, fill: false, spanGaps: false,
    }] },
    options: makePerfOptions(yAxisOpts, fmtFn),
  }));
}

/* ── Y축 설정 ── */
const pctTicks = { callback: /** @param {number} v */ (v) => Number(v).toFixed(1) + '%', font:{size:11}, color:'#64748b' };
const Y_PCT   = { min:0, max:100, ticks: pctTicks };
const Y_RUNQ  = { beginAtZero:true, suggestedMax: 2, ticks:{ font:{size:11}, color:'#64748b' } };
const Y_DISK_AWAIT = { beginAtZero:true, suggestedMax: PERF_DISK_AWAIT_SUGGESTED_MAX, ticks:{ callback: /** @param {number} v */ (v) => v.toFixed(0) + ' ms', font:{size:11}, color:'#64748b' } };
const Y_IOPS  = { beginAtZero:true, suggestedMax: PERF_IOPS_SUGGESTED_MAX, ticks:{ precision:0, font:{size:11}, color:'#64748b' } };
const Y_DISK_KBPS = { beginAtZero:true, suggestedMax: PERF_DISK_KBPS_SUGGESTED_MAX, ticks:{ callback: /** @param {number} v */ (v) => fmtThroughput(v), font:{size:11}, color:'#64748b' } };
const Y_NET   = { beginAtZero:true, suggestedMax: PERF_NET_SUGGESTED_MAX, ticks:{ callback: /** @param {number} v */ (v) => fmtKbChart(v), font:{size:11}, color:'#64748b' } };
const Y_PPS   = { beginAtZero:true, suggestedMax: PERF_PPS_SUGGESTED_MAX, ticks:{ callback: /** @param {number} v */ (v) => v.toFixed(0) + ' pps', font:{size:11}, color:'#64748b' } };
// PSI(cpu/mem/disk 공용, some 정체율 %) · TCP 재전송율 · 패킷 드롭율 — 각 상세 페이지(cpu.js/memory.js/
// storage.js/network.js)와 동일 suggestedMax.
const Y_PSI     = { beginAtZero:true, suggestedMax: 20, ticks:{ callback: /** @param {number} v */ (v) => v.toFixed(1) + '%', font:{size:11}, color:'#64748b' } };
const Y_RETRANS = { beginAtZero:true, suggestedMax: 1,  ticks:{ callback: /** @param {number} v */ (v) => Number(v).toFixed(2) + '%', font:{size:11}, color:'#64748b' } };
const Y_DROP    = { beginAtZero:true, suggestedMax: 0.5, ticks:{ callback: /** @param {number} v */ (v) => Number(v).toFixed(2) + '%', font:{size:11}, color:'#64748b' } };

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

// Windows 는 cpu_stat 이 user/system/idle 만(iowait·nice 개념 부재) — 두 축 제외(cpu.js 와 동일 OS 분기).
const CPUCLASS_META_BASE = {
  user:   { label: 'User',     color: /** @type {any} */ (ChartUtils).themeColor() },
  system: { label: 'System',   color: '#f59e0b' },
};
/** @param {string} range @param {Date|null} anchor */
async function loadCpuClassChart(range, anchor) {
  const seq = ++seqs.cpuClass;
  const isWin = OS_FAMILY === 'windows';
  const reqs = [
    fetchChart('cpu.user_percent','avg', range, anchor),
    fetchChart('cpu.system_percent','avg', range, anchor),
  ];
  if (!isWin) {
    reqs.push(fetchChart('cpu.iowait_percent','avg', range, anchor));
    reqs.push(fetchChart('cpu.nice_percent','avg', range, anchor));
  }
  const [u, s, io, nice] = await Promise.all(reqs);
  if (seq !== seqs.cpuClass) return;
  const rows = [
    ..._safe(u).map(r => ({ ...r, dimension: 'user' })),
    ..._safe(s).map(r => ({ ...r, dimension: 'system' })),
    ...(!isWin ? _safe(io).map(r => ({ ...r, dimension: 'iowait' })) : []),
    ...(!isWin ? _safe(nice).map(r => ({ ...r, dimension: 'nice' })) : []),
  ];
  const meta = {
    ...CPUCLASS_META_BASE,
    ...(!isWin ? { iowait: { label: 'I/O Wait', color: '#ef4444' }, nice: { label: 'Nice', color: '#8b5cf6' } } : {}),
  };
  renderMultiDimChart('cpuclass-canvas', 'cpuclass-empty', 'cpuclass-legend', rows, range, anchor, meta);
}

// 실행 큐·D-state 추이 (cpu.run_queue os-aware 코어당 + cpu.blocked Linux 전용 근본원인) — cpu.js 의
// renderLoadChart/loadLoadChart 와 동일 2선 모델(D-state 는 Windows 미발행 자연 제외).
const RUNQ_META = { runq: { label: '실행 큐 (코어당)', color: /** @type {any} */ (ChartUtils).themeColor() } };
/** @param {string} range @param {Date|null} anchor */
async function loadLoadChart(range, anchor) {
  const seq = ++seqs.load;
  const isWin = OS_FAMILY === 'windows';
  const reqs = [fetchChart('cpu.run_queue','avg', range, anchor), fetchChart('cpu.run_queue','max', range, anchor)];
  if (!isWin) reqs.push(fetchChart('cpu.blocked','avg', range, anchor));
  const [runqAvg, runqMax, blockedAvg] = await Promise.all(reqs);
  if (seq !== seqs.load) return;
  const rows = [
    ..._safe(runqAvg).map(r => ({ ...r, dimension: 'runq' })),
    ...(!isWin ? _safe(blockedAvg).map(r => ({ ...r, dimension: 'blocked' })) : []),
  ];
  const meta = { ...RUNQ_META, ...(!isWin ? { blocked: { label: 'D-state 블록', color: '#8b5cf6' } } : {}) };
  renderDimLineChart('load-canvas', 'load-empty', 'load-legend', rows, range, anchor, meta, Y_RUNQ, v => v.toFixed(2));
  updateMaxLabel('load-max', computePeriodMax(_safe(runqMax)), v => v.toFixed(2), null);
}

// CPU PSI 추이 (some 정체율 %) — Linux 4.20+ 전용, Windows·구커널은 N/A.
/** @param {string} range @param {Date|null} anchor */
async function loadCpuPsiChart(range, anchor) {
  await loadSingleLineChart('cpu.psi', 'cpupsi-canvas', 'cpupsi-empty', 'cpuPsi',
    'PSI (정체율)', Y_PSI, v => v.toFixed(1)+'%', 'N/A (Windows 미지원)', range, anchor);
}

// 디스크 I/O 포화 추이 (disk.io_saturation = await ms, 요청당 평균 대기) — 양 OS 실측, storage.js
// loadDiskSatChart 와 동일 단일선(avg+max ghost 아님 — 개별 탭과 시각 모델 통일).
/** @param {string} range @param {Date|null} anchor */
async function loadDiskQueueChart(range, anchor) {
  await loadSingleLineChart('disk.io_saturation', 'diskqueue-canvas', 'diskqueue-empty', 'diskQueue',
    '디스크 await', Y_DISK_AWAIT, v => v.toFixed(1)+' ms', null, range, anchor);
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

// 메모리 PSI 추이 (some 정체율 %) — Linux 4.20+ 전용, Windows·구커널은 N/A.
/** @param {string} range @param {Date|null} anchor */
async function loadMemPsiChart(range, anchor) {
  await loadSingleLineChart('mem.psi', 'mempsi-canvas', 'mempsi-empty', 'memPsi',
    'PSI (정체율)', Y_PSI, v => v.toFixed(1)+'%', 'N/A (Windows 미지원)', range, anchor);
}

// 디스크 I/O — Read/Write 합산 2선(collapse=true, 물리 디바이스 개수 무관 항상 2선 — storage.js 와 동일
// 정책, 개별 디바이스 활동은 자원 상세 탭 자체의 실시간 카드에서만 확인).
const IO_DIM_META = {
  read:  { label: 'Read',  color: /** @type {any} */ (ChartUtils).themeColor() },
  write: { label: 'Write', color: '#f59e0b' },
};
/** @param {string} range @param {Date|null} anchor */
async function loadPhysIoChart(range, anchor) {
  const seq = ++seqs.physIo;
  const [readRows, writeRows] = await Promise.all([
    fetchChart('disk.read_iops','avg', range, anchor, true),
    fetchChart('disk.write_iops','avg', range, anchor, true),
  ]);
  if (seq !== seqs.physIo) return;
  const rows = [
    ..._safe(readRows).map(r => ({ ...r, dimension: 'read' })),
    ..._safe(writeRows).map(r => ({ ...r, dimension: 'write' })),
  ];
  renderDimLineChart('physio-canvas', 'physio-empty', 'physio-legend', rows, range, anchor, IO_DIM_META, Y_IOPS, v => Math.round(v)+' IOPS');
}

// 디스크 처리량(kBps) — 물리 I/O(IOPS) 와 동일 collapse=true 2선 모델, 처리량 축(동적 kBps/MBps).
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

// 디스크 I/O PSI 추이 (some io 정체율 %) — Linux 전용, Windows 는 N/A.
/** @param {string} range @param {Date|null} anchor */
async function loadDiskPsiChart(range, anchor) {
  await loadSingleLineChart('disk.psi', 'diskpsi-canvas', 'diskpsi-empty', 'diskPsi',
    'PSI (정체율)', Y_PSI, v => v.toFixed(1)+'%', 'N/A (Windows 미지원)', range, anchor);
}

/** @param {string} range @param {Date|null} anchor */
async function loadFsChart(range, anchor) {
  const seq = ++seqs.fs;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const [avgRows, maxRows] = await Promise.all([fetchChart('fs.usage_percent','avg', range, anchor), fetchChart('fs.usage_percent','max', range, anchor)]);
  if (seq !== seqs.fs) return;
  const safeAvg = _safe(avgRows), safeMax = _safe(maxRows);
  const datasets = buildDatasets(safeAvg, safeMax, bMs, grid, null);
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const chart = setChart('fs-canvas', 'fs-empty', safeAvg, Y_PCT, v => v.toFixed(1)+'%', datasets, labels);
  buildAvgMaxLegend('fs-legend', chart, { withToggle: true });  // 다른 차트(물리 I/O·네트워크)와 동일 칩 토글
  updateMaxLabel('fs-max', computePeriodMax(safeMax), v => v.toFixed(1)+'%', colorByMountPct);
}

// 네트워크 I/O — iface x RX/TX 통합 bytes (network.js 모델).
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
}

// 네트워크 PPS — iface x RX/TX packets.
/** @param {string} range @param {Date|null} anchor */
async function loadNetPpsChart(range, anchor) {
  const seq = ++seqs.netPps;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const [rxA, rxM, txA, txM] = await Promise.all([
    fetchChart('net.rx_packets_per_sec','avg', range, anchor), fetchChart('net.rx_packets_per_sec','max', range, anchor),
    fetchChart('net.tx_packets_per_sec','avg', range, anchor), fetchChart('net.tx_packets_per_sec','max', range, anchor),
  ]);
  if (seq !== seqs.netPps) return;
  const { avg, max } = ifaceOrderedRows(rxA, txA, rxM, txM);
  const datasets = buildDatasets(avg, max, bMs, grid, null);
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const chart = setChart('netpps-canvas', 'netpps-empty', avg, Y_PPS, v => v.toFixed(1)+' pps', datasets, labels);
  buildAvgMaxLegend('netpps-legend', chart, { withToggle: true });
}

// TCP 재전송율 (%, 단일선 — 양 OS, 1% 초과면 네트워크 품질 저하).
/** @param {string} range @param {Date|null} anchor */
async function loadRetransChart(range, anchor) {
  await loadSingleLineChart('net.retrans_percent', 'retrans-canvas', 'retrans-empty', 'retrans',
    'TCP 재전송율', Y_RETRANS, v => v.toFixed(2)+'%', null, range, anchor);
}

// 패킷 드롭율 (%, 단일선 — 양 OS, 링 버퍼 오버런·경로 손실 신호).
/** @param {string} range @param {Date|null} anchor */
async function loadDropChart(range, anchor) {
  await loadSingleLineChart('net.drop_percent', 'drop-canvas', 'drop-empty', 'drop',
    '패킷 드롭율', Y_DROP, v => v.toFixed(2)+'%', null, range, anchor);
}

/* ── 전체 로드 ── */
/** @param {string} range */
function updateBucketLabel(range) {
  /** @type {HTMLElement} */ (document.getElementById('bucket-label')).textContent = BUCKET_LABEL[AUTO_BUCKET[range]] || '';
}

async function loadAllCharts() {
  // P4(b) capture-before-await: 호출 시점의 range/anchor를 캡처해 모든 로더에 전달.
  const range  = globalRange;
  const anchor = getAnchorEnd();
  updateBucketLabel(range);
  await Promise.all([
    loadCpuChart(range, anchor),     loadCpuClassChart(range, anchor),
    loadLoadChart(range, anchor),    loadCpuPsiChart(range, anchor),
    loadMemChart(range, anchor),     loadMemCompChart(range, anchor),
    loadMemPsiChart(range, anchor),
    loadPhysIoChart(range, anchor),  loadDiskKbpsChart(range, anchor),
    loadDiskQueueChart(range, anchor), loadDiskPsiChart(range, anchor),
    loadFsChart(range, anchor),
    loadNetIoChart(range, anchor),   loadNetPpsChart(range, anchor),
    loadRetransChart(range, anchor), loadDropChart(range, anchor),
  ]);
}

/* ── 인쇄 리사이즈 ──
 * Chart.js 캔버스는 화면 컨테이너 폭 기준 렌더. 인쇄로 전환되면 레이아웃 폭(2열 인쇄)이 달라
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
