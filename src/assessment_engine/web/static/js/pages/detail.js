/* detail 페이지 — server 상세 latest metrics 표시 + SSE 자동 갱신.
 *
 * Jinja2 변수: window.SERVER_ID (페이지 .html에서 inline `<script>`로 정의 후 본 파일 defer 로드).
 * 외부 의존: ChartUtils.fmtKst (F2 단일 KST 변환 경계).
 *
 * P4 5 의무 규약(a~e) 적용:
 *  (a) sequence counter — fetchMetrics는 SSE 트리거 또는 초기 1회만이라 race 없지만 collection-status는 30초 polling으로 동시 in-flight 가능 → seq counter.
 *  (b) capture-before-await — 본 페이지는 range/anchor 토글 없음. SSE는 단방향이라 stale 없음.
 *  (c) Array.isArray — collection-status·disk_io_phys·net_io 모두 fallback safe.
 *  (d) 404 분기 — /metrics/latest 404 시 metrics-no-data 표시.
 *  (e) 명명 상수 — USAGE_DANGER_PCT/USAGE_WARN_PCT + COLOR_* 모듈 상단.
 */
(() => {
  const SERVER_ID = window.SERVER_ID;
  if (!SERVER_ID) { console.error('detail.js: window.SERVER_ID missing'); return; }

  /* -------- 표시 임계값 (서버 mappers._usage_bar_color와 동일 기준) -------- */
  const USAGE_DANGER_PCT = 90;
  const USAGE_WARN_PCT   = 75;
  const COLOR_OK     = '#3b82f6';
  const COLOR_WARN   = '#f59e0b';
  const COLOR_DANGER = '#ef4444';

  /* -------- 포맷 유틸 -------- */
  const fmtPct  = (v) => v != null ? v.toFixed(1) + '%' : '—';
  const fmtLoad = (v) => v != null ? v.toFixed(2) : '—';
  const fmtIops = (v) => v != null ? v.toFixed(1) + ' IOPS' : '—';
  const fmtKbps = (v) => v != null ? v.toFixed(1) + ' kBps' : '—';
  const fmtPps  = (v) => v != null ? Math.round(v) + ' pps' : '—';
  function fmtKb(kb) {
    if (kb == null) return '—';
    if (kb >= 1024 * 1024) return (kb / 1024 / 1024).toFixed(1) + ' GB';
    if (kb >= 1024)        return (kb / 1024).toFixed(0) + ' MB';
    return kb + ' KB';
  }
  function barColor(pct) {
    if (pct == null)              return COLOR_OK;
    if (pct >= USAGE_DANGER_PCT)  return COLOR_DANGER;
    if (pct >= USAGE_WARN_PCT)    return COLOR_WARN;
    return COLOR_OK;
  }
  const show = (id) => document.getElementById(id).style.display = '';
  const hide = (id) => document.getElementById(id).style.display = 'none';
  const el    = (id) => document.getElementById(id);
  const setTxt = (id, v) => el(id).textContent = v;

  /* -------- 메트릭 렌더링 -------- */
  function renderMetrics(d) {
    hide('metrics-loading');
    hide('metrics-no-data');
    show('metrics-content');

    if (d.collected_at) {
      // F2: KST 변환은 ChartUtils.fmtKst 단일 경계
      el('metrics-ts').textContent = '수집: ' + ChartUtils.fmtKst(d.collected_at);
    }

    /* CPU */
    const cpu = d.cpu || {};
    setTxt('cpu-usage',  fmtPct(cpu.usage_pct));
    setTxt('cpu-user',   fmtPct(cpu.user_pct));
    setTxt('cpu-system', fmtPct(cpu.system_pct));
    setTxt('cpu-iowait', fmtPct(cpu.iowait_pct));
    el('cpu-bar').style.width = (cpu.usage_pct ?? 0) + '%';
    el('cpu-bar').style.background = barColor(cpu.usage_pct);

    /* Load */
    setTxt('load-1m',  fmtLoad(d.load_1m));
    setTxt('load-5m',  fmtLoad(d.load_5m));
    setTxt('load-15m', fmtLoad(d.load_15m));

    /* Memory */
    const mem = d.memory || {};
    setTxt('mem-usage',   fmtPct(mem.usage_pct));
    setTxt('mem-used',    fmtKb(mem.used_kb));
    setTxt('mem-avail',   fmtKb(mem.available_kb));
    setTxt('mem-cached',  fmtKb(mem.cached_kb));
    setTxt('mem-buffers', fmtKb(mem.buffers_kb));
    // P5: 누적 비율은 서버 metrics_calculator.compute_mem 에서 계산. 클라이언트는 표시만.
    el('mem-used-bar').style.width      = (mem.usage_pct ?? 0) + '%';
    el('mem-used-bar').style.background = barColor(mem.usage_pct);
    el('mem-cached-bar').style.width    = (mem.cached_pct ?? 0) + '%';
    el('mem-buf-bar').style.width       = (mem.buffers_pct ?? 0) + '%';

    /* Swap */
    const swap = d.swap || {};
    if (swap.total_kb) {
      show('swap-section');
      setTxt('swap-usage', fmtPct(swap.usage_pct));
      setTxt('swap-used',  fmtKb(swap.used_kb));
      setTxt('swap-total', fmtKb(swap.total_kb));
      el('swap-bar').style.width = (swap.usage_pct ?? 0) + '%';
      el('swap-bar').style.background = barColor(swap.usage_pct);
    }

    /* Disk I/O */
    const diskIo = ChartUtils.safeArray(d.disk_io_phys);
    if (diskIo.length > 0) {
      el('disk-io-body').innerHTML = diskIo.map(dk => `
        <tr>
          <td><code>${dk.device}</code></td>
          <td>${fmtIops(dk.read_iops)}</td>
          <td>${fmtIops(dk.write_iops)}</td>
          <td>${fmtKbps(dk.read_kbps)}</td>
          <td>${fmtKbps(dk.write_kbps)}</td>
        </tr>`).join('');
    }

    /* Network I/O */
    const netIo = ChartUtils.safeArray(d.net_io);
    if (netIo.length > 0) {
      show('net-io-section');
      el('net-io-body').innerHTML = netIo.map(n => `
        <tr>
          <td><code>${n.interface}</code></td>
          <td>${fmtKbps(n.rx_kbps)}</td>
          <td>${fmtKbps(n.tx_kbps)}</td>
          <td>${fmtPps(n.rx_pps)}</td>
          <td>${fmtPps(n.tx_pps)}</td>
        </tr>`).join('');
    }

    /* Filesystem */
    const mounts = ChartUtils.safeArray(d.mounts);
    if (diskIo.length > 0 || mounts.length > 0) show('storage-group');
    if (mounts.length > 0) {
      el('fs-body').innerHTML = mounts.map(m => {
        const pct   = m.usage_pct ?? 0;
        const color = barColor(m.usage_pct);
        const label = m.usage_pct != null
          ? `${m.used_gb?.toFixed(2) ?? '?'} / ${m.total_gb?.toFixed(2) ?? '?'} GB (${m.usage_pct.toFixed(1)}%)`
          : '데이터 없음';
        return `
          <div style="margin-bottom:14px;">
            <div style="display:flex; justify-content:space-between; margin-bottom:5px;">
              <span style="font-weight:500; font-size:13px;"><code>${m.mount}</code></span>
              <span style="font-size:12px; color:#64748b;">${label}</span>
            </div>
            <div class="progress-bar">
              <div class="progress-fill" style="width:${pct}%; background:${color};"></div>
            </div>
          </div>`;
      }).join('');
    }
  }

  /* -------- AJAX -------- */
  async function fetchMetrics() {
    try {
      const res = await fetch(`/api/v1/servers/${SERVER_ID}/metrics/latest`);
      if (res.status === 404) {
        hide('metrics-loading');
        show('metrics-no-data');
        return;
      }
      if (!res.ok) return;
      renderMetrics(await res.json());
    } catch (e) { console.error('metrics fetch', e); }
  }

  let statusSeq = 0;
  async function fetchCollectionStatus() {
    const seq = ++statusSeq;
    try {
      const res = await fetch(`/api/v1/servers/${SERVER_ID}/collection-status`);
      if (!res.ok) return;
      if (seq !== statusSeq) return;
      const data = await res.json();
      const item = Array.isArray(data) ? data[0] : data;
      if (!item) return;
      const badge = el('online-badge');
      if (item.is_online) {
        badge.innerHTML = '<span class="dot dot-ok"></span>온라인';
        badge.className = 'badge badge-ok';
      } else {
        badge.innerHTML = '<span class="dot dot-off"></span>오프라인';
        badge.className = 'badge';
      }
    } catch (e) {}
  }

  /* -------- SSE -------- */
  const es = new EventSource(`/api/v1/servers/${SERVER_ID}/metrics/stream`);
  es.onopen = () => {
    el('sse-dot').className = 'dot dot-ok';
    el('sse-label').textContent = '자동 갱신 중';
  };
  es.onmessage = () => fetchMetrics();
  es.onerror = () => {
    el('sse-dot').className = 'dot dot-off';
    el('sse-label').textContent = '자동 갱신 중단 — 재연결 중...';
  };

  /* -------- 초기 로드 -------- */
  fetchMetrics();
  fetchCollectionStatus();
  setInterval(fetchCollectionStatus, 30_000);
})();
