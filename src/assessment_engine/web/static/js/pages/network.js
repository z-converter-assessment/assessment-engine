/**
 * network 페이지 차트 로직.
 *
 * 외부 의존:
 * - ChartUtils (base.html에서 chart-utils.js 로드)
 * - Chart.js (페이지에서 chart.umd.min.js 로드)
 * - SERVER_ID (페이지 inline <script>가 Jinja2로 정의)
 */
// ChartUtils — /static/js/chart-utils.js (base.html에서 로드)
const { RANGE_LABEL, AUTO_BUCKET, BUCKET_LABEL, BUCKET_MS, COLORS,
        fmtKst, fmtKbChart, getAnchorEnd, initAnchor,
        makeBucketGrid, joinToGrid, bindToggle, initSse, safeArray,
        fetchRebootEvents, applyRebootMarkers } = ChartUtils;

// 추이 차트의 분해력 기준 (다중 interface x RX/TX 다중 라인 — idle 환경 트래픽도 보이도록).
// 진단 리포트(performance.html)는 다른 정책: PERF_NET_SUGGESTED_MAX = 10 MB/s (1 Gbps의 8%).
// 실데이터가 본 값을 초과하면 자동 확장 (soft ceiling). Y축 단위는 fmtKbChart로 동적 표기.
const NET_Y_SUGGESTED_MAX = 2048; // B/s ≈ 2 kB/s

function fmtKbps(v) {
  if (v == null) return '—';
  if (v >= 1024) return (v / 1024).toFixed(1) + ' MBps';
  return v.toFixed(1) + ' kBps';
}
function fmtPps(v) { return v != null ? Math.round(v) + ' pps' : '—'; }

async function loadNetSnapshot() {
  try {
    const res = await fetch(`/api/v1/servers/${SERVER_ID}/metrics/latest`);
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
        <td><code>${iface.interface}</code></td>
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
let netChart = null;
let netSeq   = 0;

function updateNetBucketLabel() {
  document.getElementById('net-bucket-label').textContent = BUCKET_LABEL[AUTO_BUCKET[netRange]] || '';
}

const fmtNetLabel = ChartUtils.fmtLabel;

function renderNetChart(avgRows, maxRows, range, anchorEnd) {
  const empty  = document.getElementById('net-chart-empty');
  const canvas = document.getElementById('net-chart-canvas');

  if (!avgRows.length) {
    canvas.style.display = 'none';
    empty.style.display  = '';
    if (netChart) { netChart.destroy(); netChart = null; }
    return;
  }

  canvas.style.display = '';
  empty.style.display  = 'none';

  const bMs    = BUCKET_MS[AUTO_BUCKET[range]];
  const grid   = makeBucketGrid(range, AUTO_BUCKET[range], anchorEnd);
  const labels = grid.map(t => fmtNetLabel(new Date(t).toISOString(), range));

  const dims = [...new Set(avgRows.map(r => r.dimension))];
  const datasets = [];
  dims.forEach((dim, i) => {
    const color = COLORS[i % COLORS.length];
    const avgMap = {}, maxMap = {};
    for (const r of avgRows.filter(r => r.dimension === dim))
      avgMap[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;
    for (const r of maxRows.filter(r => r.dimension === dim))
      maxMap[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;

    datasets.push({
      label: dim,
      data: grid.map(t => avgMap[t] ?? null),
      borderColor: color,
      backgroundColor: color + '28',
      borderWidth: 2,
      pointRadius: 1,
      pointHoverRadius: 3,
      tension: 0.3,
      fill: '+1',
      spanGaps: false,
    });
    const realMaxData = grid.map(t => maxMap[t] ?? null);
    const bufferedMaxData = grid.map(t => {
      const avgVal = avgMap[t];
      if (avgVal == null) return null;
      return maxMap[t] ?? avgVal;
    });
    datasets.push({
      label: dim + '__max',
      data: bufferedMaxData,
      realData: realMaxData,
      borderColor: 'transparent',
      backgroundColor: 'transparent',
      borderWidth: 0,
      pointRadius: 0,
      pointHoverRadius: 0,
      tension: 0.3,
      fill: false,
      spanGaps: false,
    });
  });

  if (netChart) {
    netChart.data.labels   = labels;
    netChart.data.datasets = datasets;
    netChart.update('none');
    return;
  }

  netChart = new Chart(canvas, {
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
              const maxDs = netChart?.data.datasets[ctx.datasetIndex + 1];
              const realMax = maxDs?.realData?.[ctx.dataIndex];
              if (realMax != null)
                return ` ${ctx.dataset.label}: 평균 ${fmtKbChart(avg)} / 최대 ${fmtKbChart(realMax)}`;
              return ` ${ctx.dataset.label}: ${fmtKbChart(avg)}`;
            }
          },
        },
      },
      scales: {
        x: { ticks:{ maxTicksLimit:12, font:{size:11}, color:'#94a3b8' }, grid:{ color:'#f1f5f9' } },
        y: {
          title: { display:true, text:'처리량', font:{size:11}, color:'#94a3b8' },
          ticks: { callback: v => fmtKbChart(v), font:{size:11}, color:'#64748b' },
          grid:  { color:'#f1f5f9' },
          beginAtZero: true,
          suggestedMax: NET_Y_SUGGESTED_MAX,
        },
      },
    },
  });
}

