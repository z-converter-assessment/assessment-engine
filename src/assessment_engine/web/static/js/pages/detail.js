// @ts-check
/* detail 페이지 — server 상세 latest metrics 표시 + 30초 polling 자동 갱신.
 *
 * body data-server-id 단일 진실 (#E6 inline <script> 금지).
 * 외부 의존: ChartUtils.fmtKst (F2 단일 KST 변환 경계).
 *
 * P4 의무 규약 적용 (해당 항목 a~d):
 *  (a) sequence counter — fetchMetrics 30초 polling in-flight 가능 → seq counter. collection-status 는 초기 1회(statusSeq).
 *  (b) capture-before-await — 본 페이지는 range/anchor 토글 없음. 단일 endpoint polling 이라 파라미터 stale 없음.
 *  (c) Array.isArray — collection-status·disk_io·net_io 모두 fallback safe.
 *  (d) 404 분기 — /metrics/latest 404 시 metrics-no-data 표시.
 */
(() => {
  const SERVER_ID = document.body.dataset.serverId;
  if (!SERVER_ID) { console.error('detail.js: body data-server-id missing'); return; }

  /* -------- 포맷 유틸 -------- */
  /** @param {number | null | undefined} v */
  const fmtPct  = (v) => v != null ? v.toFixed(1) + '%' : '—';
  /** @param {number | null | undefined} v */
  const fmtIops = (v) => v != null ? v.toFixed(1) + ' IOPS' : '—';
  // 처리량 동적 단위 (kB/s → MB/s) — storage/network·차트와 단위 표기 통일. 큰 값도 가독성 유지.
  /** @param {number | null | undefined} v */
  const fmtKbps = (v) => v == null ? '—' : (v >= 1024 ? (v / 1024).toFixed(1) + ' MB/s' : v.toFixed(1) + ' kB/s');
  /** @param {number | null | undefined} v */
  const fmtPps  = (v) => v != null ? v.toFixed(1) + ' pps' : '—';
  // 메모리 값은 bytes 입력 — 동적 단위 (GiB=bytes/1024^3, MiB=bytes/1024^2, KiB=bytes/1024).
  /** @param {number | null | undefined} bytes */
  function fmtKb(bytes) {
    if (bytes == null) return '—';
    if (bytes >= 1024 * 1024 * 1024) return (bytes / 1024 / 1024 / 1024).toFixed(1) + ' GB';
    if (bytes >= 1024 * 1024)        return (bytes / 1024 / 1024).toFixed(0) + ' MB';
    return (bytes / 1024).toFixed(0) + ' KB';
  }
  /** @param {string} id */
  const show = (id) => /** @type {HTMLElement} */ (document.getElementById(id)).style.display = '';
  /** @param {string} id */
  const hide = (id) => /** @type {HTMLElement} */ (document.getElementById(id)).style.display = 'none';
  /** @param {string} id */
  const el    = (id) => /** @type {HTMLElement} */ (document.getElementById(id));
  /** @param {string} id @param {string} v */
  const setTxt = (id, v) => { const e = el(id); if (e) e.textContent = v; };  // OS 전용 값은 템플릿에서 숨겨 요소 부재 -> null-safe

  /* 활용률 도넛 게이지 — 단색(임계색 아님, E8 일관). pct null = 빈 게이지 + 회색 '—'. P4 동적 SVG 산술. */
  const DONUT_CIRC = 263.89;  // 2*pi*42 (r=42)
  /** @param {string} arcId @param {string} textId @param {number | null | undefined} pct */
  function setDonut(arcId, textId, pct) {
    const arc = el(arcId), txt = el(textId);
    const unit = el(textId.replace('-text', '-unit'));  // 값 아래 작은 % 단위 (네트워크·디스크IO 도넛과 일관)
    if (pct == null) {
      arc.setAttribute('stroke-dasharray', '0 ' + DONUT_CIRC);
      txt.textContent = '—'; txt.setAttribute('fill', '#94a3b8'); txt.setAttribute('y', /** @type {any} */ (56));
      if (unit) unit.style.display = 'none';
      return;
    }
    const len = Math.max(0, Math.min(pct, 100)) / 100 * DONUT_CIRC;
    arc.setAttribute('stroke-dasharray', len.toFixed(2) + ' ' + DONUT_CIRC);
    txt.textContent = pct.toFixed(1); txt.setAttribute('fill', '#1e293b'); txt.setAttribute('y', /** @type {any} */ (50));
    if (unit) unit.style.display = '';
  }

  /* -------- 메트릭 렌더링 -------- */
  /** @param {import('../generated/api').components['schemas']['MetricDashboard']} d */
  function renderMetrics(d) {
    hide('metrics-loading');
    hide('metrics-no-data');
    show('metrics-content');

    if (d.collected_at) {
      // F2: KST 변환은 ChartUtils.fmtKst 단일 경계. 카드 밖 우측상단 stamp (환경 실시간과 동일 형식).
      el('metrics-stamp').textContent = '30초마다 자동 갱신 · 최근 ' + ChartUtils.fmtKst(d.collected_at);
    }

    /* CPU */
    /** @type {Partial<import('../generated/api').components['schemas']['CpuSnapshot']>} */
    const cpu = d.cpu || {};
    setTxt('cpu-usage',  fmtPct(cpu.usage_pct));
    setTxt('cpu-user',   fmtPct(cpu.user_pct));
    setTxt('cpu-system', fmtPct(cpu.system_pct));
    setTxt('cpu-iowait', fmtPct(cpu.iowait_pct));  // Linux 전용 (Windows 는 템플릿에서 열 숨김)
    setDonut('cpu-donut-arc', 'cpu-donut-text', cpu.usage_pct);

    /* 포화 스냅샷 신호 — 서버가 os-aware 판정(값·임계·saturated·4상태)을 끝낸 구조화 신호를 공통 렌더만(P4).
       JS os 분기·임계 재계산 없음(SignalUtils). 근거(metric·임계)는 각 항목 hover. */
    SignalUtils.renderSaturation(el('cpu-sat-signals'), d.cpu_saturation);
    SignalUtils.renderSaturation(el('mem-sat-signals'), d.mem_saturation);
    SignalUtils.renderSaturation(el('disk-sat-signals'), d.disk_saturation);
    SignalUtils.renderSaturation(el('net-sat-signals'), d.net_saturation);
    /* 에러 축 표시자 (카운트형, 정상=0 발화) — 서버 판정 완료, 렌더만. */
    SignalUtils.renderErrors(el('error-fleet'), d.errors);

    /* Memory */
    /** @type {Partial<import('../generated/api').components['schemas']['MemSnapshot']>} */
    const mem = d.memory || {};
    setTxt('mem-usage',   fmtPct(mem.usage_pct));
    setTxt('mem-used',    fmtKb(mem.used_bytes));
    setTxt('mem-avail',   fmtKb(mem.available_bytes));
    setTxt('mem-cached',  fmtKb(mem.cached_bytes));  // Linux 전용
    setTxt('mem-buffers', fmtKb(mem.buffered_bytes));  // Linux 전용 (wire: buffered_bytes)
    setDonut('mem-donut-arc', 'mem-donut-text', mem.usage_pct);

    /* Disk I/O (E9: 데이터 없어도 제목 노출 + placeholder) */
    const ioRow = (/** @type {import('../generated/api').components['schemas']['DiskIoSnapshot']} */ dk) => `
        <tr>
          <td>${dk.device}</td>
          <td>${fmtIops(dk.read_iops)}</td>
          <td>${fmtIops(dk.write_iops)}</td>
          <td>${fmtKbps(dk.read_kbps)}</td>
          <td>${fmtKbps(dk.write_kbps)}</td>
        </tr>`;
    const diskIo = ChartUtils.safeArray(d.disk_io);
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

    /* 디스크 활용률 도넛용 mounts — Filesystem 목록은 스토리지 상세 페이지로 분리(detail 미표시) */
    const mounts = ChartUtils.safeArray(d.mounts);
    if (diskIo.length > 0 || mounts.length > 0) show('storage-group');

    /* 디스크 활용률 도넛 — 전 mount 통합 풀(sum used / sum total), 환경 실시간 disk_pool_pct 와 동일 기준. */
    const fsRows = mounts.filter(m => m.total_gb && m.used_gb != null);
    const fsTotal = fsRows.reduce((s, m) => s + /** @type {number} */ (m.total_gb), 0);
    setDonut('disk-donut-arc', 'disk-donut-text', fsTotal > 0 ? fsRows.reduce((s, m) => s + /** @type {number} */ (m.used_gb), 0) / fsTotal * 100 : null);
  }

  /* -------- AJAX -------- */
  let metricsSeq = 0;
  async function fetchMetrics() {
    const seq = ++metricsSeq;  // 30초 polling 동시 in-flight 시 stale 응답 폐기 (P4 a)
    try {
      const res = await fetch(`/api/servers/${SERVER_ID}/metrics/latest`);
      if (seq !== metricsSeq) return;
      if (res.status === 404) {
        hide('metrics-loading');
        show('metrics-no-data');
        return;
      }
      if (!res.ok) return;
      /** @type {import('../generated/api').components['schemas']['MetricDashboard']} */
      const data = await res.json();
      if (seq !== metricsSeq) return;
      renderMetrics(data);
    } catch (e) { console.error('metrics fetch', e); }
  }

  let statusSeq = 0;
  async function fetchCollectionStatus() {
    const seq = ++statusSeq;
    try {
      const res = await fetch(`/api/servers/${SERVER_ID}/collection-status`);
      if (!res.ok) return;
      if (seq !== statusSeq) return;
      /** @type {import('../generated/api').components['schemas']['CollectionStatusItem'] | null} */
      const data = await res.json();
      const item = Array.isArray(data) ? data[0] : data;
      if (!item) return;
      const badge = el('online-badge');
      // 상태 표시는 dot 뱃지 아닌 폰트색 글자 — 대시보드 목록(.status-on/.status-off)과 통일 (static-assets.md).
      if (item.is_online) {
        badge.className = 'status-on no-print';
        badge.textContent = '온라인';
      } else {
        badge.className = 'status-off no-print';
        badge.textContent = '오프라인';
      }
    } catch (e) {}
  }

  /* -------- 초기 로드 + 30초 polling — 실시간 메트릭 카드만 자동 갱신 -------- */
  /* online-badge(수집 상태)는 실시간 메트릭 카드 밖이라 초기 1회만 — 카드의 "최근 시각"이 끊김 신호 겸함. */
  fetchMetrics();
  fetchCollectionStatus();
  ChartUtils.initAutoRefresh(fetchMetrics, 30_000);  // 탭 비활성 시 일시정지 (chart-utils 단일 진실)
})();

