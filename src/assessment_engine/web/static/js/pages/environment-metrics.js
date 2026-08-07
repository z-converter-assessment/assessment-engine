/**
 * 환경 성능 추이 페이지 차트 로직 — 전체 환경(모든 서버) 차트.
 *
 * 서버 상세 성능 추이(metrics.js) 기반의 환경판 — CPU·메모리·디스크·네트워크만 신호 카탈로그가 다르다(아래).
 * server_id 없음 — 환경 전체 집계 (capacity-weighted: cpu·mem·fs 이용률(%) / 판정 crossing 서버 수(count,
 * "윈도우 정규화 보정") — cpu 실행 큐·mem 페이징 압박·disk I/O 포화·net 이상 서버 수, 각 os별 임계로 판정한
 * 서버 수를 단일선 카운트로 집계(강도 지수 아님 — 도메인 지식 없이 바로 읽히는 표현으로 통일, "가장 나쁜 곳
 * 1개"보다 확산 범위가 드러남). CPU 분류·CPU/메모리/디스크 PSI 는 Windows 미발행/부분발행이라 환경 혼합
 * 단위에서 제외, 서버 상세엔 유지. 디스크 IOPS·처리량·네트워크 PPS(합산 절대값)도 제외 — 이기종 장치를 그냥
 * 더한 숫자는 비교 기준선이 없어 해석 불가(높다/낮다 판단 근거 없음, CPU%·await ms 와 달리 척도·임계가 없는
 * 숫자). 네트워크 I/O(rx/tx bytes/s)는 링크 속도 대비 이용률(%) 정규화를 검토했으나 link_speed_bps 결측이
 * fleet 상당수라 raw 활동량(합산, floating y축)으로 유지 — TCP 재전송율·패킷 드롭율은 net.congested_hosts
 * (판정 crossing 서버 수)로 통합, 두 % 라인이 시각적으로 거의 겹쳐 구분 안 되던 문제도 해결.
 * fetch: GET /api/servers/environment/metrics-chart (agg 미지원 — capacity-weighted/합산 단일).
 * 외부 의존: ChartUtils (base.html), Chart.js (페이지 로드). 수집 기준은 SSR(#last-metric-ts) 고정.
 */
import { AUTO_BUCKET, BUCKET_LABEL, BUCKET_MS, fmtKbChart, safeArray, bindToggle, renderChipLegend, buildAvgMaxDatasets, buildAvgMaxLegend } from "@/chart-utils";
import * as ChartUtils from "@/chart-utils";

/** @typedef {import('../generated/api').components['schemas']['MetricSeriesItem']} MetricSeriesItem */

// 선택 N대 한정(있으면) — 차트 fetch 에 ids 전달. 없으면 전체 환경 (data-selection-ids 미설정/빈 문자열).
const SELECTION_IDS = document.body.dataset.selectionIds || '';
// 판정 crossing 서버 수 차트(cpu.saturation_hosts·mem.paging_pressure_hosts) Y축 고정 상한 — 전체 서버 수
// (SSR, get_fleet_status 동일 산식). suggestedMax(floor)가 아니라 max(hard) — 자동 확장 없이 "이론상 최대치"
// 기준으로 항상 동일 스케일 유지, 구간·새로고침 바뀌어도 축이 안 흔들려 시각적 비교가 가능.
const TOTAL_HOSTS = Number(document.body.dataset.totalHosts) || 1;

let globalRange = '15m';
/** @type {Record<string, any>} */
const chartInstances = {};
// P4(a) sequence counter — per-chart 분리.
const seqs = {
  cpu: 0, cpuSaturation: 0, mem: 0, memPaging: 0,
  diskSat: 0, storagePct: 0, netIo: 0, netCongested: 0,
};

const _safe = safeArray;
const fmtLabel = (/** @type {string} */ iso, /** @type {string} */ range) => ChartUtils.fmtLabel(iso, range);
const makeBucketGrid = (/** @type {string} */ range, /** @type {Date | null} */ anchor) => ChartUtils.makeBucketGrid(range, /** @type {any} */ (AUTO_BUCKET[range]), anchor);
const getAnchorEnd = () => /** @type {any} */ (ChartUtils).getAnchorEnd('anchor-date');