function buildNetLegend() {
  const container = document.getElementById('net-legend');
  if (!netChart) { container.innerHTML = ''; return; }
  const avgDatasets = netChart.data.datasets.filter((_, i) => i % 2 === 0);
  container.innerHTML = avgDatasets.map((ds, i) => `
    <label style="display:flex; align-items:center; gap:6px; font-size:12px; color:#475569; cursor:pointer; user-select:none;">
      <input type="checkbox" data-avg="${i * 2}" data-max="${i * 2 + 1}" checked
        style="accent-color:${ds.borderColor}; width:13px; height:13px; cursor:pointer;">
      <span>${ds.label}</span>
    </label>
  `).join('');
  container.querySelectorAll('input[type=checkbox]').forEach(cb => {
    cb.addEventListener('change', () => {
      netChart.getDatasetMeta(+cb.dataset.avg).hidden = !cb.checked;
      netChart.getDatasetMeta(+cb.dataset.max).hidden = !cb.checked;
      netChart.update();
    });
  });
}

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
      fetch(`/api/v1/servers/${SERVER_ID}/metrics/chart?${mkP('net.rx_bytes_per_sec', 'avg')}`).then(r => r.json()),
      fetch(`/api/v1/servers/${SERVER_ID}/metrics/chart?${mkP('net.rx_bytes_per_sec', 'max')}`).then(r => r.json()),
      fetch(`/api/v1/servers/${SERVER_ID}/metrics/chart?${mkP('net.tx_bytes_per_sec', 'avg')}`).then(r => r.json()),
      fetch(`/api/v1/servers/${SERVER_ID}/metrics/chart?${mkP('net.tx_bytes_per_sec', 'max')}`).then(r => r.json()),
    ]);
    if (seq !== netSeq) return;
    const safe = arr => Array.isArray(arr) ? arr : [];
    const avgRows = [
      ...safe(rxAvg).map(r => ({ ...r, dimension: `${r.dimension} RX` })),
      ...safe(txAvg).map(r => ({ ...r, dimension: `${r.dimension} TX` })),
    ];
    const maxRows = [
      ...safe(rxMax).map(r => ({ ...r, dimension: `${r.dimension} RX` })),
      ...safe(txMax).map(r => ({ ...r, dimension: `${r.dimension} TX` })),
    ];
    if (netChart) { netChart.destroy(); netChart = null; }
    renderNetChart(avgRows, maxRows, capturedRange, capturedAnchor);
    buildNetLegend();
    const events = await fetchRebootEvents(SERVER_ID, capturedRange, capturedAnchor);
    if (seq !== netSeq) return;
    const grid = makeBucketGrid(capturedRange, AUTO_BUCKET[capturedRange], capturedAnchor);
    applyRebootMarkers(netChart, events, grid);
  } catch(e) {
    console.error(e);
  }
}

bindToggle('net-range-btns', v => { netRange = v; updateNetBucketLabel(); document.getElementById('net-range-print').textContent = ' — ' + RANGE_LABEL[v]; loadNetChart(); });
document.getElementById('net-anchor').addEventListener('change', () => loadNetChart());

/* ── SSE ── */
initSse(SERVER_ID, loadNetSnapshot);

initAnchor('net-anchor');
updateNetBucketLabel();
loadNetSnapshot();
loadNetChart();
