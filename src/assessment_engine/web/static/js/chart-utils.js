// 차트 템플릿 공통 유틸. 모든 차트 페이지가 import.
// CLAUDE.md §E1 P4 의무 규약(sequence counter, capture-before-await,
// Array.isArray 방어, 404 분기, suggestedMax 명명 상수)의 도구 모음.

(function (root) {
  // ── 시간 범위 / 버킷 매핑 ──
  // 14d는 right-sizing 윈도우(recommendation.WINDOW_DAYS)와 동일 — 보고서·대시보드·차트 일관.
  // 14일 → 6시간 버킷 자동 매핑 (14*24/6 = 56 데이터 포인트, 가독성·표시 부담 균형).
  const RANGE_LABEL  = { '15m':'15분', '1h':'1시간', '6h':'6시간', '24h':'1일', '7d':'7일', '14d':'14일', '30d':'30일' };
  const AUTO_BUCKET  = { '15m':'1m', '1h':'5m', '6h':'15m', '24h':'30m', '7d':'3h', '14d':'6h', '30d':'12h' };
  const BUCKET_LABEL = { '1m':'1분 집계', '5m':'5분 집계', '15m':'15분 집계', '30m':'30분 집계',
                         '1h':'1시간 집계', '3h':'3시간 집계', '6h':'6시간 집계', '12h':'12시간 집계', '1d':'1일 집계' };
  const RANGE_MS  = { '15m':9e5, '1h':36e5, '6h':216e5, '24h':864e5, '7d':6048e5, '14d':12096e5, '30d':2592e6 };
  const BUCKET_MS = { '1m':6e4, '5m':3e5, '15m':9e5, '30m':18e5, '1h':36e5, '3h':108e5, '6h':216e5, '12h':432e5, '1d':864e5 };

  // ── 색상 팔레트 ──
  const COLORS = ['#3b82f6','#f59e0b','#22c55e','#ef4444','#8b5cf6','#06b6d4','#f97316','#ec4899'];

  // ── 시간 포매팅 (KST) ──
  function fmtKst(isoStr) {
    const d = new Date(isoStr);
    const kst = new Date(d.getTime() + 9 * 60 * 60 * 1000);
    return kst.toISOString().replace('T', ' ').slice(0, 19);
  }

  function fmtLabel(ts, range) {
    const d = new Date(new Date(ts).getTime() + 9 * 60 * 60 * 1000);
    const MM = String(d.getUTCMonth() + 1).padStart(2, '0');
    const DD = String(d.getUTCDate()).padStart(2, '0');
    const HH = String(d.getUTCHours()).padStart(2, '0');
    const mm = String(d.getUTCMinutes()).padStart(2, '0');
    if (range === '30d') return `${MM}/${DD} ${HH}:00`;
    if (range === '7d')  return `${MM}/${DD} ${HH}:${mm}`;
    return `${HH}:${mm}`;
  }

  // ── 처리량 포매터 (B/s → kB/s → MB/s) ──
  function fmtKbChart(v) {
    if (v == null) return '';
    if (v >= 1024 * 1024) return (v / 1024 / 1024).toFixed(1) + ' MB/s';
    if (v >= 1024) return (v / 1024).toFixed(1) + ' kB/s';
    return v.toFixed(0) + ' B/s';
  }

  // ── 앵커 datetime 입력 처리 ──
  function getAnchorEnd(inputId) {
    const val = document.getElementById(inputId).value;
    if (!val) return null;
    const d = new Date(val + ':00+09:00');
    return d >= new Date() ? null : d;
  }

  function initAnchor(inputId) {
    const input = document.getElementById(inputId);
    const kstNow = new Date().toLocaleString('sv-SE', { timeZone: 'Asia/Seoul' }).slice(0, 16).replace(' ', 'T');
    input.max = kstNow;
    input.value = kstNow;
  }

  // ── 버킷 그리드 생성 ──
  function makeBucketGrid(rangeKey, bucketKey, anchorEnd) {
    const endMs = (anchorEnd || new Date()).getTime();
    const bMs   = BUCKET_MS[bucketKey];
    const start = endMs - RANGE_MS[rangeKey];
    const first = Math.ceil(start / bMs) * bMs;
    const grid  = [];
    for (let t = first; t <= endMs; t += bMs) grid.push(t);
    return grid;
  }

  function joinToGrid(grid, rows, bMs) {
    const map = {};
    for (const r of rows) {
      const t = Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs;
      map[t] = r.value;
    }
    return grid.map(t => map[t] ?? null);
  }

  // ── 토글 그룹 바인딩 ──
  function bindToggle(groupId, onChange) {
    document.getElementById(groupId).addEventListener('click', e => {
      const btn = e.target.closest('.toggle');
      if (!btn) return;
      document.querySelectorAll(`#${groupId} .toggle`).forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      onChange(btn.dataset.val);
    });
  }

  // ── SSE 초기화 ──
  function initSse(serverId, onMessage) {
    const es = new EventSource(`/api/servers/${serverId}/metrics/stream`);
    const dot = document.getElementById('sse-dot');
    const lbl = document.getElementById('sse-label');
    es.onopen    = () => { if (dot) dot.className = 'dot dot-ok'; if (lbl) lbl.textContent = '자동 갱신 중'; };
    es.onmessage = () => onMessage();
    es.onerror   = () => { if (dot) dot.className = 'dot dot-off'; if (lbl) lbl.textContent = '자동 갱신 중단 — 재연결 중...'; };
    return es;
  }

  // ── 응답 안전 변환 ──
  function safeArray(arr) { return Array.isArray(arr) ? arr : []; }

  // ── avg+max ghost dataset 빌드 (P4 패턴) ──
  // avgRows·maxRows: [{collected_at, value, dimension?}]
  // opts: { label?, color?, dashFn?(dim), pointRadius? }
  // single-dim(라벨 1개) 또는 multi-dim(dim별 색·dash) 통합.
  // 결과: [avgDataset, maxGhostDataset]쌍 N개. tooltip filter `datasetIndex % 2 === 0`로 max ghost 숨김.
  function buildAvgMaxDatasets(avgRows, maxRows, bMs, grid, opts = {}) {
    const dims = [...new Set([...avgRows, ...maxRows].map(r => r.dimension || ''))];
    const datasets = [];
    dims.forEach((dim, i) => {
      const color = opts.color || COLORS[i % COLORS.length];
      const dash  = opts.dashFn ? opts.dashFn(dim) : [];
      const avgMap = {}, maxMap = {};
      for (const r of avgRows) if ((r.dimension || '') === dim)
        avgMap[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;
      for (const r of maxRows) if ((r.dimension || '') === dim)
        maxMap[Math.floor(new Date(r.collected_at).getTime() / bMs) * bMs] = r.value;
      const avgData         = grid.map(t => avgMap[t] ?? null);
      const realMaxData     = grid.map(t => maxMap[t] ?? null);
      const bufferedMaxData = grid.map(t => avgMap[t] == null ? null : (maxMap[t] ?? avgMap[t]));
      const baseLabel = opts.label || dim || 'Value';
      datasets.push({
        label: baseLabel, data: avgData,
        borderColor: color, backgroundColor: color + '28',
        borderWidth: 2, borderDash: dash,
        pointRadius: opts.pointRadius ?? 1, pointHoverRadius: 3,
        tension: 0.3, fill: '+1', spanGaps: false,
      });
      datasets.push({
        label: baseLabel + '__max', data: bufferedMaxData, realData: realMaxData,
        borderColor: 'transparent', backgroundColor: 'transparent',
        borderWidth: 0, pointRadius: 0, pointHoverRadius: 0,
        fill: false, spanGaps: false,
      });
    });
    return datasets;
  }

  // ── 짝수 인덱스 avg dataset만 legend 표시 (max ghost 숨김) ──
  // opts: { codeLabel?: code 태그 사용, withToggle?: checkbox + avg/max 짝 hide 토글 (P4 허용 — E1 P4 절) }
  function buildAvgMaxLegend(containerId, chart, opts = {}) {
    const el = document.getElementById(containerId);
    if (!el || !chart) { if (el) el.innerHTML = ''; return; }
    const avgDatasets = chart.data.datasets.filter((_, i) => i % 2 === 0);
    el.innerHTML = avgDatasets.map((ds, i) => {
      const isDash = ds.borderDash && ds.borderDash.length > 0;
      const lineHtml = isDash
        ? `<svg width="20" height="3" style="flex-shrink:0;"><line x1="0" y1="1.5" x2="20" y2="1.5" stroke="${ds.borderColor}" stroke-width="2" stroke-dasharray="4 2"/></svg>`
        : `<span style="width:20px; height:3px; border-radius:2px; background:${ds.borderColor}; flex-shrink:0;"></span>`;
      const labelHtml = opts.codeLabel
        ? `<code style="font-size:11px; background:#f1f5f9; padding:1px 5px; border-radius:3px; color:#334155;">${ds.label}</code>`
        : `<span style="font-size:12px; color:#475569;">${ds.label}</span>`;
      if (opts.withToggle) {
        return `<label style="display:flex; align-items:center; gap:6px; cursor:pointer; user-select:none;">
          <input type="checkbox" data-avg="${i * 2}" checked
            style="accent-color:${ds.borderColor}; width:13px; height:13px; cursor:pointer;">
          ${lineHtml}${labelHtml}
        </label>`;
      }
      return `<span style="display:flex; align-items:center; gap:5px;">${lineHtml}${labelHtml}</span>`;
    }).join('');
    if (opts.withToggle) {
      el.querySelectorAll('input[type=checkbox]').forEach(cb => {
        cb.addEventListener('change', () => {
          const avgIdx = +cb.dataset.avg;
          chart.getDatasetMeta(avgIdx).hidden     = !cb.checked;
          chart.getDatasetMeta(avgIdx + 1).hidden = !cb.checked;
          chart.update();
        });
      });
    }
  }

  // ── reboot / agent restart 이벤트 (차트 vertical marker용) ──
  // 백엔드 API: GET /api/servers/{id}/events/reboot?time_range=...&end=...
  // 응답: [{collected_at, boot_time, agent_started_at, kind: "reboot"|"restart"}]
  async function fetchRebootEvents(serverId, range, anchor) {
    const p = new URLSearchParams({ time_range: range });
    if (anchor) p.append('end', anchor.toISOString());
    const res = await fetch(`/api/servers/${serverId}/events/reboot?${p}`);
    if (res.status === 404 || !res.ok) return [];
    const data = await res.json();
    return Array.isArray(data) ? data : [];
  }

  // grid는 timestamp(ms) 오름차순 배열. ts에 가장 가까운(<=) 인덱스 반환. 없으면 -1.
  function _findGridIndex(grid, ts) {
    if (!grid.length || ts < grid[0]) return -1;
    let lo = 0, hi = grid.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (grid[mid] <= ts) lo = mid; else hi = mid - 1;
    }
    return lo;
  }

  // Chart.js custom plugin — 차트 options.plugins.rebootMarkers.{events, gridMs} 읽어
  // vertical dashed line + 작은 라벨 그림. afterDraw로 dataset 위에 덮어 그림.
  // 색상: reboot=red(#ef4444), restart=amber(#f59e0b). 기존 USAGE_DANGER/WARN 색과 일치.
  const rebootMarkersPlugin = {
    id: 'rebootMarkers',
    afterDraw(chart) {
      const opts = chart.options.plugins?.rebootMarkers;
      if (!opts || !Array.isArray(opts.events) || !opts.events.length) return;
      const grid = opts.gridMs;
      if (!Array.isArray(grid) || !grid.length) return;
      const xScale = chart.scales.x;
      if (!xScale) return;
      const area = chart.chartArea;
      const ctx = chart.ctx;
      ctx.save();
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 3]);
      ctx.font = '10px sans-serif';
      ctx.textAlign = 'left';
      for (const ev of opts.events) {
        const ts = new Date(ev.collected_at).getTime();
        const idx = _findGridIndex(grid, ts);
        if (idx < 0) continue;
        const x = xScale.getPixelForValue(idx);
        if (x < area.left || x > area.right) continue;
        const color = ev.kind === 'reboot' ? '#ef4444' : '#f59e0b';
        ctx.strokeStyle = color;
        ctx.beginPath();
        ctx.moveTo(x, area.top);
        ctx.lineTo(x, area.bottom);
        ctx.stroke();
        ctx.fillStyle = color;
        ctx.fillText(ev.kind === 'reboot' ? '재부팅' : '재시작', x + 3, area.top + 11);
      }
      ctx.restore();
    },
  };
  // Chart.js 4.x global plugin 등록. base.html에서 chart.umd.min.js 로드 후 본 파일 실행.
  if (root.Chart && typeof root.Chart.register === 'function') {
    root.Chart.register(rebootMarkersPlugin);
  }

  // 차트 인스턴스에 marker 옵션 주입 + animation 없이 redraw.
  // 호출자: 모든 chart load 후 (한 번에 모든 차트에 적용)
  function applyRebootMarkers(chart, events, gridMs) {
    if (!chart || !chart.options) return;
    chart.options.plugins = chart.options.plugins || {};
    chart.options.plugins.rebootMarkers = { events, gridMs };
    chart.update('none');
  }

  root.ChartUtils = {
    RANGE_LABEL, AUTO_BUCKET, BUCKET_LABEL, RANGE_MS, BUCKET_MS, COLORS,
    fmtKst, fmtLabel, fmtKbChart,
    getAnchorEnd, initAnchor,
    makeBucketGrid, joinToGrid,
    bindToggle, initSse, safeArray,
    fetchRebootEvents, applyRebootMarkers,
    buildAvgMaxDatasets, buildAvgMaxLegend,
  };
})(window);