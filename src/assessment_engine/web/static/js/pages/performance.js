/**
 * 성능 리포트 페이지 차트 로직.
 *
 * 외부 의존:
 * - ChartUtils (base.html에서 chart-utils.js 로드)
 * - Chart.js (페이지에서 chart.umd.min.js 로드)
 * - SERVER_ID, CPU_CORES (페이지 inline <script>가 Jinja2로 정의)
 */
// ChartUtils — /static/js/chart-utils.js (base.html에서 로드)
const { RANGE_LABEL, AUTO_BUCKET, BUCKET_LABEL, BUCKET_MS, RANGE_MS, COLORS,
        fmtKbChart, safeArray } = ChartUtils;


const PERF_IOPS_SUGGESTED_MAX = 200;              // HDD 랜덤 I/O 한계(~100–200 IOPS) 기준
const PERF_NET_SUGGESTED_MAX  = 10 * 1024 * 1024; // 10 MB/s — 1 Gbps 이더넷의 약 8%

// 색상 임계값 — 서버 mappers._usage_bar_color 와 동일 기준. 변경 시 양쪽 동기화.
const USAGE_DANGER_PCT  = 90;
const USAGE_WARN_PCT    = 75;
const SWAP_DANGER_PCT   = 0.1;  // 스왑 사용 자체가 이슈
const COLOR_NEUTRAL = '#64748b';
const COLOR_WARN    = '#f59e0b';
const COLOR_DANGER  = '#ef4444';
const colorByMountPct = v => v >= USAGE_DANGER_PCT ? COLOR_DANGER : v >= USAGE_WARN_PCT ? COLOR_WARN : COLOR_NEUTRAL;
const colorBySwapPct  = v => v >= SWAP_DANGER_PCT  ? COLOR_DANGER : COLOR_NEUTRAL;

