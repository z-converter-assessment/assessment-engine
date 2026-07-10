// 차트 템플릿 공통 유틸. 모든 차트 페이지가 import.
// CLAUDE.md #E1 P4 의무 규약(sequence counter, capture-before-await,
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
  // 테마색1 (base.html :root --color-title) — JS 차트 시리즈가 CSS 변수를 추종.
  // getComputedStyle 실패·빈 값 시 #3b82f6 fallback (현행과 동일 색 — 회귀 0 보장).
  function themeColor() {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue('--color-title').trim();
      return v || '#3b82f6';
    } catch (e) { return '#3b82f6'; }
  }
  const COLORS = [themeColor(),'#f59e0b','#22c55e','#ef4444','#8b5cf6','#06b6d4','#f97316','#ec4899'];

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

  // ── 다중 dimension avg-only 라인 dataset 빌드 ──
  // cpu 분류·실행 큐·메모리 구성·종합 추이가 공유. rows: [{collected_at, value, dimension}].
  // metaMap: { dim: {label, color} } — 미정의 dim 은 dim 이름·기본색(#8b5cf6).
  // opts.valueFn: per-point 값 변환(기본 항등). opts.pointRadius: 0(추이)·1(분류).
  function buildDimDatasets(rows, bMs, grid, metaMap = {}, opts = {}) {
    const valueFn = opts.valueFn || (v => v);
    const pointRadius = opts.pointRadius ?? 0;
    const byDim = {};
    for (const r of rows) { (byDim[r.dimension] = byDim[r.dimension] || []).push(r); }
    return Object.entries(byDim).map(([dim, pts]) => {
      const map = {};
      for (const p of pts) { map[Math.floor(new Date(p.collected_at).getTime() / bMs) * bMs] = valueFn(p.value); }
      const meta  = metaMap[dim] || { label: dim, color: '#8b5cf6' };
      const color = meta.color || '#8b5cf6';
      return {
        label: meta.label || dim,
        data: grid.map(t => map[t] ?? null),
        borderColor: color, backgroundColor: color + '22',
        borderWidth: 2, pointRadius, pointHoverRadius: 3,
        tension: 0.3, fill: false, spanGaps: false,
      };
    });
  }

  // ── 처리량 동적 단위 포매터 (kB/s → MB/s) ──
  // 종합·환경 성능 추이(metrics·environment-metrics) Y축 단위 포매터 (B/s 기준 fmtKbChart 와 구분 — 이쪽은 kB 입력).
  // 단위 표기 "kB/s"/"MB/s" 통일 (fmtKbChart·format_net_rate 와 동일 관습).
  function fmtThroughput(kb) {
    if (kb == null) return '—';
    return kb >= 1024 ? (kb / 1024).toFixed(1) + ' MB/s' : kb.toFixed(1) + ' kB/s';
  }

  // ── 토글 그룹 바인딩 ──
  // groupId 가 <select> 면 change 로, .toggle 버튼 그룹이면 click 으로 자동 분기.
  // 호출처는 (groupId, onChange(val)) 동일 — HTML 만 select/button 선택.
  function bindToggle(groupId, onChange) {
    const el = document.getElementById(groupId);
    if (!el) return;
    if (el.tagName === 'SELECT') {
      el.addEventListener('change', e => onChange(e.target.value));
      return;
    }
    el.addEventListener('click', e => {
      const btn = e.target.closest('.toggle');
      if (!btn) return;
      el.querySelectorAll('.toggle').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      onChange(btn.dataset.val);
    });
  }

  // ── 30초 polling 자동 갱신 (detail 실시간 메트릭과 일관) ──
  // 탭 비활성(document.hidden) 시 tick skip — 다중 탭에서 누적 폴링 요청 차단(서버 부하 감소).
  // 숨겨졌다 다시 보이면 즉시 1회 refresh 해 멈춰있던 화면 보정 (loader 의 seq 가드가 중복 응답 흡수).
  // 폴링은 연결 상태 개념이 없어 상태 DOM 갱신 없음. stamp 는 호출처 loader 가 갱신.
  function initAutoRefresh(onRefresh, intervalMs = 30_000) {
    const id = setInterval(() => { if (!document.hidden) onRefresh(); }, intervalMs);
    const onVisible = () => { if (!document.hidden) onRefresh(); };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('pagehide', () => {                    // 좀비 타이머 방지
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVisible);
    });
    return id;
  }

  // ── 응답 안전 변환 ──
  function safeArray(arr) { return Array.isArray(arr) ? arr : []; }

  // ── Windows 미측정 메트릭 N/A (표시 경계) ──
  // Windows 는 cpu iowait/steal·mem buffers/cached 를 측정하지 않아 payload 에서 null 로 온다(구 에이전트는 0).
  // 값이 아니라 os_family==='windows' + 본 키로 판정해 'N/A' 표시 — null·0 어느 쪽이든 "측정값 0"과 구분. 부재 메트릭 카탈로그 단일 진실(JS).
  const WIN_NA_KEYS = new Set(['cpu_iowait', 'cpu_steal', 'mem_buffers', 'mem_cached']);
  function naWindows(osFamily, key, formatted) {
    return osFamily === 'windows' && WIN_NA_KEYS.has(key) ? 'N/A' : formatted;
  }

  // ── avg+max ghost dataset 빌드 (P4 패턴) ──
  // avgRows·maxRows: [{collected_at, value, dimension?}]
  // opts: { label?, color?, dashFn?(dim), pointRadius? }
  // single-dim(라벨 1개) 또는 multi-dim(dim별 색·dash) 통합.
  // 결과: [avgDataset, maxGhostDataset]쌍 N개. tooltip filter `datasetIndex % 2 === 0`로 max ghost 숨김.
  function buildAvgMaxDatasets(avgRows, maxRows, bMs, grid, opts = {}) {
    // 버킷이 최소 단위(1분)면 버킷당 데이터가 1포인트라 max=avg → 음영 무의미.
    // 15분 구간(1분 버킷)에서 max ghost 비활성화 (음영·tooltip max 제거). environment 단일선과 동일하게 max=[] 처리.
    if (bMs <= BUCKET_MS['1m']) maxRows = [];
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
  // opts: { codeLabel?: code 태그 사용, withToggle?: 칩(pill) 토글 — avg/max 짝 함께 hide (P4 허용 — E1 P4 절) }
  function buildAvgMaxLegend(containerId, chart, opts = {}) {
    const el = document.getElementById(containerId);
    if (!el || !chart) { if (el) el.innerHTML = ''; return; }
    const avgDatasets = chart.data.datasets.filter((_, i) => i % 2 === 0);
    // withToggle: cpu/memory 와 동일한 칩(pill) 토글 형식. avg/max 쌍을 1칩으로 묶어 함께 show/hide.
    if (opts.withToggle) {
      el.innerHTML = avgDatasets.map((ds, i) => `
        <button type="button" class="legend-chip" data-avg="${i * 2}" aria-pressed="true">
          <span class="legend-dot" style="background:${ds.borderColor};"></span>${ds.label}
        </button>
      `).join('');
      el.querySelectorAll('.legend-chip').forEach(chip => {
        chip.addEventListener('click', () => {
          const avgIdx = +chip.dataset.avg;
          const hidden = !chart.getDatasetMeta(avgIdx).hidden;
          chart.getDatasetMeta(avgIdx).hidden     = hidden;
          chart.getDatasetMeta(avgIdx + 1).hidden = hidden;
          chip.setAttribute('aria-pressed', String(!hidden));
          chart.update();
        });
      });
      return;
    }
    // 정적 범례 (performance codeLabel 등) — 선 + 라벨.
    el.innerHTML = avgDatasets.map(ds => {
      const isDash = ds.borderDash && ds.borderDash.length > 0;
      const lineHtml = isDash
        ? `<svg width="20" height="3" style="flex-shrink:0;"><line x1="0" y1="1.5" x2="20" y2="1.5" stroke="${ds.borderColor}" stroke-width="2" stroke-dasharray="4 2"/></svg>`
        : `<span class="legend-line" style="width:20px; height:3px; border-radius:2px; background:${ds.borderColor}; flex-shrink:0;"></span>`;
      const labelHtml = opts.codeLabel
        ? `<code>${ds.label}</code>`
        : `<span class="text-label">${ds.label}</span>`;
      return `<span style="display:flex; align-items:center; gap:5px;">${lineHtml}${labelHtml}</span>`;
    }).join('');
  }

  // 색 점 + 라벨 칩(pill) 토글 범례 — 클릭 시 dataset show/hide. 모든 차트 페이지 공용.
  // 숨김 상태는 aria-pressed=false (CSS 가 흐리게). label/checkbox 대신 button 이라 키보드 토글 자연 지원.
  function renderChipLegend(container, chart) {
    if (!chart) { container.innerHTML = ''; return; }
    container.innerHTML = chart.data.datasets.map((ds, i) => `
      <button type="button" class="legend-chip" data-idx="${i}" aria-pressed="true">
        <span class="legend-dot" style="background:${ds.borderColor};"></span>${ds.label}
      </button>
    `).join('');
    container.querySelectorAll('.legend-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const meta = chart.getDatasetMeta(+chip.dataset.idx);
        meta.hidden = !meta.hidden;
        chip.setAttribute('aria-pressed', String(!meta.hidden));
        chart.update();
      });
    });
  }

  root.ChartUtils = {
    RANGE_LABEL, AUTO_BUCKET, BUCKET_LABEL, RANGE_MS, BUCKET_MS, COLORS, themeColor,
    fmtKst, fmtLabel, fmtKbChart,
    getAnchorEnd, initAnchor,
    makeBucketGrid, joinToGrid, buildDimDatasets, fmtThroughput,
    bindToggle, initAutoRefresh, safeArray, naWindows,
    buildAvgMaxDatasets, buildAvgMaxLegend,
    renderChipLegend,
  };
})(window);