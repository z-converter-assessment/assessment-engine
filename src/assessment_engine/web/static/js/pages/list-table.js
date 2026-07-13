// @ts-check
/**
 * 서버 목록 페이지(list_table.html) — ZConverter Install·선택 N대 액션(보고서·Export·실시간/성능추이)·검색 필터.
 *
 * 외부 의존: 없음 (모달은 list_table.html에 inline markup).
 */

/** @typedef {import('../generated/api').components['schemas']['TaskDetailItem']} TaskDetailItem */

// Install 모달 Escape 닫기 (installModal·hideInstallModal 은 아래 Install 섹션에서 정의 — 콜백 실행 시점엔 바인딩됨).
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && installModal.style.display === 'flex') hideInstallModal();
});


// ─── ZConverter Install ────────────────────────────────────────────────────
// 체크박스로 호스트 선택 -> POST /api/tasks/install.
// engine 은 DB INSERT + agent.tasks.<agent_id> 큐 동적 declare + task.install publish.
// download.url 은 운영자 입력 ZDM host + ZDM_PACKAGE_PATH 조립, sha256/size_bytes 는 ZDM_PACKAGE_* env.

const installModal            = /** @type {HTMLElement} */ (document.getElementById('install-modal'));
const installBtn              = /** @type {HTMLButtonElement} */ (document.getElementById('install-btn'));
const installCloseBtn         = /** @type {HTMLElement} */ (document.getElementById('install-close'));
const installSubmitBtn        = /** @type {HTMLButtonElement} */ (document.getElementById('install-submit'));
const installCountEl          = /** @type {HTMLElement} */ (document.getElementById('install-count'));
const selectAllCb      = /** @type {HTMLInputElement | null} */ (document.getElementById('select-all'));

function selectedRows() {
  return /** @type {HTMLElement[]} */ ([...document.querySelectorAll('.row-select:checked')]);
}

// selection 버튼 상태 갱신 — 선택 N대면 액션 버튼 enabled.
// CSS .btn-primary:disabled 가 시각 분기 (opacity inline 불필요).
const selectionCountEl = /** @type {HTMLElement | null} */ (document.getElementById('selection-count'));
function refreshInstallButton() {
  const n = selectedRows().length;
  installBtn.disabled = n === 0;
  exportBtn.disabled = n === 0;
  reportCustomerBtn.disabled = n === 0;
  reportEngineerBtn.disabled = n === 0;
  if (realtimeSelBtn) realtimeSelBtn.disabled = n === 0;
  if (metricsSelBtn) metricsSelBtn.disabled = n === 0;
  // 선택 개수 라이브 표시 — 0 이면 빈 문자열(숨김 효과).
  if (selectionCountEl) selectionCountEl.textContent = n > 0 ? n + '대' : '';
}

const reportCustomerBtn = /** @type {HTMLButtonElement} */ (document.getElementById('report-customer-btn'));
const reportEngineerBtn = /** @type {HTMLButtonElement} */ (document.getElementById('report-engineer-btn'));

// 선택 N대 실시간/성능추이 — 환경 로직을 ids 한정으로 navigate (체크 서버 public_id 전달, #E4).
const realtimeSelBtn = /** @type {HTMLButtonElement | null} */ (document.getElementById('realtime-sel-btn'));
const metricsSelBtn = /** @type {HTMLButtonElement | null} */ (document.getElementById('metrics-sel-btn'));
function _selectedPublicIds() {
  return selectedRows().map(cb => cb.dataset.publicId).filter(Boolean);
}
realtimeSelBtn?.addEventListener('click', () => {
  const ids = _selectedPublicIds();
  if (ids.length) location.href = '/environment/realtime?ids=' + encodeURIComponent(ids.join(','));
});
metricsSelBtn?.addEventListener('click', () => {
  const ids = _selectedPublicIds();
  if (ids.length) location.href = '/environment/metrics?ids=' + encodeURIComponent(ids.join(','));
});