const PHYS_DISK_RE = /^(sd[a-z]+|vd[a-z]+|hd[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+)$/;
function isPhysicalDisk(name) { return PHYS_DISK_RE.test(name); }

let globalRange = '24h';
let globalSeq   = 0;
const chartInstances = {};

/* ── 유틸 ──
 * P4(b) capture-before-await: 모든 함수가 range/anchor를 인자로 받음.
 * 호출자(차트 로더)가 await 직전에 globalRange를 로컬 캡처 후 전달 → stale 응답 race 방지.
 */
const fmtLabel = (iso, range) => ChartUtils.fmtLabel(iso, range);
const makeBucketGrid = (range, anchor) => ChartUtils.makeBucketGrid(range, AUTO_BUCKET[range], anchor);
const getAnchorEnd = () => ChartUtils.getAnchorEnd('anchor-date');

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

async function fetchChart(metricType, agg, range, anchor) {
  const p = new URLSearchParams({
    metric_type: metricType,
    time_range:  range,
    bucket:      AUTO_BUCKET[range],
    agg,
  });
  if (anchor) p.append('end', anchor.toISOString());
  const res = await fetch(`/api/v1/servers/${SERVER_ID}/metrics/chart?${p}`);
  // P4(d): 404는 데이터 부재. 빈 배열 반환 → setChart가 empty 분기.
  if (res.status === 404) return [];
  if (!res.ok) return [];
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
            const maxDs = ctx.chart.data.datasets[ctx.datasetIndex + 1];
            const realMax = maxDs?.realData?.[ctx.dataIndex];
            const label = ctx.dataset.label ? ` ${ctx.dataset.label}:` : '';
            const avgStr = avg != null ? fmtFn(avg) : '—';
            return realMax != null
              ? `${label} 평균 ${avgStr} / 최대 ${fmtFn(realMax)}`
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

/* avg+max ghost 데이터셋 빌드
 * dashFn(dim) → borderDash array — 물리/가상 구분 등에 사용 */
function buildDatasets(avgRows, maxRows, bMs, grid, labelOverride, dashFn) {
  const dims = [...new Set([...avgRows, ...maxRows].map(r => r.dimension || ''))];
  const datasets = [];
  dims.forEach((dim, i) => {
    const color = COLORS[i % COLORS.length];
    const dash  = dashFn ? dashFn(dim) : [];
    const avgMap = {}, maxMap = {};
    for (const r of avgRows) if ((r.dimension || '') === dim)
      avgMap[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;
    for (const r of maxRows) if ((r.dimension || '') === dim)
      maxMap[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;

    const avgData         = grid.map(t => avgMap[t] ?? null);
    const realMaxData     = grid.map(t => maxMap[t] ?? null);
    const bufferedMaxData = grid.map(t => avgMap[t] == null ? null : (maxMap[t] ?? avgMap[t]));

    datasets.push({
      label: labelOverride || dim || 'Value',
      data: avgData,
      borderColor: color, backgroundColor: color + '28',
      borderWidth: 2, borderDash: dash,
      pointRadius: 0, pointHoverRadius: 3,
      tension: 0.3, fill: '+1', spanGaps: false,
    });
    datasets.push({
      label: (labelOverride || dim || 'Value') + '__max',
      data: bufferedMaxData, realData: realMaxData,
      borderColor: 'transparent', backgroundColor: 'transparent',
      borderWidth: 0, pointRadius: 0, pointHoverRadius: 0,
      fill: false, spanGaps: false,
    });
  });
  return datasets;
}

function buildLegend(containerId, chart, codeLabel = false) {
  const el = document.getElementById(containerId);
  if (!el || !chart) { if (el) el.innerHTML = ''; return; }
  const avgDatasets = chart.data.datasets.filter((_, i) => i % 2 === 0);
  el.innerHTML = avgDatasets.map(ds => {
    const isDash = ds.borderDash && ds.borderDash.length > 0;
    const lineHtml = isDash
      ? `<svg width="20" height="3" style="flex-shrink:0;"><line x1="0" y1="1.5" x2="20" y2="1.5" stroke="${ds.borderColor}" stroke-width="2" stroke-dasharray="4 2"/></svg>`
      : `<span style="width:20px; height:3px; border-radius:2px; background:${ds.borderColor}; flex-shrink:0;"></span>`;
    const labelHtml = codeLabel
      ? `<code style="font-size:11px; background:#f1f5f9; padding:1px 5px; border-radius:3px; color:#334155;">${ds.label}</code>`
      : `<span style="font-size:12px; color:#475569;">${ds.label}</span>`;
    return `<span style="display:flex; align-items:center; gap:5px;">
      ${lineHtml}
      ${labelHtml}
    </span>`;
  }).join('');
}

function setChart(canvasId, emptyId, avgRows, yAxisOpts, fmtFn, datasets, labels) {
  const canvas = document.getElementById(canvasId);
  const empty  = document.getElementById(emptyId);
  if (chartInstances[canvasId]) { chartInstances[canvasId].destroy(); delete chartInstances[canvasId]; }
  if (!avgRows.length) {
    canvas.style.display = 'none'; empty.style.display = '';
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

/* ── Y축 설정 ── */
const pctTicks = { callback: v => v + '%', font:{size:11}, color:'#64748b' };
const Y_PCT   = { min:0, max:100, ticks: pctTicks };
const Y_MEM   = { min:0, max:100, ticks: pctTicks };
const Y_SWAP  = { min:0, beginAtZero:true, suggestedMax:25, ticks: pctTicks };
const Y_LOAD  = { beginAtZero:true, suggestedMax: CPU_CORES || 4, ticks:{ font:{size:11}, color:'#64748b' }, title:{ display:true, text:'Load', font:{size:11}, color:'#94a3b8' } };
const Y_IOPS  = { beginAtZero:true, suggestedMax: PERF_IOPS_SUGGESTED_MAX, ticks:{ precision:0, font:{size:11}, color:'#64748b' }, title:{ display:true, text:'IOPS', font:{size:11}, color:'#94a3b8' } };
const Y_NET   = { beginAtZero:true, suggestedMax: PERF_NET_SUGGESTED_MAX, ticks:{ callback: v => fmtKbChart(v), font:{size:11}, color:'#64748b' } };

/* ── 개별 차트 로더 (P4: 단일 globalSeq로 stale 응답 폐기) ── */
const _safe = safeArray;

async function loadCpuChart(seq, capturedRange, capturedAnchor) {
  const bMs = BUCKET_MS[AUTO_BUCKET[capturedRange]];
  const grid = makeBucketGrid(capturedRange, capturedAnchor);
  const [avgRows, maxRows] = await Promise.all([fetchChart('cpu.usage_percent','avg', capturedRange, capturedAnchor), fetchChart('cpu.usage_percent','max', capturedRange, capturedAnchor)]);
  if (seq !== globalSeq) return;
  const safeAvg = _safe(avgRows), safeMax = _safe(maxRows);
  const datasets = buildDatasets(safeAvg, safeMax, bMs, grid, 'CPU');
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), capturedRange));
  setChart('cpu-canvas', 'cpu-empty', safeAvg, Y_PCT, v => v.toFixed(1)+'%', datasets, labels);
  updateMaxLabel('cpu-max', computePeriodMax(safeMax), v => v.toFixed(1)+'%', null);
}

async function loadMemChart(seq, capturedRange, capturedAnchor) {
  const bMs = BUCKET_MS[AUTO_BUCKET[capturedRange]];
  const grid = makeBucketGrid(capturedRange, capturedAnchor);
  const [avgRows, maxRows] = await Promise.all([fetchChart('mem.usage_percent','avg', capturedRange, capturedAnchor), fetchChart('mem.usage_percent','max', capturedRange, capturedAnchor)]);
  if (seq !== globalSeq) return;
  const safeAvg = _safe(avgRows), safeMax = _safe(maxRows);
  const datasets = buildDatasets(safeAvg, safeMax, bMs, grid, '메모리');
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), capturedRange));
  setChart('mem-canvas', 'mem-empty', safeAvg, Y_MEM, v => v.toFixed(1)+'%', datasets, labels);
  updateMaxLabel('mem-max', computePeriodMax(safeMax), v => v.toFixed(1)+'%', null);
}