// 서버 1대 scope 보고서 발행 모달 — 대시보드 환경 보고서 모달과 동일 form (time_range select + anchor).
// /reports/servers 라우터가 time_range 그대로 받음 — JS 변환 없음.
(function () {
  const card = document.getElementById('server-report-card');
  if (!card) return;
  const modal = /** @type {HTMLElement} */ (document.getElementById('server-report-modal'));
  const customerOpenBtn = /** @type {HTMLElement} */ (document.getElementById('server-report-customer-open'));
  const engineerOpenBtn = /** @type {HTMLElement} */ (document.getElementById('server-report-engineer-open'));
  const closeBtn = /** @type {HTMLElement} */ (document.getElementById('server-report-close'));
  const submitBtn = /** @type {HTMLButtonElement} */ (document.getElementById('server-report-submit'));
  const titleEl = /** @type {HTMLElement} */ (document.getElementById('server-report-title'));
  const descEl = /** @type {HTMLElement} */ (document.getElementById('server-report-desc'));
  const rangeSel = /** @type {HTMLSelectElement} */ (document.getElementById('server-report-range'));
  const anchorInput = document.getElementById('server-report-anchor');
  const publicId = /** @type {string} */ (card.dataset.serverPublicId);
  const hostname = card.dataset.serverHostname;
  const _VIEW_TITLES = { customer: '고객 보고서 발행', engineer: '엔지니어 보고서 발행' };
  const _VIEW_DESCS = {
    customer: `서버 ${hostname} 1대 대상 자원 적정성 규칙 기반 고객 보고서 발행.`,
    engineer: `서버 ${hostname} 1대 대상 자원 적정성 규칙 기반 엔지니어 보고서 발행.`,
  };
  let currentView = 'customer';

  /** @param {'customer' | 'engineer'} view */
  function open(view) {
    currentView = view;
    titleEl.textContent = _VIEW_TITLES[view];
    descEl.textContent = _VIEW_DESCS[view];
    submitBtn.disabled = false;  // 매번 재활성화 — 직전 발행(PRG navigate) 후 bfcache 복귀 시 disabled 잔존 우회
    modal.style.display = 'flex';
  }
  function close() { modal.style.display = 'none'; }

  // PRG — POST emit(record) → view_url GET navigate. 공용 EmitUtils(비활성·토스트·bfcache 복구 내장).
  // 다시 보기 / 북마크 / 직접 URL 은 GET 만 → record 안 됨 → 중복 방지.
  function publish() {
    /** @type {any} */ (window.EmitUtils).submitNavigate(submitBtn, () => {
      const params = new URLSearchParams();
      params.set('ids', publicId);
      params.set('view', currentView);
      params.set('time_range', rangeSel.value);
      return `/reports/servers/emit?${params.toString()}`;
    }, {
      pendingMsg: '보고서 발행 중...',
      errPrefix: '보고서 발행 실패',
      viewUrlTransform: (/** @type {string} */ u) => u + `&back=${encodeURIComponent(location.pathname)}`,
      onRestore: close, // bfcache 복귀 시 열린 모달도 닫음
    });
  }

  // 페이지 로드 시 anchor input 기본값 채움 (대시보드 모달과 일관 UX).
  if (anchorInput && window.ChartUtils && /** @type {any} */ (window.ChartUtils).initAnchor) {
    /** @type {any} */ (window.ChartUtils).initAnchor('server-report-anchor');
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
  const modal = /** @type {HTMLElement} */ (document.getElementById('server-install-modal'));
  const openBtn = /** @type {HTMLElement} */ (document.getElementById('server-install-open'));
  const closeBtn = /** @type {HTMLElement} */ (document.getElementById('server-install-close'));
  const submitBtn = /** @type {HTMLButtonElement} */ (document.getElementById('server-install-submit'));
  const zdmIpEl = /** @type {HTMLInputElement} */ (document.getElementById('server-install-zdm-target'));
  const zdmUserEl = /** @type {HTMLInputElement} */ (document.getElementById('server-install-zdm-account'));
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
      /** @type {import('../generated/api').components['schemas']['TaskCreated'][]} */
      const data = await res.json();
      const t0 = Array.isArray(data) && data[0] ? data[0] : null;
      const tid = t0 ? t0.task_id : '';
      if (window.ToastUtils) {
        // 오프라인이면 advisory(warn) — 발행은 됐고 큐에 적재됨(재접속 시 배달, 창 넘기면 만료).
        if (t0 && t0.target_online === false) {
          ToastUtils.show(`Install 큐 적재 — ${hostname} 오프라인. 재접속 시 배달(미접속 시 만료) · task ${tid.slice(0, 8)}`, 'warn');
        } else {
          ToastUtils.show(`Install 발행 완료 — task ${tid.slice(0, 8)}`, 'ok');
        }
      }
      closeModal();
      // 페이지 새로고침으로 최근 작업 row 갱신.
      setTimeout(() => location.reload(), 600);
    } catch (e) {
      if (pending) pending.remove();
      if (window.ToastUtils) ToastUtils.show(`Install 발행 오류: ${/** @type {Error} */ (e).message}`, 'err');
    } finally {
      submitBtn.disabled = false;
    }
  }

  openBtn.addEventListener('click', openModal);
  closeBtn.addEventListener('click', closeModal);
  submitBtn.addEventListener('click', submit);
  modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });
})();