// 선택 N대 보고서 발행 모달 — 대시보드 액션 영역 '고객 보고서' / '엔지니어 보고서' 클릭 시 open.
// 서버 상세 모달 / 환경 보고서 모달과 동일 form (time_range + anchor). 발행 시 현재 탭 이동 (history.back 정합).
(function () {
  const modal = /** @type {HTMLElement} */ (document.getElementById('multi-server-report-modal'));
  if (!modal) return;
  const closeBtn = /** @type {HTMLElement} */ (document.getElementById('multi-server-report-close'));
  const submitBtn = /** @type {HTMLButtonElement} */ (document.getElementById('multi-server-report-submit'));
  const titleEl = /** @type {HTMLElement} */ (document.getElementById('multi-server-report-title'));
  const countEl = /** @type {HTMLElement} */ (document.getElementById('multi-server-report-count'));
  const rangeSel = /** @type {HTMLSelectElement} */ (document.getElementById('multi-server-report-range'));
  const anchorInput = /** @type {HTMLElement | null} */ (document.getElementById('multi-server-report-anchor'));
  const _VIEW_TITLES = { customer: '선택 서버 고객 보고서 발행', engineer: '선택 서버 엔지니어 보고서 발행' };
  let currentView = 'customer';
  /** @type {HTMLElement[]} */
  let currentRows = [];

  /** @param {'customer' | 'engineer'} view */
  function open(view) {
    const rows = selectedRows();
    if (!rows.length) return;
    currentView = view;
    currentRows = rows;
    titleEl.textContent = _VIEW_TITLES[view];
    countEl.textContent = /** @type {any} */ (rows.length);
    // 발행 버튼 활성 리셋 — 직전 발행 후 navigate + back(bfcache) 시 disabled=true 가 sticky 하게 남아 먹통 방지.
    submitBtn.disabled = false;
    modal.style.display = 'flex';
  }
  function close() { modal.style.display = 'none'; }

  // PRG — POST emit(record) → view_url GET navigate. 공용 EmitUtils(비활성·토스트·bfcache 복구 내장).
  // 다시 보기 / 북마크 / 직접 URL 은 GET 만 → record 안 됨 → 중복 방지.
  function publish() {
    // globals.d.ts EmitUtilsApi.submitNavigate 시그니처가 (url, opts) 로 선언돼 실제 (button, urlFn, opts) 3-arity 와 불일치 — 로컬 any 캐스트로 우회.
    /** @type {any} */ (window.EmitUtils).submitNavigate(submitBtn, () => {
      const ids = currentRows.map(r => r.dataset.publicId).join(',');
      const params = new URLSearchParams();
      params.set('ids', ids);
      params.set('view', currentView);
      params.set('time_range', rangeSel.value);
      return `/reports/servers/emit?${params.toString()}`;
    }, {
      pendingMsg: '보고서 발행 중...',
      errPrefix: '서버 보고서 발행 실패',
      viewUrlTransform: (/** @type {string} */ u) => u + `&back=${encodeURIComponent(location.pathname + location.search)}`,
      onRestore: close,
    });
  }

  // globals.d.ts ChartUtilsApi.initAnchor 는 (onChange) 로 선언됐으나 실제 첫 인자는 anchor element id 문자열 — 로컬 any 캐스트로 우회.
  if (anchorInput && window.ChartUtils && /** @type {any} */ (window.ChartUtils).initAnchor) {
    /** @type {any} */ (window.ChartUtils).initAnchor('multi-server-report-anchor');
  }

  reportCustomerBtn.addEventListener('click', () => open('customer'));
  reportEngineerBtn.addEventListener('click', () => open('engineer'));
  closeBtn.addEventListener('click', close);
  submitBtn.addEventListener('click', publish);
  modal.addEventListener('click', e => { if (e.target === modal) close(); });
})();

const exportBtn = /** @type {HTMLButtonElement} */ (document.getElementById('export-btn'));