async function loadSwapChart(seq, capturedRange, capturedAnchor) {
  const bMs = BUCKET_MS[AUTO_BUCKET[capturedRange]];
  const grid = makeBucketGrid(capturedRange, capturedAnchor);
  const [avgRows, maxRows] = await Promise.all([fetchChart('swap.usage_percent','avg', capturedRange, capturedAnchor), fetchChart('swap.usage_percent','max', capturedRange, capturedAnchor)]);
  if (seq !== globalSeq) return;
  const safeAvg = _safe(avgRows), safeMax = _safe(maxRows);
  const datasets = buildDatasets(safeAvg, safeMax, bMs, grid, '스왑');
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), capturedRange));
  setChart('swap-canvas', 'swap-empty', safeAvg, Y_SWAP, v => v.toFixed(1)+'%', datasets, labels);
  updateMaxLabel('swap-max', computePeriodMax(safeMax), v => v.toFixed(1)+'%', colorBySwapPct);
}

async function loadLoadChart(seq, capturedRange, capturedAnchor) {
  const bMs = BUCKET_MS[AUTO_BUCKET[capturedRange]];
  const grid = makeBucketGrid(capturedRange, capturedAnchor);
  const [avgRows, maxRows] = await Promise.all([fetchChart('load.15m','avg', capturedRange, capturedAnchor), fetchChart('load.15m','max', capturedRange, capturedAnchor)]);
  if (seq !== globalSeq) return;
  const safeAvg = _safe(avgRows), safeMax = _safe(maxRows);

  if (chartInstances['load-canvas']) { chartInstances['load-canvas'].destroy(); delete chartInstances['load-canvas']; }
  const canvas = document.getElementById('load-canvas');
  const empty  = document.getElementById('load-empty');
  if (!safeAvg.length) { canvas.style.display = 'none'; empty.style.display = ''; updateMaxLabel('load-max', null, v => v.toFixed(2), null); return; }
  canvas.style.display = ''; empty.style.display = 'none';

  const avgMap = {};
  for (const r of safeAvg) avgMap[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), capturedRange));
  const data   = grid.map(t => avgMap[t] ?? null);

  chartInstances['load-canvas'] = new Chart(canvas, {
    type: 'line',
    data: { labels, datasets: [{
      label: 'Load 15m', data,
      borderColor: '#f59e0b', backgroundColor: '#f59e0b22',
      borderWidth: 2, pointRadius: 0, pointHoverRadius: 3,
      tension: 0.3, fill: true, spanGaps: false,
    }] },
    options: makePerfOptions(Y_LOAD, v => v.toFixed(2)),
  });
  updateMaxLabel('load-max', computePeriodMax(safeMax), v => v.toFixed(2), null);
}