// 인터페이스/device 정렬·RX/TX(또는 Read/Write) 인접 병합 — 환경 합산은 dimension 없으니 단일 라인.
// 환경 차트는 dimension=NULL 반환 -> RX/TX·Read/Write 만 라벨 부여 (서버별 iface 라인 폭증 회피).
/** @param {MetricSeriesItem[]} rows */
function computePeriodMax(rows) {
  const vals = rows.map(r => r.value).filter(v => v != null);
  return vals.length ? Math.max(...vals) : null;
}

/**
 * @param {string} elId
 * @param {number | null} val
 * @param {(v: number) => string} fmtFn
 * @param {((v: number) => string) | null} colorFn
 * @param {string} [label]
 */
function updateMaxLabel(elId, val, fmtFn, colorFn, label = '최대') {
  const el = document.getElementById(elId);
  if (!el) return;
  if (val == null) { el.textContent = `${label}: —`; el.style.color = '#94a3b8'; return; }
  el.textContent = `${label}: ` + fmtFn(val);
  el.style.color = colorFn ? colorFn(val) : '#64748b';
}

// 환경 endpoint — server_id·device_category 없음. agg 는 무시되나(capacity-weighted/합산 단일) 호출 형태 유지.
/**
 * @param {string} metricType
 * @param {string} range
 * @param {Date | null} anchor
 * @returns {Promise<MetricSeriesItem[]>}
 */
async function fetchChart(metricType, range, anchor) {
  const p = new URLSearchParams({
    metric_type: metricType,
    time_range:  range,
    bucket:      AUTO_BUCKET[range],
  });
  if (anchor) p.append('end', anchor.toISOString());
  if (SELECTION_IDS) p.append('ids', SELECTION_IDS);
  const res = await fetch(`/api/servers/environment/metrics-chart?${p}`);
  if (res.status === 404 || !res.ok) return [];  // P4(d): 데이터 부재 → 빈 배열
  return res.json();
}

/**
 * @param {any} yAxisOpts
 * @param {(v: number) => string} fmtFn
 */
