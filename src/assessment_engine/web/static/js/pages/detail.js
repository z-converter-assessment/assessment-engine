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

  /* -------- 포맷/DOM 유틸 (개요는 도넛·포화·에러만 — breakdown 표 없음) -------- */
  /** @param {string} id */
  const show = (id) => /** @type {HTMLElement} */ (document.getElementById(id)).style.display = '';
  /** @param {string} id */
  const hide = (id) => /** @type {HTMLElement} */ (document.getElementById(id)).style.display = 'none';
  /** @param {string} id */
  const el    = (id) => /** @type {HTMLElement} */ (document.getElementById(id));

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

    /* 실시간 카드 — 순간 도넛 3개(CPU/메모리/디스크 이용률) + 디스크·네트워크 활동(I/O). 이용률·포화·에러
       분류는 순간이 아닌 14일 창이라 별도 '자원 이용률·포화·에러' 카드(SSR)로 분리. */

    /* 순간 이용률 도넛 — 마지막 스냅샷 delta 현재값. 디스크는 서버 집계(disk_usage_pct, P2). */
    /** @type {Partial<import('../generated/api').components['schemas']['CpuSnapshot']>} */
    const cpu = d.cpu || {};
    setDonut('cpu-donut-arc', 'cpu-donut-text', cpu.usage_pct);
    /** @type {Partial<import('../generated/api').components['schemas']['MemSnapshot']>} */
    const mem = d.memory || {};
    setDonut('mem-donut-arc', 'mem-donut-text', mem.usage_pct);
    setDonut('disk-donut-arc', 'disk-donut-text', d.disk_usage_pct);

    /* 활동 축 — 물리 디스크 어태치별 R/W(+IOPS)·물리 인터페이스별 RX/TX (서버가 이미 물리 필터). 실시간. */
    SignalUtils.renderActivity(el('disk-activity'), ChartUtils.safeArray(d.disk_io), 'disk');
    SignalUtils.renderActivity(el('net-activity'), ChartUtils.safeArray(d.net_io), 'net');
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
      if (window.ToastUtils) ToastUtils.show('ZDM IP | 관리자 계정 필수', 'err');
      else alert('ZDM IP | 관리자 계정 필수');
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