async function loadIowaitChart(seq, capturedRange, capturedAnchor) {
  const bMs = BUCKET_MS[AUTO_BUCKET[capturedRange]];
  const grid = makeBucketGrid(capturedRange, capturedAnchor);
  const [avgRows, maxRows] = await Promise.all([fetchChart('cpu.iowait_percent','avg', capturedRange, capturedAnchor), fetchChart('cpu.iowait_percent','max', capturedRange, capturedAnchor)]);
  if (seq !== globalSeq) return;
  const safeAvg = _safe(avgRows), safeMax = _safe(maxRows);
  const datasets = buildDatasets(safeAvg, safeMax, bMs, grid, 'iowait');
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), capturedRange));
  setChart('iowait-canvas', 'iowait-empty', safeAvg, Y_PCT, v => v.toFixed(1)+'%', datasets, labels);
  updateMaxLabel('iowait-max', computePeriodMax(safeMax), v => v.toFixed(1)+'%', null);
}

async function loadCpuUserSystemChart(seq, capturedRange, capturedAnchor) {
  const bMs = BUCKET_MS[AUTO_BUCKET[capturedRange]];
  const grid = makeBucketGrid(capturedRange, capturedAnchor);
  const labels = grid.map(t => fmtLabel(new Date(t).toISOString(), capturedRange));
  const [uAvg, uMax, sAvg, sMax] = await Promise.all([
    fetchChart('cpu.user_percent','avg', capturedRange, capturedAnchor), fetchChart('cpu.user_percent','max', capturedRange, capturedAnchor),
    fetchChart('cpu.system_percent','avg', capturedRange, capturedAnchor), fetchChart('cpu.system_percent','max', capturedRange, capturedAnchor),
  ]);
  if (seq !== globalSeq) return;
  const sUA = _safe(uAvg), sUM = _safe(uMax), sSA = _safe(sAvg), sSM = _safe(sMax);

  const canvas = document.getElementById('cpuus-canvas');
  const empty  = document.getElementById('cpuus-empty');
  if (chartInstances['cpuus-canvas']) { chartInstances['cpuus-canvas'].destroy(); delete chartInstances['cpuus-canvas']; }

  if (!sUA.length && !sSA.length) {
    canvas.style.display = 'none'; empty.style.display = '';
    updateMaxLabel('cpuus-max', null, v => v.toFixed(1)+'%', null);
    buildLegend('cpuus-legend', null);
    return;
  }
  canvas.style.display = ''; empty.style.display = 'none';

  const userDs   = buildDatasets(sUA, sUM, bMs, grid, 'user');
  userDs[0].borderColor = COLORS[0]; userDs[0].backgroundColor = COLORS[0] + '28';
  const systemDs = buildDatasets(sSA, sSM, bMs, grid, 'system');
  systemDs[0].borderColor = COLORS[2]; systemDs[0].backgroundColor = COLORS[2] + '28';

  const chart = new Chart(canvas, {
    type: 'line',
    data: { labels, datasets: [...userDs, ...systemDs] },
    options: makePerfOptions(Y_PCT, v => v.toFixed(1)+'%'),
  });
  chartInstances['cpuus-canvas'] = chart;
  buildLegend('cpuus-legend', chart);

  const allMax = [...sUM, ...sSM].map(r => r.value).filter(v => v != null);
  updateMaxLabel('cpuus-max', allMax.length ? Math.max(...allMax) : null, v => v.toFixed(1)+'%', null);
}