function makePerfOptions(yAxisOpts, fmtFn) {
  return {
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
            const label = ctx.dataset.label ? ` ${ctx.dataset.label}:` : '';
            return `${label} ${avg != null ? fmtFn(avg) : '—'}`;
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

// 환경 단일선 — avg only (ghost max 미사용). buildAvgMaxDatasets 에 max=[] 전달.
/**
 * @param {any[]} avgRows
 * @param {number} bMs
 * @param {number[]} grid
 * @param {string | null} labelOverride
 */
function buildDatasets(avgRows, bMs, grid, labelOverride) {
  return buildAvgMaxDatasets(avgRows, [], bMs, grid, { label: labelOverride, pointRadius: 0 });
}

/**
 * @param {string} canvasId
 * @param {string} emptyId
 * @param {any[]} avgRows
 * @param {any} yAxisOpts
 * @param {(v: number) => string} fmtFn
 * @param {any[]} datasets
 * @param {any[]} labels
 */
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

/* -- Y축 설정 -- */
const pctTicks = { callback: (/** @type {number} */ v) => Number(v).toFixed(1) + '%', font:{size:11}, color:'#64748b' };
const Y_PCT   = { min:0, max:100, ticks: pctTicks };
// 고정 상한 없음 — Chart.js 자동 스케일(beginAtZero 만 강제해 축 최솟값은 항상 0, 최댓값은 실 데이터 범위에
// 맞춰 자동 확장). 이기종 fleet 트래픽 규모가 서버군마다 달라 고정 suggestedMax 는 저트래픽 환경에서 추이선이
// 바닥에 눌려 안 보이는 문제가 있었음.
const Y_NET   = { beginAtZero:true, ticks:{ callback: (/** @type {number} */ v) => fmtKbChart(v), font:{size:11}, color:'#64748b' } };

/* -- 개별 차트 로더 -- */

/**
 * @param {string} range
 * @param {Date | null} anchor
 */
async function loadCpuChart(range, anchor) {
  const seq = ++seqs.cpu;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const avgRows = await fetchChart('cpu.usage_percent', range, anchor);
  if (seq !== seqs.cpu) return;
  const safeAvg = _safe(avgRows);
  const datasets = buildDatasets(safeAvg, bMs, grid, 'CPU');
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  setChart('cpu-canvas', 'cpu-empty', safeAvg, Y_PCT, v => v.toFixed(1)+'%', datasets, labels);
  updateMaxLabel('cpu-max', computePeriodMax(safeAvg), v => v.toFixed(1)+'%', null);
}

// 실행 큐 포화 서버 수 — right_sizing.cpu_saturation_index 와 동일 임계 판정을 SQL 로 이식(backend
// cpu.saturation_hosts, NULL dimension 단일선). Linux procs_running(임계1.0)·Windows Processor Queue
// Length(임계2.0) — 판정 crossing 서버 수(count). 메모리 압박 서버 수와 일관된 표현(강도 지수보다 "몇 대"가
// 도메인 지식 없이 바로 읽힘) — "윈도우 정규화 보정".
/**
 * @param {string} range
 * @param {Date | null} anchor
 */
async function loadCpuSaturationChart(range, anchor) {
  const seq = ++seqs.cpuSaturation;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const rows = await fetchChart('cpu.saturation_hosts', range, anchor);
  if (seq !== seqs.cpuSaturation) return;
  const safe = _safe(rows);
  const canvas = /** @type {HTMLElement} */ (document.getElementById('cpusat-canvas'));
  const empty  = /** @type {HTMLElement} */ (document.getElementById('cpusat-empty'));
  if (chartInstances['cpusat-canvas']) { chartInstances['cpusat-canvas'].destroy(); delete chartInstances['cpusat-canvas']; }
  if (!safe.length) {
    canvas.style.display = 'none'; empty.style.display = 'flex';
    updateMaxLabel('cpusat-max', null, v => v.toFixed(0)+'대', null);
    return;
  }
  canvas.style.display = ''; empty.style.display = 'none';
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  /** @type {Record<number, number | null>} */
  const m = {};
  for (const r of safe) m[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;
  const data = grid.map(t => m[t] ?? null);
  chartInstances['cpusat-canvas'] = new Chart(canvas, /** @type {any} */ ({
    type: 'line',
    data: { labels, datasets: [{
      label: '실행 큐 포화 서버 수', data,
      borderColor: /** @type {any} */ (ChartUtils).themeColor(),
      backgroundColor: /** @type {any} */ (ChartUtils).themeColor(), yAxisID: 'yA',
      borderWidth: 2, pointRadius: 0, pointHoverRadius: 3, tension: 0.3, fill: false, spanGaps: false, stepped: 'before',
    }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode:'index', intersect:false },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (/** @type {any} */ ctx) => ` 포화: ${ctx.parsed.y != null ? ctx.parsed.y.toFixed(0)+'대' : '—'}` } },
      },
      scales: {
        x:  { ticks:{ maxTicksLimit:10, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
        yA: { type:'linear', position:'left', beginAtZero:true, min:0, max:TOTAL_HOSTS,
              ticks:{ precision:0, callback: (/** @type {number} */ v) => Number(v).toFixed(0) + '대', font:{size:11}, color:'#64748b' }, grid:{ color:'#f1f5f9' } },
      },
    },
  }));
  updateMaxLabel('cpusat-max', computePeriodMax(safe), v => v.toFixed(0)+'대', null);
}

// 디스크 I/O 포화 서버 수 — disk.io_saturation(worst-device MAX 단일선)과 동일 임계(DISKIO_AWAIT_MS)를
// 서버별로 적용한 판정 crossing 서버 수(count, NULL dimension). CPU 실행 큐·메모리 페이징과 동형 — "가장
// 나쁜 곳 1개"보다 "몇 대가 영향받았는지"가 더 유용. 물리 disk only + 카운터 신뢰 조건은 backend 단일 진실.
/**
 * @param {string} range
 * @param {Date | null} anchor
 */
async function loadDiskSaturationChart(range, anchor) {
  const seq = ++seqs.diskSat;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const rows = await fetchChart('disk.saturation_hosts', range, anchor);
  if (seq !== seqs.diskSat) return;
  const safe = _safe(rows);
  const canvas = /** @type {HTMLElement} */ (document.getElementById('disksat-canvas'));
  const empty  = /** @type {HTMLElement} */ (document.getElementById('disksat-empty'));
  if (chartInstances['disksat-canvas']) { chartInstances['disksat-canvas'].destroy(); delete chartInstances['disksat-canvas']; }
  if (!safe.length) {
    canvas.style.display = 'none'; empty.style.display = 'flex';
    updateMaxLabel('disksat-max', null, v => v.toFixed(0)+'대', null);
    return;
  }
  canvas.style.display = ''; empty.style.display = 'none';
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  /** @type {Record<number, number | null>} */
  const m = {};
  for (const r of safe) m[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;
  const data = grid.map(t => m[t] ?? null);
  chartInstances['disksat-canvas'] = new Chart(canvas, /** @type {any} */ ({
    type: 'line',
    data: { labels, datasets: [{
      label: '디스크 I/O 포화 서버 수', data,
      borderColor: /** @type {any} */ (ChartUtils).themeColor(),
      backgroundColor: /** @type {any} */ (ChartUtils).themeColor(), yAxisID: 'yA',
      borderWidth: 2, pointRadius: 0, pointHoverRadius: 3, tension: 0.3, fill: false, spanGaps: false, stepped: 'before',
    }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode:'index', intersect:false },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (/** @type {any} */ ctx) => ` 포화: ${ctx.parsed.y != null ? ctx.parsed.y.toFixed(0)+'대' : '—'}` } },
      },
      scales: {
        x:  { ticks:{ maxTicksLimit:10, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
        yA: { type:'linear', position:'left', beginAtZero:true, min:0, max:TOTAL_HOSTS,
              ticks:{ precision:0, callback: (/** @type {number} */ v) => Number(v).toFixed(0) + '대', font:{size:11}, color:'#64748b' }, grid:{ color:'#f1f5f9' } },
      },
    },
  }));
  updateMaxLabel('disksat-max', computePeriodMax(safe), v => v.toFixed(0)+'대', null);
}

/**
 * @param {string} range
 * @param {Date | null} anchor
 */
async function loadMemChart(range, anchor) {
  const seq = ++seqs.mem;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const avgRows = await fetchChart('mem.usage_percent', range, anchor);
  if (seq !== seqs.mem) return;
  const safeAvg = _safe(avgRows);
  const datasets = buildDatasets(safeAvg, bMs, grid, '메모리');
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  setChart('mem-canvas', 'mem-empty', safeAvg, Y_PCT, v => v.toFixed(1)+'%', datasets, labels);
  updateMaxLabel('mem-max', computePeriodMax(safeAvg), v => v.toFixed(1)+'%', null);
}

// 메모리 압박 서버 수 — right_sizing.mem_pressure_active(mem_saturated dual-gate 의 실제 페이징 판정
// 신호원) 동일 원자료·임계(backend mem.paging_pressure_hosts, NULL dimension 단일선). Linux(refault, 임계
// "> 0")·Windows(Pages Input/sec, 임계 20/s) 판정 crossing 서버 수 — 정규화 지수 아닌 count(분모 왜곡 없음).
// mem.psi(판정 비관여 참고치, Windows 미발행) 대체 — 실제 분류에 쓰는 신호라 정합성 높음, "윈도우 정규화 보정".
/**
 * @param {string} range
 * @param {Date | null} anchor
 */
async function loadMemPagingChart(range, anchor) {
  const seq = ++seqs.memPaging;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const rows = await fetchChart('mem.paging_pressure_hosts', range, anchor);
  if (seq !== seqs.memPaging) return;
  const safe = _safe(rows);
  const canvas = /** @type {HTMLElement} */ (document.getElementById('mempaging-canvas'));
  const empty  = /** @type {HTMLElement} */ (document.getElementById('mempaging-empty'));
  if (chartInstances['mempaging-canvas']) { chartInstances['mempaging-canvas'].destroy(); delete chartInstances['mempaging-canvas']; }
  if (!safe.length) {
    canvas.style.display = 'none'; empty.style.display = 'flex';
    updateMaxLabel('mempaging-max', null, v => v.toFixed(0)+'대', null);
    return;
  }
  canvas.style.display = ''; empty.style.display = 'none';
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  /** @type {Record<number, number | null>} */
  const m = {};
  for (const r of safe) m[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;
  const data = grid.map(t => m[t] ?? null);
  chartInstances['mempaging-canvas'] = new Chart(canvas, /** @type {any} */ ({
    type: 'line',
    data: { labels, datasets: [{
      label: '페이징 압박 서버 수', data,
      borderColor: /** @type {any} */ (ChartUtils).themeColor(),
      backgroundColor: /** @type {any} */ (ChartUtils).themeColor(), yAxisID: 'yA',
      borderWidth: 2, pointRadius: 0, pointHoverRadius: 3, tension: 0.3, fill: false, spanGaps: false, stepped: 'before',
    }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode:'index', intersect:false },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (/** @type {any} */ ctx) => ` 압박: ${ctx.parsed.y != null ? ctx.parsed.y.toFixed(0)+'대' : '—'}` } },
      },
      scales: {
        x:  { ticks:{ maxTicksLimit:10, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
        yA: { type:'linear', position:'left', beginAtZero:true, min:0, max:TOTAL_HOSTS,
              ticks:{ precision:0, callback: (/** @type {number} */ v) => Number(v).toFixed(0) + '대', font:{size:11}, color:'#64748b' }, grid:{ color:'#f1f5f9' } },
      },
    },
  }));
  updateMaxLabel('mempaging-max', computePeriodMax(safe), v => v.toFixed(0)+'대', null);
}

// 스토리지 사용률 — 전 서버 용량 가중 평균(fs.usage_percent, CPU/메모리 사용률과 동일 0~100 척도).
/**
 * @param {string} range
 * @param {Date | null} anchor
 */
async function loadStoragePctChart(range, anchor) {
  const seq = ++seqs.storagePct;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const avgRows = await fetchChart('fs.usage_percent', range, anchor);
  if (seq !== seqs.storagePct) return;
  const safeAvg = _safe(avgRows);
  const datasets = buildDatasets(safeAvg, bMs, grid, '스토리지 사용률');
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  setChart('storagepct-canvas', 'storagepct-empty', safeAvg, Y_PCT, v => v.toFixed(1)+'%', datasets, labels);
  updateMaxLabel('storagepct-max', computePeriodMax(safeAvg), v => v.toFixed(1)+'%', null);
}

// 네트워크 I/O — 환경 합산(RX/TX bytes).
/**
 * @param {string} range
 * @param {Date | null} anchor
 */
async function loadNetIoChart(range, anchor) {
  const seq = ++seqs.netIo;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const [rx, tx] = await Promise.all([
    fetchChart('net.rx_bytes_per_sec', range, anchor),
    fetchChart('net.tx_bytes_per_sec', range, anchor),
  ]);
  if (seq !== seqs.netIo) return;
  const safeRx = _safe(rx);
  const safeTx = _safe(tx);
  const rows = [
    ...safeRx.map(r => ({ ...r, dimension: 'RX' })),
    ...safeTx.map(r => ({ ...r, dimension: 'TX' })),
  ];
  const datasets = buildDatasets(rows, bMs, grid, null);
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const chart = setChart('netio-canvas', 'netio-empty', rows, Y_NET, fmtKbChart, datasets, labels);
  buildAvgMaxLegend('netio-legend', chart, { withToggle: true });
  updateMaxLabel('netio-rx-max', computePeriodMax(safeRx), fmtKbChart, null, 'RX 최대');
  updateMaxLabel('netio-tx-max', computePeriodMax(safeTx), fmtKbChart, null, 'TX 최대');
}

// 네트워크 이상 서버 수 — right_sizing.assess_network 의 실제 판정(network_congested: 재전송율>1%·
// 드롭율>0.5%(저트래픽 게이트)·conntrack 고갈>=0.8 OR)과 동일 원자료·임계를 SQL 로 이식(backend
// net.congested_hosts, NULL dimension 단일선). 재전송율·드롭율 두 % 라인이 시각적으로 거의 겹쳐 구분이 안
// 되던 문제를 판정 crossing 서버 수(count)로 통합 — cpu/mem/disk 포화 서버 수와 동형.
/**
 * @param {string} range
 * @param {Date | null} anchor
 */
async function loadNetCongestionChart(range, anchor) {
  const seq = ++seqs.netCongested;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const rows = await fetchChart('net.congested_hosts', range, anchor);
  if (seq !== seqs.netCongested) return;
  const safe = _safe(rows);
  const canvas = /** @type {HTMLElement} */ (document.getElementById('netcong-canvas'));
  const empty  = /** @type {HTMLElement} */ (document.getElementById('netcong-empty'));
  if (chartInstances['netcong-canvas']) { chartInstances['netcong-canvas'].destroy(); delete chartInstances['netcong-canvas']; }
  if (!safe.length) {
    canvas.style.display = 'none'; empty.style.display = 'flex';
    updateMaxLabel('netcong-max', null, v => v.toFixed(0)+'대', null);
    return;
  }
  canvas.style.display = ''; empty.style.display = 'none';
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  /** @type {Record<number, number | null>} */
  const m = {};
  for (const r of safe) m[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;
  const data = grid.map(t => m[t] ?? null);
  chartInstances['netcong-canvas'] = new Chart(canvas, /** @type {any} */ ({
    type: 'line',
    data: { labels, datasets: [{
      label: '네트워크 이상 서버 수', data,
      borderColor: /** @type {any} */ (ChartUtils).themeColor(),
      backgroundColor: /** @type {any} */ (ChartUtils).themeColor(), yAxisID: 'yA',
      borderWidth: 2, pointRadius: 0, pointHoverRadius: 3, tension: 0.3, fill: false, spanGaps: false, stepped: 'before',
    }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode:'index', intersect:false },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (/** @type {any} */ ctx) => ` 이상: ${ctx.parsed.y != null ? ctx.parsed.y.toFixed(0)+'대' : '—'}` } },
      },
      scales: {
        x:  { ticks:{ maxTicksLimit:10, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
        yA: { type:'linear', position:'left', beginAtZero:true, min:0, max:TOTAL_HOSTS,
              ticks:{ precision:0, callback: (/** @type {number} */ v) => Number(v).toFixed(0) + '대', font:{size:11}, color:'#64748b' }, grid:{ color:'#f1f5f9' } },
      },
    },
  }));
  updateMaxLabel('netcong-max', computePeriodMax(safe), v => v.toFixed(0)+'대', null);
}

/* -- 전체 로드 -- */
/** @param {string} range */
function updateBucketLabel(range) {
  /** @type {HTMLElement} */ (document.getElementById('bucket-label')).textContent = BUCKET_LABEL[AUTO_BUCKET[range]] || '';
}

// 구간·앵커 select/input 은 인쇄에서 no-print(조작 컨트롤이라 무의미) — 인쇄 텍스트 대체 표시(range-print·
// anchor-print). 앵커는 getAnchorEnd 가 null(=현재 시각 이후 선택, 라이브)이면 "실시간", 과거 지정이면
// KST 값(입력 자체가 이미 KST, #F2 표시 경계 함수인 initAnchor 산출값이라 재변환 불필요) 그대로 표시.
/**
 * @param {string} range
 * @param {Date | null} anchor
 */
function updatePrintControlsLabel(range, anchor) {
  const select = /** @type {HTMLSelectElement} */ (document.getElementById('global-range-btns'));
  const rangeText = select.options[select.selectedIndex]?.text || range;
  /** @type {HTMLElement} */ (document.getElementById('range-print')).textContent = `구간: ${rangeText}`;
  const anchorInput = /** @type {HTMLInputElement} */ (document.getElementById('anchor-date'));
  const anchorText = anchor ? anchorInput.value.replace('T', ' ') : '실시간';
  /** @type {HTMLElement} */ (document.getElementById('anchor-print')).textContent = `기준: ${anchorText}`;
}

async function loadAllCharts() {
  const range  = globalRange;
  const anchor = getAnchorEnd();
  updateBucketLabel(range);
  updatePrintControlsLabel(range, anchor);
  await Promise.all([
    loadCpuChart(range, anchor),     loadCpuSaturationChart(range, anchor),
    loadMemChart(range, anchor),
    loadMemPagingChart(range, anchor),
    loadDiskSaturationChart(range, anchor),
    loadStoragePctChart(range, anchor),
    loadNetIoChart(range, anchor),
    loadNetCongestionChart(range, anchor),
  ]);
}

/* -- 인쇄 리사이즈 -- */
function resizeAllCharts() {
  for (const c of Object.values(chartInstances)) { if (c) c.resize(); }
}
window.addEventListener('beforeprint', resizeAllCharts);
window.addEventListener('afterprint', resizeAllCharts);

/* -- 날짜 인풋 초기화 + 컨트롤 바인딩 -- */
/** @type {any} */ (ChartUtils).initAnchor('anchor-date');
bindToggle('global-range-btns', val => { globalRange = val; loadAllCharts(); });
// 앵커 변경 즉시 반영 — 구간 토글·상세 차트(cpu/network/storage)와 동일 (적용 버튼 없이 change 로 갱신).
/** @type {HTMLElement} */ (document.getElementById('anchor-date')).addEventListener('change', () => loadAllCharts());

loadAllCharts();