async function exportInventory() {
  const rows = selectedRows();
  if (!rows.length) return;
  exportBtn.disabled = true;
  const pending = /** @type {HTMLElement} */ (ToastUtils.show(`Export 중 (${rows.length}대)...`, 'pending'));
  try {
    const res = await fetch('/api/exports/inventory', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_public_ids: rows.map(r => r.dataset.publicId) }),
    });
    pending.remove();
    if (!res.ok) {
      ToastUtils.show(`Export 실패 (HTTP ${res.status})`, 'err');
      return;
    }
    const data = await res.json();
    // 클라이언트 다운로드 — 서버는 stateless (파일 미생성)
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const ts = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 15);
    a.download = `inventory-export-${ts}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    ToastUtils.show(`${rows.length}대 Export 완료 (inventory-export-${ts}.json)`, 'ok');
  } catch (e) {
    pending.remove();
    ToastUtils.show('Export 실패: ' + /** @type {Error} */ (e).message, 'err');
  } finally {
    exportBtn.disabled = false;
    refreshInstallButton();
  }
}

exportBtn.addEventListener('click', exportInventory);

function showInstallModal() {
  const rows = selectedRows();
  if (rows.length === 0) return;
  installCountEl.textContent = /** @type {any} */ (rows.length);
  // 모달 open 시점 마다 defaultValue 강제 reset — 브라우저 autocomplete · cache · 이전 입력 잔존 우회.
  // value attribute 가 immutable defaultValue 라 server-side zdm_defaults 값으로 항상 복귀.
  ['install-zdm-target', 'install-zdm-account'].forEach(id => {
    const el = /** @type {HTMLInputElement | null} */ (document.getElementById(id));
    if (el && el.defaultValue) el.value = el.defaultValue;
  });
  installModal.style.display = 'flex';
  installSubmitBtn.focus();
}

function hideInstallModal() {
  installModal.style.display = 'none';
}

// task cell HTML 렌더 단일 진실 — install 직후 polling + 페이지 로드 시 진행 중 추적 공용.
// 시간 포맷 단일 진실 — ChartUtils.fmtKst (YYYY-MM-DD HH:MM:SS). toLocaleString 은 locale-dependent 라 회피.
/**
 * @param {Element} cell
 * @param {TaskDetailItem} detail
 */
function renderTaskCell(cell, detail) {
  const created = ChartUtils.fmtKst(detail.created_at);
  // 시각 글자크기·색은 SSR(list.html task cell) 과 동일 — 11px / .text-muted(#64748b). 새로고침 전후 일관.
  cell.innerHTML = `<a class="task-cell" href="#" data-task-id="${detail.task_id}" title="${detail.failure_label || ''}"><span class="badge ${detail.badge_class}">${detail.badge_label}</span><span class="text-muted" style="font-size:11px;">${created}</span></a>`;
}

// install 발행 직후 행별 last_task cell polling — TaskModal.pollUntilFinal 으로 final 도달 시 cell 갱신.
// pending row 의 cell HTML 을 미리 교체 -> 운영자가 즉시 "진행 중" 인지.
/**
 * @param {string} targetPublicId
 * @param {string} taskId
 */
function pollAndUpdateRow(targetPublicId, taskId) {
  const row = document.querySelector(`.row-select[data-public-id="${targetPublicId}"]`)?.closest('tr');
  if (!row) return;
  const cell = row.querySelector('td:nth-last-child(2)');
  if (!cell) return;
  cell.innerHTML = `<a class="task-cell" href="#" data-task-id="${taskId}"><span class="badge rec-pending">진행 중</span></a>`;
  if (!window.TaskModal) return;
  // globals.d.ts TaskModalApi.pollUntilFinal 은 (taskId) 1-arity 로 선언됐으나 실제 2번째 opts({onUpdate}) 인자 수용 — 로컬 any 캐스트로 우회.
  /** @type {any} */ (window.TaskModal).pollUntilFinal(taskId, { onUpdate(/** @type {TaskDetailItem} */ detail) { renderTaskCell(cell, detail); } });
}

async function submitInstall() {
  const rows = selectedRows();
  if (rows.length === 0) { ToastUtils.show('선택된 호스트 없음', 'err'); return; }

  const zdmIp = /** @type {HTMLInputElement} */ (document.getElementById('install-zdm-target')).value.trim();
  const zdmUser = /** @type {HTMLInputElement} */ (document.getElementById('install-zdm-account')).value.trim();
  if (!zdmIp || !zdmUser) {
    ToastUtils.show('ZDM IP / 관리자 계정 필수', 'err');
    return;
  }

  const pending = /** @type {HTMLElement} */ (ToastUtils.show(`Install 발행 중 (${rows.length}대)...`, 'pending'));
  installSubmitBtn.disabled = true;

  try {
    const res = await fetch('/api/tasks/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target_public_ids: rows.map(r => r.dataset.publicId),
        zdm_ip: zdmIp,
        zdm_user: zdmUser,
      }),
    });
    pending.remove();
    if (!res.ok) {
      // detail(친화 메시지)만 노출 — JSON raw·HTTP 코드 숨김. 파싱 실패 시 fallback.
      let msg;
      try { msg = (await res.json()).detail; } catch (_) { msg = '요청을 처리하지 못했습니다.'; }
      ToastUtils.show('Install 발행 실패: ' + (msg || '요청을 처리하지 못했습니다.'), 'err');
      return;
    }
    /** @type {import('../generated/api').components['schemas']['TaskCreated'][]} */
    const data = await res.json();
    const list = Array.isArray(data) ? data : [];   // 5xx가 JSON object 반환 시 TypeError 방어
    const lines = list.map(t => {
      const host = rows.find(r => r.dataset.publicId === t.target_public_id)?.dataset.hostname || t.target_public_id;
      // 오프라인 대상은 큐 적재 표식 — 발행은 됐고 재접속 시 배달(창 넘기면 만료).
      const mark = t.target_online === false ? ' (오프라인·큐 적재)' : '';
      return `- ${host} -> task ${t.task_id.slice(0, 8)}${mark}`;
    });
    const offline = list.filter(t => t.target_online === false).length;
    // 하나라도 오프라인이면 advisory(warn) — 나머지는 발행 완료(ok).
    const head = offline > 0
      ? `${list.length}대 Install 발행 — ${offline}대 오프라인(큐 적재, 재접속 시 배달)`
      : `${list.length}대 Install 발행 완료`;
    ToastUtils.show(
      `${head}<br><div style="margin-top:6px; font-family:monospace; font-size:12px;">${lines.join('<br>')}</div>`,
      offline > 0 ? 'warn' : 'ok',
    );
    hideInstallModal();
    // 행별 "최근 작업" cell polling — task 완료 시 badge 갱신.
    list.forEach(t => pollAndUpdateRow(t.target_public_id, t.task_id));
  } catch (e) {
    pending.remove();
    ToastUtils.show('Install 요청 실패: ' + /** @type {Error} */ (e).message, 'err');
  } finally {
    installSubmitBtn.disabled = false;
  }
}

// 행 체크박스 토글 → 버튼 라벨·활성화 갱신
document.querySelectorAll('.row-select').forEach(cb => {
  cb.addEventListener('change', refreshInstallButton);
});

// 전체 선택 토글
if (selectAllCb) {
  selectAllCb.addEventListener('change', () => {
    // 필터 통과 행만 토글 — 필터로 숨겨진 행(예: 오프라인)은 전체선택 제외. clip 으로만 숨은 행(match)은 포함.
    /** @type {NodeListOf<HTMLElement>} */ (document.querySelectorAll('tr.server-row')).forEach(tr => {
      const cb = /** @type {HTMLInputElement | null} */ (tr.querySelector('.row-select'));
      if (cb) cb.checked = (tr.dataset.filterMatch !== '0') && selectAllCb.checked;
    });
    refreshInstallButton();
  });
}

installBtn.addEventListener('click', showInstallModal);
installCloseBtn.addEventListener('click', hideInstallModal);
installModal.addEventListener('click', e => { if (e.target === installModal) hideInstallModal(); });
installSubmitBtn.addEventListener('click', submitInstall);

// Install 모달 input — 브라우저 autocomplete 우회. value attribute 값(defaultValue) 강제 적용.
// autocomplete="off" 만으로 일부 브라우저(Chrome 일부 버전)에서 history 덮어쓰기 가능 — 2중 가드.
['install-zdm-target', 'install-zdm-account'].forEach(id => {
  const el = /** @type {HTMLInputElement | null} */ (document.getElementById(id));
  if (el && el.defaultValue) el.value = el.defaultValue;
});

// 필터 form 즉시 client-side 적용 — server reload 없이 row hide/show + URL 갱신.
// tr 마다 data-* attribute (data-hostname / data-is-online / data-os-id / data-classification / data-services)
// 가 박혀 있어 JS 가 그 값 비교. URL replaceState 로 deep link / 새로고침 시 server-side filter 와 정합.
const filterForm = /** @type {HTMLElement} */ (document.getElementById('filter-form'));
if (filterForm) {
  // 기본 표시 행 수 — 필터 비활성 시 처음 CLIP_SIZE 행만 보이고 "더보기"로 전체 노출.
  // 필터 활성(검색·온라인·서비스·OS·분류 중 하나라도) 시엔 clip 없이 조건 맞는 전부 노출.
  const CLIP_SIZE = 20;
  let expanded = false;  // "더보기" 클릭 여부 (필터 비활성 상태에서만 의미)

  /**
   * @param {boolean} visible
   * @param {number} total
   */
  function updateShowMore(visible, total) {
    const wrap = document.getElementById('show-more-wrap');
    if (wrap) wrap.style.display = visible ? '' : 'none';
    if (!visible) return;
    // 확장 상태면 "접기"(CLIP 복귀), 아니면 "전체보기 (CLIP/total)".
    const btn = /** @type {HTMLElement | null} */ (document.getElementById('show-more-btn'));
    const c = /** @type {HTMLElement | null} */ (document.getElementById('show-more-count'));
    if (btn && btn.firstChild) btn.firstChild.nodeValue = expanded ? '접기 ' : '전체보기 ';
    if (c) c.textContent = expanded ? '' : `(${CLIP_SIZE}/${total})`;
  }

  function applyFilters() {
    const rows = /** @type {NodeListOf<HTMLElement>} */ (document.querySelectorAll('tr.server-row'));  // 매번 재조회 — 자동갱신 행 교체 후에도 정합
    const searchInput = /** @type {HTMLInputElement | null} */ (filterForm.querySelector('input[name=search]'));
    // 통합 검색 — hostname·OS·워크로드·자원 적정성·EOL·온라인 여부를 한 문자열(data-search)에서 부분일치.
    const search = (searchInput?.value || '').toLowerCase().trim();
    const active = !!search;
    let matchCount = 0;  // 필터 통과 행 수 (clip 이전)
    rows.forEach(tr => {
      const hay = (tr.dataset.search || '').toLowerCase();
      const match = !search || hay.includes(search);
      let visible = match;
      if (match) {
        matchCount += 1;
        // 검색 비활성 + 미확장이면 CLIP_SIZE 초과 행은 숨김 (더보기 대상).
        if (!active && !expanded && matchCount > CLIP_SIZE) visible = false;
      }
      // 전체선택이 검색 통과 행만 잡도록 표식 (clip 으로만 숨은 행은 match='1' 이라 포함).
      tr.dataset.filterMatch = match ? '1' : '0';
      tr.style.display = visible ? '' : 'none';
    });
    // 전체보기 버튼 — 검색 비활성·미확장·전체가 CLIP 초과일 때만. (CLIP_SIZE/total 표기)
    updateShowMore(!active && matchCount > CLIP_SIZE, matchCount);
    const clearBtn = document.getElementById('filter-clear');
    if (clearBtn) clearBtn.style.display = active ? 'inline-flex' : 'none';
    if (searchInput) searchInput.classList.toggle('active', !!search);
    // 보이는 행만 zebra 재줄무늬 — 검색·clip 으로 숨은 행 제외(흰색 어긋남 방지, table-utils).
    if (window.TableUtils && listTable) window.TableUtils.restripe(listTable);
  }

  // 전체보기/접기 토글 — expanded 반전 후 재적용 (전체 노출 <-> CLIP 복귀).
  document.getElementById('show-more-btn')?.addEventListener('click', () => {
    expanded = !expanded;
    applyFilters();
  });

  // ─── 칼럼 클릭 정렬 — 공용 TableUtils(정렬 로직·zebra 단일화). 정렬 후 필터·clip 재적용(applyFilters 끝에서 restripe). ───
  const listTable = /** @type {HTMLElement | null} */ (document.querySelector('table.server-list-table'));
  listTable?.querySelector('thead')?.addEventListener('click', function (e) {
    const th = /** @type {HTMLElement} */ (e.target).closest('th.sort-col');
    if (!th) return;
    const idx = Array.from(/** @type {ParentNode} */ (th.parentNode).children).indexOf(th);
    window.TableUtils.sortByColumn(/** @type {HTMLElement} */ (listTable), idx);
    expanded = false; // 정렬 후 CLIP 복귀 (상위 재적용)
    applyFilters();
  });

  // 필터 변경 시 선택 초기화 — 전체선택 후 필터를 바꾸면 깨끗한 전체해제 상태 (숨은 행이 선택에 남지 않게).
  // show-more(전체보기)·init 은 필터 변경이 아니므로 미적용 (선택 보존).
  function clearSelectionOnFilterChange() {
    /** @type {NodeListOf<HTMLInputElement>} */ (document.querySelectorAll('.row-select')).forEach(cb => { cb.checked = false; });
    if (selectAllCb) selectAllCb.checked = false;
    refreshInstallButton();
  }

  // text input — typing 마다 debounce (200ms) client filter.
  /** @type {any} */
  let debounceTimer = null;
  filterForm.querySelector('input[name=search]')?.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => { clearSelectionOnFilterChange(); applyFilters(); }, 200);
  });
  // select / checkbox — change 즉시.
  filterForm.querySelectorAll('select, input[type=checkbox]').forEach(el => {
    el.addEventListener('change', () => { clearSelectionOnFilterChange(); applyFilters(); });
  });
  // Enter 키 default form submit 방지 (page reload 없이).
  filterForm.addEventListener('submit', e => {
    e.preventDefault();
    clearSelectionOnFilterChange();
    applyFilters();
  });

  // 자동갱신(replaceServerRows) 후 client 필터 재적용용 — 모듈 외부 노출.
  /** @type {any} */ (window).__applyDashboardFilters = applyFilters;
  // 초기 1회 — 전체 로드된 행에 clip(20) 적용 + deep-link query(form 초기값) 반영.
  applyFilters();
}

// ─── 진행 중 작업 자동 추적 ──────────────────────────────────────────────
// 서버목록의 진행 중(rec-pending) task cell 을 pollUntilFinal 로 추적 — 완료까지 cell 갱신.
// 페이지 로드 시 + install 발행 직후 호출. task-cell 모달은 delegation(task-modal.js).
function trackPendingTasks() {
  /** @type {NodeListOf<HTMLElement>} */ (document.querySelectorAll('.task-cell')).forEach(a => {
    if (!a.querySelector('.badge.rec-pending')) return;
    const taskId = a.dataset.taskId;
    if (!taskId || !window.TaskModal) return;
    const cell = /** @type {Element} */ (a.closest('td'));
    /** @type {any} */ (window.TaskModal).pollUntilFinal(taskId, { onUpdate(/** @type {TaskDetailItem} */ detail) { renderTaskCell(cell, detail); } });
  });
}

trackPendingTasks();

// ─── 뒤로가기 복원 정합 (선택 초기화) ─────────────────────────────────────
// 액션 버튼(보고서·실시간·성능추이·Export·Install)은 클릭 시 다른 페이지로 navigate 한다.
// 브라우저는 뒤로가기 시 체크박스 checked 를 자동 복원(session history form-state / bfcache)하는데,
// 그 복원은 change 이벤트를 발생시키지 않아 refreshInstallButton 이 안 돌고 버튼 enabled·"N대"
// 카운트가 desync 된다 — 체크는 살아있는데 버튼은 disabled 로 남는 "먹통". 복귀 시 선택을 항상
// 초기화해 "뒤로 오면 깨끗한 목록" 을 결정적으로 보장 (bfcache / full-reload 브라우저 차이 무관).
function resetSelectionOnShow() {
  /** @type {NodeListOf<HTMLInputElement>} */ (document.querySelectorAll('.row-select')).forEach(cb => { cb.checked = false; });
  if (selectAllCb) selectAllCb.checked = false;
  // 열린 모달 닫기 + 발행 버튼 재활성 — navigate 직전 disable 이 history 복원에 sticky 로 남는 것 방지.
  ['multi-server-report-modal', 'install-modal'].forEach(id => {
    const m = document.getElementById(id);
    if (m) m.style.display = 'none';
  });
  ['multi-server-report-submit', 'install-submit'].forEach(id => {
    const b = /** @type {HTMLButtonElement | null} */ (document.getElementById(id));
    if (b) b.disabled = false;
  });
  refreshInstallButton();  // 초기화된 실제 체크 상태(0) 기준으로 버튼 disabled·카운트 재계산
}
// pageshow 는 최초 로드·뒤로가기 복원 모두에서 발화 (persisted 여부 무관) — 두 경우 다 깨끗한 상태로 수렴.
window.addEventListener('pageshow', resetSelectionOnShow);
