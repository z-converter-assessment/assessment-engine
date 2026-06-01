/* detail 페이지 — server 상세 latest metrics 표시 + SSE 자동 갱신.
 *
 * body data-server-id 단일 진실 (#E6 inline <script> 금지).
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
  const SERVER_ID = document.body.dataset.serverId;
  if (!SERVER_ID) { console.error('detail.js: body data-server-id missing'); return; }
  const OS_FAMILY = document.body.dataset.osFamily || '';  // Windows 미측정 메트릭 N/A 분기

  /* -------- 표시 임계값 — backend mappers._USAGE_*_PCT 단일 진실, body data-attribute 로 주입 (#E1 P4). */
  const USAGE_DANGER_PCT = parseFloat(document.body.dataset.usageDangerPct) || 90;
  const USAGE_WARN_PCT   = parseFloat(document.body.dataset.usageWarnPct)   || 75;
  const COLOR_OK     = '#3b82f6';
  const COLOR_WARN   = '#f59e0b';
  const COLOR_DANGER = '#ef4444';

  /* -------- 포맷 유틸 -------- */
  const fmtPct  = (v) => v != null ? v.toFixed(1) + '%' : '—';
  const fmtLoad = (v) => v != null ? v.toFixed(2) : '—';
  const fmtIops = (v) => v != null ? v.toFixed(1) + ' IOPS' : '—';
  const fmtKbps = (v) => v != null ? v.toFixed(1) + ' kBps' : '—';
  // swap 은 used 가 작아도 GB 소숫점1 고정 — KB/MB 자동 단위(fmtKb)는 비현실적이라 단위 통일.
  const fmtGb   = (kb) => kb != null ? (kb / 1024 / 1024).toFixed(1) + ' GB' : '—';
  const fmtPps  = (v) => v != null ? v.toFixed(1) + ' pps' : '—';
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
      el('metrics-ts').textContent = '수집 기준: ' + ChartUtils.fmtKst(d.collected_at);
    }

    /* CPU */
    const cpu = d.cpu || {};
    setTxt('cpu-usage',  fmtPct(cpu.usage_pct));
    setTxt('cpu-user',   fmtPct(cpu.user_pct));
    setTxt('cpu-system', fmtPct(cpu.system_pct));
    setTxt('cpu-iowait', ChartUtils.naWindows(OS_FAMILY, 'cpu_iowait', fmtPct(cpu.iowait_pct)));
    el('cpu-bar').style.width = (cpu.usage_pct ?? 0) + '%';
    el('cpu-bar').style.background = barColor(cpu.usage_pct);

    /* Load */
    setTxt('load-1m',  ChartUtils.naWindows(OS_FAMILY, 'load_1m', fmtLoad(d.load_1m)));
    setTxt('load-5m',  ChartUtils.naWindows(OS_FAMILY, 'load_5m', fmtLoad(d.load_5m)));
    setTxt('load-15m', ChartUtils.naWindows(OS_FAMILY, 'load_15m', fmtLoad(d.load_15m)));

    /* Memory */
    const mem = d.memory || {};
    setTxt('mem-usage',   fmtPct(mem.usage_pct));
    setTxt('mem-used',    fmtKb(mem.used_kb));
    setTxt('mem-avail',   fmtKb(mem.available_kb));
    setTxt('mem-cached',  ChartUtils.naWindows(OS_FAMILY, 'mem_cached', fmtKb(mem.cached_kb)));
    setTxt('mem-buffers', ChartUtils.naWindows(OS_FAMILY, 'mem_buffers', fmtKb(mem.buffers_kb)));
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
      setTxt('swap-used',  fmtGb(swap.used_kb));
      setTxt('swap-total', fmtGb(swap.total_kb));
      el('swap-bar').style.width = (swap.usage_pct ?? 0) + '%';
      el('swap-bar').style.background = barColor(swap.usage_pct);
    }

    /* Disk I/O — 물리 / 논리·가상 (E9: 데이터 없어도 제목 노출 + placeholder) */
    const ioRow = dk => `
        <tr>
          <td>${dk.device}</td>
          <td>${fmtIops(dk.read_iops)}</td>
          <td>${fmtIops(dk.write_iops)}</td>
          <td>${fmtKbps(dk.read_kbps)}</td>
          <td>${fmtKbps(dk.write_kbps)}</td>
        </tr>`;
    const diskIo = ChartUtils.safeArray(d.disk_io_phys);
    if (diskIo.length > 0) {
      el('disk-io-body').innerHTML = diskIo.map(ioRow).join('');
      show('disk-io-table'); hide('disk-io-empty');
    } else {
      hide('disk-io-table'); show('disk-io-empty');
    }

    /* Network I/O */
    const netIo = ChartUtils.safeArray(d.net_io);
    if (netIo.length > 0) {
      show('net-io-section');
      el('net-io-body').innerHTML = netIo.map(n => `
        <tr>
          <td>${n.interface}</td>
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
              <span style="font-weight:500; font-size:13px;">${m.mount}</span>
              <span class="text-muted">${label}</span>
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
      const res = await fetch(`/api/servers/${SERVER_ID}/metrics/latest`);
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
      const res = await fetch(`/api/servers/${SERVER_ID}/collection-status`);
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
  const es = new EventSource(`/api/servers/${SERVER_ID}/metrics/stream`);
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

// 서버 1대 scope 보고서 발행 모달 — 대시보드 환경 보고서 모달과 동일 form (time_range select + anchor).
// /servers/report 라우터가 time_range 그대로 받음 — JS 변환 없음.
(function () {
  const card = document.getElementById('server-report-card');
  if (!card) return;
  const modal = document.getElementById('server-report-modal');
  const customerOpenBtn = document.getElementById('server-report-customer-open');
  const engineerOpenBtn = document.getElementById('server-report-engineer-open');
  const closeBtn = document.getElementById('server-report-close');
  const submitBtn = document.getElementById('server-report-submit');
  const titleEl = document.getElementById('server-report-title');
  const descEl = document.getElementById('server-report-desc');
  const rangeSel = document.getElementById('server-report-range');
  const anchorInput = document.getElementById('server-report-anchor');
  const publicId = card.dataset.serverPublicId;
  const hostname = card.dataset.serverHostname;
  const _VIEW_TITLES = { customer: '고객 보고서 발행', engineer: '엔지니어 보고서 발행' };
  const _VIEW_DESCS = {
    customer: `서버 ${hostname} 1대 대상 Right-sizing 규칙 기반 고객 보고서 발행. 새 탭으로 이동합니다.`,
    engineer: `서버 ${hostname} 1대 대상 Right-sizing 규칙 기반 엔지니어 보고서 발행. 새 탭으로 이동합니다.`,
  };
  let currentView = 'customer';

  function open(view) {
    currentView = view;
    titleEl.textContent = _VIEW_TITLES[view];
    descEl.textContent = _VIEW_DESCS[view];
    modal.style.display = 'flex';
  }
  function close() { modal.style.display = 'none'; }

  async function publish() {
    // PRG pattern — POST emit (record) → 응답 view_url 로 GET navigate (read-only 표시).
    // 다시 보기 / 북마크 / 직접 URL 은 GET 만 호출 → record 안 됨 → 중복 방지.
    submitBtn.disabled = true;
    try {
      const params = new URLSearchParams();
      params.set('ids', publicId);
      params.set('view', currentView);
      params.set('time_range', rangeSel.value);
      const res = await fetch(`/servers/report/emit?${params.toString()}`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const viewUrl = data.view_url + `&back=${encodeURIComponent(location.pathname)}`;
      window.location.href = viewUrl;
    } catch (e) {
      if (window.ToastUtils) ToastUtils.show('보고서 발행 실패: ' + e.message, 'err');
      submitBtn.disabled = false;
    }
    close();
  }

  // 페이지 로드 시 anchor input 기본값 채움 (대시보드 모달과 일관 UX).
  if (anchorInput && window.ChartUtils && window.ChartUtils.initAnchor) {
    window.ChartUtils.initAnchor('server-report-anchor');
  }

  customerOpenBtn.addEventListener('click', () => open('customer'));
  engineerOpenBtn.addEventListener('click', () => open('engineer'));
  closeBtn.addEventListener('click', close);
  submitBtn.addEventListener('click', publish);
  modal.addEventListener('click', e => { if (e.target === modal) close(); });
})();

// 서버 1대 scope ZConverter Install 발행 모달 — 대시보드 #install-modal 과 동일 form.
// 발행 시 POST /api/tasks/install body={target_public_ids:[<public_id>], zdm_ip, zdm_user}
(function () {
  const card = document.getElementById('server-install-card');
  if (!card) return;
  const modal = document.getElementById('server-install-modal');
  const openBtn = document.getElementById('server-install-open');
  const closeBtn = document.getElementById('server-install-close');
  const submitBtn = document.getElementById('server-install-submit');
  const zdmIpEl = document.getElementById('server-install-zdm-target');
  const zdmUserEl = document.getElementById('server-install-zdm-account');
  const publicId = card.dataset.serverPublicId;
  const hostname = card.dataset.serverHostname;

  function openModal() {
    // 매번 defaultValue 강제 복원 — autocomplete/cache/이전 입력 잔존 우회.
    if (zdmIpEl && zdmIpEl.defaultValue) zdmIpEl.value = zdmIpEl.defaultValue;
    if (zdmUserEl && zdmUserEl.defaultValue) zdmUserEl.value = zdmUserEl.defaultValue;
    modal.style.display = 'flex';
  }
  function closeModal() { modal.style.display = 'none'; }

  async function submit() {
    const zdmIp = zdmIpEl.value.trim();
    const zdmUser = zdmUserEl.value.trim();
    if (!zdmIp || !zdmUser) {
      if (window.ToastUtils) ToastUtils.show('ZDM IP / 관리자 계정 필수', 'err');
      else alert('ZDM IP / 관리자 계정 필수');
      return;
    }
    submitBtn.disabled = true;
    const pending = window.ToastUtils ? ToastUtils.show(`Install 발행 중 (${hostname})...`, 'pending') : null;
    try {
      const res = await fetch('/api/tasks/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_public_ids: [publicId],
          zdm_ip: zdmIp,
          zdm_user: zdmUser,
        }),
      });
      if (pending) pending.remove();
      if (!res.ok) {
        const detail = await res.text();
        if (window.ToastUtils) ToastUtils.show(`Install 발행 실패 (HTTP ${res.status}): ${detail}`, 'err');
        else alert(`Install 발행 실패 (HTTP ${res.status}): ${detail}`);
        return;
      }
      const data = await res.json();
      const tid = Array.isArray(data) && data[0] ? data[0].task_id : '';
      if (window.ToastUtils) {
        ToastUtils.show(`Install 발행 완료 — task ${tid.slice(0, 8)}`, 'ok');
      }
      closeModal();
      // 페이지 새로고침으로 최근 작업 row 갱신.
      setTimeout(() => location.reload(), 600);
    } catch (e) {
      if (pending) pending.remove();
      if (window.ToastUtils) ToastUtils.show(`Install 발행 오류: ${e.message}`, 'err');
    } finally {
      submitBtn.disabled = false;
    }
  }

  openBtn.addEventListener('click', openModal);
  closeBtn.addEventListener('click', closeModal);
  submitBtn.addEventListener('click', submit);
  modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });
})();
