/**
 * 환경 성능 추이 페이지 차트 로직 — 전체 환경(모든 서버) 차트.
 *
 * 서버 상세 성능 추이(metrics.js) 기반의 환경판. server_id 없음 — 환경 전체 집계
 * (capacity-weighted: cpu·mem·fs·swap / 합산: disk·net rate / 평균: load).
 * fetch: GET /api/servers/environment/metrics-chart (agg 미지원 — capacity-weighted/합산 단일).
 * 외부 의존: ChartUtils (base.html), Chart.js (페이지 로드). 수집 기준은 SSR(#last-metric-ts) 고정.
 */
const { AUTO_BUCKET, BUCKET_LABEL, BUCKET_MS,
        fmtKbChart, fmtThroughput, safeArray, bindToggle, renderChipLegend,
        buildAvgMaxDatasets, buildAvgMaxLegend, buildDimDatasets } = ChartUtils;

const PERF_IOPS_SUGGESTED_MAX = 200;              // HDD 랜덤 I/O 한계 기준 (환경 합산이라 자동 확장 가능)
const PERF_NET_SUGGESTED_MAX  = 10 * 1024 * 1024; // 10 MB/s
const PERF_DISK_KBPS_SUGGESTED_MAX = 10 * 1024;   // 10 MB/s — net 처리량과 동일 절대 기준선
// 처리량 동적 단위(kBps/MBps)는 ChartUtils.fmtThroughput 단일 진실 (storage/detail/개별 성능추이 공용).

const USAGE_DANGER_PCT = parseFloat(document.body.dataset.usageDangerPct) || 90;
const USAGE_WARN_PCT   = parseFloat(document.body.dataset.usageWarnPct)   || 75;
const COLOR_NEUTRAL = '#64748b';
const COLOR_WARN    = '#f59e0b';
const COLOR_DANGER  = '#ef4444';
const colorByMountPct = v => v >= USAGE_DANGER_PCT ? COLOR_DANGER : v >= USAGE_WARN_PCT ? COLOR_WARN : COLOR_NEUTRAL;

// 선택 N대 한정(있으면) — 차트 fetch 에 ids 전달. 없으면 전체 환경 (data-selection-ids 미설정/빈 문자열).
const SELECTION_IDS = document.body.dataset.selectionIds || '';

let globalRange = '15m';
const chartInstances = {};
// P4(a) sequence counter — per-chart 분리.
const seqs = {
  cpu: 0, cpuClass: 0, runQueue: 0, mem: 0, memComp: 0, swap: 0,
  physIo: 0, diskKbps: 0, diskSat: 0, fs: 0, netIo: 0, retrans: 0,
};

const _safe = safeArray;
const fmtLabel = (iso, range) => ChartUtils.fmtLabel(iso, range);
const makeBucketGrid = (range, anchor) => ChartUtils.makeBucketGrid(range, AUTO_BUCKET[range], anchor);
const getAnchorEnd = () => ChartUtils.getAnchorEnd('anchor-date');

// 인터페이스/device 정렬·RX/TX(또는 Read/Write) 인접 병합 — 환경 합산은 dimension 없으니 단일 라인.
// 환경 차트는 dimension=NULL 반환 -> RX/TX·Read/Write 만 라벨 부여 (서버별 iface 라인 폭증 회피).
function computePeriodMax(rows) {
  const vals = rows.map(r => r.value).filter(v => v != null);
  return vals.length ? Math.max(...vals) : null;
}

function updateMaxLabel(elId, val, fmtFn, colorFn) {
  const el = document.getElementById(elId);
  if (!el) return;
  if (val == null) { el.textContent = '최대: —'; el.style.color = '#94a3b8'; return; }
  el.textContent = '최대: ' + fmtFn(val);
  el.style.color = colorFn ? colorFn(val) : '#64748b';
}

// 환경 endpoint — server_id·device_category 없음. agg 는 무시되나(capacity-weighted/합산 단일) 호출 형태 유지.
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