const diskDashFn = dim => isPhysicalDisk(dim) ? [] : [5, 3];

async function loadDiskReadChart(seq, capturedRange, capturedAnchor) {
  const bMs = BUCKET_MS[AUTO_BUCKET[capturedRange]];
  const grid = makeBucketGrid(capturedRange, capturedAnchor);
  const [avgRows, maxRows] = await Promise.all([fetchChart('disk.read_iops','avg', capturedRange, capturedAnchor), fetchChart('disk.read_iops','max', capturedRange, capturedAnchor)]);
  if (seq !== globalSeq) return;
  const safeAvg = _safe(avgRows), safeMax = _safe(maxRows);
  const datasets = buildDatasets(safeAvg, safeMax, bMs, grid, null, diskDashFn);
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), capturedRange));
  const chart = setChart('dread-canvas', 'dread-empty', safeAvg, Y_IOPS, v => Math.round(v)+' IOPS', datasets, labels);
  buildLegend('dread-legend', chart);
  updateMaxLabel('dread-max', computePeriodMax(safeMax), v => Math.round(v)+' IOPS', null);
}

async function loadDiskWriteChart(seq, capturedRange, capturedAnchor) {
  const bMs = BUCKET_MS[AUTO_BUCKET[capturedRange]];
  const grid = makeBucketGrid(capturedRange, capturedAnchor);
  const [avgRows, maxRows] = await Promise.all([fetchChart('disk.write_iops','avg', capturedRange, capturedAnchor), fetchChart('disk.write_iops','max', capturedRange, capturedAnchor)]);
  if (seq !== globalSeq) return;
  const safeAvg = _safe(avgRows), safeMax = _safe(maxRows);
  const datasets = buildDatasets(safeAvg, safeMax, bMs, grid, null, diskDashFn);
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), capturedRange));
  const chart = setChart('dwrite-canvas', 'dwrite-empty', safeAvg, Y_IOPS, v => Math.round(v)+' IOPS', datasets, labels);
  buildLegend('dwrite-legend', chart);
  updateMaxLabel('dwrite-max', computePeriodMax(safeMax), v => Math.round(v)+' IOPS', null);
}

async function loadNetRxChart(seq, capturedRange, capturedAnchor) {
  const bMs = BUCKET_MS[AUTO_BUCKET[capturedRange]];
  const grid = makeBucketGrid(capturedRange, capturedAnchor);
  const [avgRows, maxRows] = await Promise.all([fetchChart('net.rx_bytes_per_sec','avg', capturedRange, capturedAnchor), fetchChart('net.rx_bytes_per_sec','max', capturedRange, capturedAnchor)]);
  if (seq !== globalSeq) return;
  const safeAvg = _safe(avgRows), safeMax = _safe(maxRows);
  const datasets = buildDatasets(safeAvg, safeMax, bMs, grid, null);
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), capturedRange));
  const chart = setChart('netrx-canvas', 'netrx-empty', safeAvg, Y_NET, fmtKbChart, datasets, labels);
  buildLegend('netrx-legend', chart);
  updateMaxLabel('netrx-max', computePeriodMax(safeMax), fmtKbChart, null);
}

async function loadNetTxChart(seq, capturedRange, capturedAnchor) {
  const bMs = BUCKET_MS[AUTO_BUCKET[capturedRange]];
  const grid = makeBucketGrid(capturedRange, capturedAnchor);
  const [avgRows, maxRows] = await Promise.all([fetchChart('net.tx_bytes_per_sec','avg', capturedRange, capturedAnchor), fetchChart('net.tx_bytes_per_sec','max', capturedRange, capturedAnchor)]);
  if (seq !== globalSeq) return;
  const safeAvg = _safe(avgRows), safeMax = _safe(maxRows);
  const datasets = buildDatasets(safeAvg, safeMax, bMs, grid, null);
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), capturedRange));
  const chart = setChart('nettx-canvas', 'nettx-empty', safeAvg, Y_NET, fmtKbChart, datasets, labels);
  buildLegend('nettx-legend', chart);
  updateMaxLabel('nettx-max', computePeriodMax(safeMax), fmtKbChart, null);
}