function makePerfOptions(yAxisOpts, fmtFn) {
  return {
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

function makeMultiDimOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode:'index', intersect:false },
    plugins: {
      legend: { display: false },
      tooltip: { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? ctx.parsed.y.toFixed(1) : '—'}%` } },
    },
    scales: {
      x: { ticks:{ maxTicksLimit:10, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
      y: {
        ticks: { callback: v => v + '%', font:{size:11}, color:'#64748b' },
        grid:  { color:'#f1f5f9' }, beginAtZero: true,
      },
    },
  };
}

// 환경 단일선 — avg only (ghost max 미사용). buildAvgMaxDatasets 에 max=[] 전달.
function buildDatasets(avgRows, bMs, grid, labelOverride) {
  return buildAvgMaxDatasets(avgRows, [], bMs, grid, { label: labelOverride, pointRadius: 0 });
}

function setChart(canvasId, emptyId, avgRows, yAxisOpts, fmtFn, datasets, labels) {
  const canvas = document.getElementById(canvasId);
  const empty  = document.getElementById(emptyId);
  if (chartInstances[canvasId]) { chartInstances[canvasId].destroy(); delete chartInstances[canvasId]; }
  if (!avgRows.length) {
    canvas.style.display = 'none'; empty.style.display = 'flex';
    return null;
  }
  canvas.style.display = ''; empty.style.display = 'none';
  const chart = new Chart(canvas, {
    type: 'line',
    data: { labels, datasets },
    options: makePerfOptions(yAxisOpts, fmtFn),
  });
  chartInstances[canvasId] = chart;
  return chart;
}

function renderMultiDimChart(canvasId, emptyId, legendId, rows, range, anchor, metaMap) {
  const canvas = document.getElementById(canvasId);
  const empty  = document.getElementById(emptyId);
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
  const datasets = buildDimDatasets(rows, bMs, grid, metaMap);
  const chart = new Chart(canvas, { type: 'line', data: { labels, datasets }, options: makeMultiDimOptions() });
  chartInstances[canvasId] = chart;
  renderChipLegend(document.getElementById(legendId), chart);
}

/* ── Y축 설정 ── */
const pctTicks = { callback: v => v + '%', font:{size:11}, color:'#64748b' };
const Y_PCT   = { min:0, max:100, ticks: pctTicks };
const Y_RUNQ  = { beginAtZero:true, suggestedMax: 2, ticks:{ font:{size:11}, color:'#64748b' } };  // 실행 큐/코어 — Linux 1·Windows 2 포화
const Y_IOPS  = { beginAtZero:true, suggestedMax: PERF_IOPS_SUGGESTED_MAX, ticks:{ precision:0, font:{size:11}, color:'#64748b' } };
const Y_DISK_KBPS = { beginAtZero:true, suggestedMax: PERF_DISK_KBPS_SUGGESTED_MAX, ticks:{ callback: v => fmtThroughput(v), font:{size:11}, color:'#64748b' } };
const Y_NET   = { beginAtZero:true, suggestedMax: PERF_NET_SUGGESTED_MAX, ticks:{ callback: v => fmtKbChart(v), font:{size:11}, color:'#64748b' } };
const Y_RETRANS = { beginAtZero:true, suggestedMax: 1, ticks:{ callback: v => v.toFixed(2) + '%', font:{size:11}, color:'#64748b' } };  // 1% = 성능 영향 임계

/* ── 개별 차트 로더 ── */

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

const CPUCLASS_META = {
  user:   { label: 'User',     color: ChartUtils.themeColor() },
  system: { label: 'System',   color: '#f59e0b' },
  iowait: { label: 'I/O Wait', color: '#ef4444' },
};
async function loadCpuClassChart(range, anchor) {
  const seq = ++seqs.cpuClass;
  const [u, s, io] = await Promise.all([
    fetchChart('cpu.user_percent', range, anchor),
    fetchChart('cpu.system_percent', range, anchor),
    fetchChart('cpu.iowait_percent', range, anchor),
  ]);
  if (seq !== seqs.cpuClass) return;
  const rows = [
    ..._safe(u).map(r => ({ ...r, dimension: 'user' })),
    ..._safe(s).map(r => ({ ...r, dimension: 'system' })),
    ..._safe(io).map(r => ({ ...r, dimension: 'iowait' })),
  ];
  renderMultiDimChart('cpuclass-canvas', 'cpuclass-empty', 'cpuclass-legend', rows, range, anchor, CPUCLASS_META);
}

// 실행 큐/코어 os-aware — Linux procs_running(R-state) / Windows Processor Queue Length. 분류 CPU 포화 신호.
// 한 카드에 OS별 2선(dimension=os_family). load(폐기, 분류 미사용) 대체. 앵커/윈도우/버킷은 makeBucketGrid 로 동일 적용.
const RUNQ_META = {
  linux:   { label: 'Linux',   color: ChartUtils.themeColor() },
  windows: { label: 'Windows', color: '#8b5cf6' },
};
function makeRunQueueOptions() {
  return {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode:'index', intersect:false },
    plugins: {
      legend: { display: false },
      tooltip: { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? ctx.parsed.y.toFixed(2) : '—'}` } },
    },
    scales: {
      x: { ticks:{ maxTicksLimit:10, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
      y: { ...Y_RUNQ, grid:{ color:'#f1f5f9' } },
    },
  };
}
async function loadRunQueueChart(range, anchor) {
  const seq = ++seqs.runQueue;
  const rows = await fetchChart('cpu.run_queue', range, anchor);
  if (seq !== seqs.runQueue) return;
  const safe = _safe(rows);
  const canvas = document.getElementById('runqueue-canvas');
  const empty  = document.getElementById('runqueue-empty');
  if (chartInstances['runqueue-canvas']) { chartInstances['runqueue-canvas'].destroy(); delete chartInstances['runqueue-canvas']; }
  if (!safe.length) {
    canvas.style.display = 'none'; empty.style.display = 'flex';
    renderChipLegend(document.getElementById('runqueue-legend'), null);
    return;
  }
  canvas.style.display = ''; empty.style.display = 'none';
  const bMs    = BUCKET_MS[AUTO_BUCKET[range]];
  const grid   = makeBucketGrid(range, anchor);
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const datasets = buildDimDatasets(safe, bMs, grid, RUNQ_META);
  const chart = new Chart(canvas, { type: 'line', data: { labels, datasets }, options: makeRunQueueOptions() });
  chartInstances['runqueue-canvas'] = chart;
  renderChipLegend(document.getElementById('runqueue-legend'), chart);
}

// 디스크 I/O 포화 os-aware — 정규화 없이 raw 2선(이중 Y축). Linux await(ms, 좌축) / Windows 큐 깊이(우축).
// 단위가 달라 한 축에 겹치면 스케일이 뭉개지므로 os별 축 분리(Linux/Windows 호스트는 서로 겹치지 않아 정직).
// backend disk.io_saturation 이 os_family dimension 반환. 앵커/윈도우/버킷은 makeBucketGrid 로 동일 적용.
const DISKSAT_META = {
  linux:   { label: 'Linux await (ms)', color: ChartUtils.themeColor(), axis: 'yA' },
  windows: { label: 'Windows 큐 깊이',   color: '#8b5cf6',              axis: 'yQ' },
};
async function loadDiskSaturationChart(range, anchor) {
  const seq = ++seqs.diskSat;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const rows = await fetchChart('disk.io_saturation', range, anchor);
  if (seq !== seqs.diskSat) return;
  const safe = _safe(rows);
  const canvas = document.getElementById('disksat-canvas');
  const empty  = document.getElementById('disksat-empty');
  if (chartInstances['disksat-canvas']) { chartInstances['disksat-canvas'].destroy(); delete chartInstances['disksat-canvas']; }
  if (!safe.length) {
    canvas.style.display = 'none'; empty.style.display = 'flex';
    renderChipLegend(document.getElementById('disksat-legend'), null);
    return;
  }
  canvas.style.display = ''; empty.style.display = 'none';
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const mkData = dim => {
    const m = {};
    for (const r of safe) if (r.dimension === dim) m[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;
    return grid.map(t => m[t] ?? null);
  };
  const datasets = [];
  for (const dim of ['linux', 'windows']) {
    const data = mkData(dim);
    if (!data.some(v => v != null)) continue;  // 해당 OS 표본 없으면 선 생략(빈 축 노출 안 함)
    const meta = DISKSAT_META[dim];
    datasets.push({
      label: meta.label, data, borderColor: meta.color, backgroundColor: meta.color, yAxisID: meta.axis,
      borderWidth: 2, pointRadius: 0, pointHoverRadius: 3, tension: 0.3, fill: false, spanGaps: false,
    });
  }
  chartInstances['disksat-canvas'] = new Chart(canvas, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode:'index', intersect:false },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? ctx.parsed.y.toFixed(1) : '—'}` } },
      },
      scales: {
        x:  { ticks:{ maxTicksLimit:10, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
        yA: { type:'linear', position:'left', beginAtZero:true, suggestedMax:20,
              ticks:{ callback: v => v + 'ms', font:{size:11}, color:'#64748b' }, grid:{ color:'#f1f5f9' } },
        yQ: { type:'linear', position:'right', beginAtZero:true, suggestedMax:2,
              ticks:{ font:{size:11}, color:'#64748b' }, grid:{ drawOnChartArea:false } },
      },
    },
  });
  renderChipLegend(document.getElementById('disksat-legend'), chartInstances['disksat-canvas']);
}

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

const MEMCOMP_META = {
  used:      { label: 'Used',      color: ChartUtils.themeColor() },
  available: { label: 'Available', color: '#8b5cf6' },
  cached:    { label: 'Cached',    color: '#22c55e' },
  buffers:   { label: 'Buffers',   color: '#f59e0b' },
};
async function loadMemCompChart(range, anchor) {
  const seq = ++seqs.memComp;
  const [used, avail, cached, buffers] = await Promise.all([
    fetchChart('mem.usage_percent', range, anchor),
    fetchChart('mem.available_percent', range, anchor),
    fetchChart('mem.cached_percent', range, anchor),
    fetchChart('mem.buffers_percent', range, anchor),
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

// 스왑 os-split — Linux swap(오버플로 신호, 평소 0)와 Windows pagefile(상시 baseline)은 의미가 달라 한 선 평균이
// 오도라 OS별 2선(dimension=os_family). backend metric_trend 이 env(collapse=True)에서 os 분리 반환. % 라 Y축 공유.
const SWAP_META = {
  linux:   { label: 'Linux swap',       color: ChartUtils.themeColor() },
  windows: { label: 'Windows pagefile', color: '#8b5cf6' },
};
async function loadSwapChart(range, anchor) {
  const seq = ++seqs.swap;
  const rows = await fetchChart('swap.usage_percent', range, anchor);
  if (seq !== seqs.swap) return;
  renderMultiDimChart('swap-canvas', 'swap-empty', 'swap-legend', _safe(rows), range, anchor, SWAP_META);
}

// 디스크 읽기/쓰기 IOPS — 환경 합산(Read/Write). 게스트 블록 디바이스(virtio, kind=physical)만 합산(이중 집계 회피).
async function loadPhysIoChart(range, anchor) {
  const seq = ++seqs.physIo;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const [read, write] = await Promise.all([
    fetchChart('disk.read_iops', range, anchor),
    fetchChart('disk.write_iops', range, anchor),
  ]);
  if (seq !== seqs.physIo) return;
  const rows = [
    ..._safe(read).map(r => ({ ...r, dimension: 'Read' })),
    ..._safe(write).map(r => ({ ...r, dimension: 'Write' })),
  ];
  const datasets = buildDatasets(rows, bMs, grid, null);
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const chart = setChart('physio-canvas', 'physio-empty', rows, Y_IOPS, v => Math.round(v)+' IOPS', datasets, labels);
  buildAvgMaxLegend('physio-legend', chart, { withToggle: true });
}

// 디스크 처리량(kBps) — 환경 합산(Read/Write). 물리 device 만 합산(IOPS 와 동일 device).
async function loadDiskKbpsChart(range, anchor) {
  const seq = ++seqs.diskKbps;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const [read, write] = await Promise.all([
    fetchChart('disk.read_kbps', range, anchor),
    fetchChart('disk.write_kbps', range, anchor),
  ]);
  if (seq !== seqs.diskKbps) return;
  const rows = [
    ..._safe(read).map(r => ({ ...r, dimension: 'Read' })),
    ..._safe(write).map(r => ({ ...r, dimension: 'Write' })),
  ];
  const datasets = buildDatasets(rows, bMs, grid, null);
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const chart = setChart('diskkbps-canvas', 'diskkbps-empty', rows, Y_DISK_KBPS, fmtThroughput, datasets, labels);
  buildAvgMaxLegend('diskkbps-legend', chart, { withToggle: true });
}

async function loadFsChart(range, anchor) {
  const seq = ++seqs.fs;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const avgRows = await fetchChart('fs.usage_percent', range, anchor);
  if (seq !== seqs.fs) return;
  const safeAvg = _safe(avgRows);
  const datasets = buildDatasets(safeAvg, bMs, grid, null);
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const chart = setChart('fs-canvas', 'fs-empty', safeAvg, Y_PCT, v => v.toFixed(1)+'%', datasets, labels);
  buildAvgMaxLegend('fs-legend', chart, { withToggle: true });
  updateMaxLabel('fs-max', computePeriodMax(safeAvg), v => v.toFixed(1)+'%', colorByMountPct);
}

// 네트워크 I/O — 환경 합산(RX/TX bytes).
async function loadNetIoChart(range, anchor) {
  const seq = ++seqs.netIo;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const [rx, tx] = await Promise.all([
    fetchChart('net.rx_bytes_per_sec', range, anchor),
    fetchChart('net.tx_bytes_per_sec', range, anchor),
  ]);
  if (seq !== seqs.netIo) return;
  const rows = [
    ..._safe(rx).map(r => ({ ...r, dimension: 'RX' })),
    ..._safe(tx).map(r => ({ ...r, dimension: 'TX' })),
  ];
  const datasets = buildDatasets(rows, bMs, grid, null);
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  const chart = setChart('netio-canvas', 'netio-empty', rows, Y_NET, fmtKbChart, datasets, labels);
  buildAvgMaxLegend('netio-legend', chart, { withToggle: true });
}

// TCP 재전송율 % — 네트워크 "문제" 신호(활동 아닌 품질). Σ(Δretrans)/Σ(Δtx_packets)*100, 분류 net_retrans 와 동일 산식.
// 처리량(PPS) 대체 — 절대 패킷 수는 처리량 중복이라 폐기, 재전송율이 성능 영향 신호(>1%). 양 OS 동일 신호 단일선.
async function loadRetransChart(range, anchor) {
  const seq = ++seqs.retrans;
  const bMs = BUCKET_MS[AUTO_BUCKET[range]];
  const grid = makeBucketGrid(range, anchor);
  const avgRows = await fetchChart('net.retrans_percent', range, anchor);
  if (seq !== seqs.retrans) return;
  const safeAvg = _safe(avgRows);
  const datasets = buildDatasets(safeAvg, bMs, grid, '재전송율');
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), range));
  setChart('retrans-canvas', 'retrans-empty', safeAvg, Y_RETRANS, v => v.toFixed(2)+'%', datasets, labels);
  updateMaxLabel('retrans-max', computePeriodMax(safeAvg), v => v.toFixed(2)+'%', null);
}

/* ── 전체 로드 ── */
function updateBucketLabel(range) {
  document.getElementById('bucket-label').textContent = BUCKET_LABEL[AUTO_BUCKET[range]] || '';
}

async function loadAllCharts() {
  const range  = globalRange;
  const anchor = getAnchorEnd();
  updateBucketLabel(range);
  await Promise.all([
    loadCpuChart(range, anchor),     loadCpuClassChart(range, anchor),
    loadRunQueueChart(range, anchor), loadMemChart(range, anchor),
    loadMemCompChart(range, anchor), loadSwapChart(range, anchor),
    loadPhysIoChart(range, anchor),  loadDiskKbpsChart(range, anchor),
    loadDiskSaturationChart(range, anchor), loadFsChart(range, anchor),
    loadNetIoChart(range, anchor),   loadRetransChart(range, anchor),
  ]);
}

/* ── 인쇄 리사이즈 ── */
function resizeAllCharts() {
  for (const c of Object.values(chartInstances)) { if (c) c.resize(); }
}
window.addEventListener('beforeprint', resizeAllCharts);
window.addEventListener('afterprint', resizeAllCharts);

/* ── 날짜 인풋 초기화 + 컨트롤 바인딩 ── */
ChartUtils.initAnchor('anchor-date');
bindToggle('global-range-btns', val => { globalRange = val; loadAllCharts(); });
// 앵커 변경 즉시 반영 — 구간 토글·상세 차트(cpu/network/storage)와 동일 (적용 버튼 없이 change 로 갱신).
document.getElementById('anchor-date').addEventListener('change', () => loadAllCharts());

loadAllCharts();