async function loadMountChart(seq, capturedRange, capturedAnchor) {
  const bMs = BUCKET_MS[AUTO_BUCKET[capturedRange]];
  const grid = makeBucketGrid(capturedRange, capturedAnchor);
  const [avgRows, maxRows] = await Promise.all([fetchChart('fs.usage_percent','avg', capturedRange, capturedAnchor), fetchChart('fs.usage_percent','max', capturedRange, capturedAnchor)]);
  if (seq !== globalSeq) return;
  const safeAvg = _safe(avgRows), safeMax = _safe(maxRows);
  const datasets = buildDatasets(safeAvg, safeMax, bMs, grid, null);
  const labels   = grid.map(t => fmtLabel(new Date(t).toISOString(), capturedRange));
  const chart = setChart('mount-canvas', 'mount-empty', safeAvg, Y_PCT, v => v.toFixed(1)+'%', datasets, labels);
  buildLegend('mount-legend', chart, true);
  const maxVal = computePeriodMax(safeMax);
  updateMaxLabel('mount-max', maxVal, v => v.toFixed(1)+'%', colorByMountPct);
}

/* ── 전체 로드 ── */
function updateBucketLabel(range) {
  document.getElementById('bucket-label').textContent = BUCKET_LABEL[AUTO_BUCKET[range]] || '';
}

async function loadAllCharts() {
  const seq = ++globalSeq;
  // P4(b) capture-before-await: 호출 시점의 range/anchor를 캡처해 모든 로더에 전달.
  // 이후 사용자가 range 토글하면 새 loadAllCharts가 새 capturedRange로 시작 + seq 증가 → 이전 응답 폐기.
  const capturedRange  = globalRange;
  const capturedAnchor = getAnchorEnd();
  updateBucketLabel(capturedRange);
  await Promise.all([
    loadMountChart(seq, capturedRange, capturedAnchor),
    loadCpuChart(seq, capturedRange, capturedAnchor), loadIowaitChart(seq, capturedRange, capturedAnchor),
    loadCpuUserSystemChart(seq, capturedRange, capturedAnchor), loadLoadChart(seq, capturedRange, capturedAnchor),
    loadMemChart(seq, capturedRange, capturedAnchor), loadSwapChart(seq, capturedRange, capturedAnchor),
    loadDiskReadChart(seq, capturedRange, capturedAnchor), loadDiskWriteChart(seq, capturedRange, capturedAnchor),
    loadNetRxChart(seq, capturedRange, capturedAnchor), loadNetTxChart(seq, capturedRange, capturedAnchor),
  ]);
}

/* ── 최근 수집 시간 ── */
async function loadLastMetricTs() {
  const el = document.getElementById('last-metric-ts');
  // P4(d) 404 분기: try/catch 이전에 status 검사로 데이터 부재를 명시 분기.
  const res = await fetch(`/api/v1/servers/${SERVER_ID}/collection-status`);
  if (res.status === 404 || !res.ok) { el.textContent = '—'; return; }
  try {
    const data = await res.json();
    if (!data.last_metric_at) { el.textContent = '—'; return; }
    const d = new Date(new Date(data.last_metric_at).getTime() + 9 * 60 * 60 * 1000);
    const pad = n => String(n).padStart(2, '0');
    el.textContent = `${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
  } catch { el.textContent = '—'; }
}

/* ── 날짜 인풋 초기화 ── */
(function initAnchorDate() {
  const input = document.getElementById('anchor-date');
  const kstNow = new Date().toLocaleString('sv-SE', { timeZone: 'Asia/Seoul' }).slice(0, 16).replace(' ', 'T');
  input.max   = kstNow;
  input.value = kstNow;
})();

document.getElementById('global-range-btns').addEventListener('click', e => {
  const btn = e.target.closest('.toggle');
  if (!btn) return;
  document.querySelectorAll('#global-range-btns .toggle').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  globalRange = btn.dataset.val;
  loadAllCharts();
});

document.getElementById('anchor-date').addEventListener('change', () => loadAllCharts());

loadAllCharts();
loadLastMetricTs();
