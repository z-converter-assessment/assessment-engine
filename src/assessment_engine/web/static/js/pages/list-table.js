/**
 * 서버 목록 페이지(list_table.html) — 서버 발견·ZConverter Install·선택 N대 액션(보고서·Export·실시간/성능추이)·검색 필터.
 *
 * 서버 발견: IP/hostname 입력 -> SSH 포트(기본 22) TCP connect로 도달성 확인 (Ansible 배포 1단계).
 *   한계: 포트 listen != 로그인 가능. 1차 필터일 뿐.
 *   probe 기본 target/port는 서버가 #probe-ip / #probe-port value로 렌더.
 *
 * 외부 의존: 없음 (모달은 list_table.html에 inline markup).
 */

const modal       = document.getElementById('discover-modal');
const openBtn     = document.getElementById('discover-btn');
const closeBtn    = document.getElementById('discover-close');
const probeBtn    = document.getElementById('probe-btn');
const ipInput     = document.getElementById('probe-ip');
const portInput   = document.getElementById('probe-port');
const resultEl    = document.getElementById('probe-result');

function showModal() {
  modal.style.display = 'flex';
  ipInput.focus();
  ipInput.select();
  resultEl.style.display = 'none';
  resultEl.textContent = '';
}

function hideModal() {
  modal.style.display = 'none';
}

function renderResult(data, errMsg) {
  resultEl.style.display = '';
  if (errMsg) {
    resultEl.style.background = '#fef2f2';
    resultEl.style.color = '#991b1b';
    resultEl.style.border = '1px solid #fecaca';
    resultEl.textContent = '도달 불가 — ' + errMsg;
    return;
  }
  if (data.reachable) {
    resultEl.style.background = '#f0fdf4';
    resultEl.style.color = '#166534';
    resultEl.style.border = '1px solid #bbf7d0';
    // banner 는 외부(SSH 서버)가 보낸 문자열 — textContent 로 삽입해 XSS 차단.
    const detail = data.banner ? `SSH 확인 — ${data.banner}` : '포트 열림 (SSH 응답 없음)';
    resultEl.textContent = `도달 가능 — ${detail}, ${data.elapsed_ms}ms`;
  } else {
    resultEl.style.background = '#fef2f2';
    resultEl.style.color = '#991b1b';
    resultEl.style.border = '1px solid #fecaca';
    resultEl.textContent = `도달 불가 — ${data.error || 'unknown error'} (${data.elapsed_ms}ms)`;
  }
}

async function runProbe() {
  const target = ipInput.value.trim();
  const port   = parseInt(portInput.value, 10);

  if (!target) { renderResult(null, '대상(IP 또는 hostname)을 입력하세요'); return; }
  if (!port || port < 1 || port > 65535) { renderResult(null, '포트는 1~65535 범위'); return; }

  // 진행 표시
  resultEl.style.display = '';
  resultEl.style.background = '#f1f5f9';
  resultEl.style.color = '#64748b';
  resultEl.style.border = '1px solid #e2e8f0';
  resultEl.textContent = '확인 중...';
  probeBtn.disabled = true;

  try {
    const res = await fetch('/api/discovery/probe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target, port }),
    });
    if (res.status === 422) {
      const detail = await res.json();
      renderResult(null, '입력 형식 오류: ' + JSON.stringify(detail.detail));
      return;
    }
    if (!res.ok) {
      renderResult(null, `서버 오류 (HTTP ${res.status})`);
      return;
    }
    const data = await res.json();
    renderResult(data, null);
  } catch (e) {
    renderResult(null, '요청 실패: ' + e.message);
  } finally {
    probeBtn.disabled = false;
  }
}

openBtn.addEventListener('click', showModal);
closeBtn.addEventListener('click', hideModal);
modal.addEventListener('click', e => { if (e.target === modal) hideModal(); });  // 배경 클릭 시 닫기
probeBtn.addEventListener('click', runProbe);
ipInput.addEventListener('keypress', e => { if (e.key === 'Enter') runProbe(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && (modal.style.display === 'flex' || installModal.style.display === 'flex')) {
  hideModal(); hideInstallModal();
}});


// ─── ZConverter Install ────────────────────────────────────────────────────
// 체크박스로 호스트 선택 -> POST /api/tasks/install.
// engine 은 DB INSERT + agent.tasks.<composite_id> 큐 동적 declare + task.install publish.
// download.url 은 운영자 입력 ZDM host + ZDM_PACKAGE_PATH 조립, sha256/size_bytes 는 ZDM_PACKAGE_* env.

const installModal            = document.getElementById('install-modal');
const installBtn              = document.getElementById('install-btn');
const installCloseBtn         = document.getElementById('install-close');
const installSubmitBtn        = document.getElementById('install-submit');
const installCountEl          = document.getElementById('install-count');
const selectAllCb      = document.getElementById('select-all');

function selectedRows() {
  return [...document.querySelectorAll('.row-select:checked')];
}

// selection 버튼 상태 갱신 — 선택 N대면 액션 버튼 enabled.
// CSS .btn-primary:disabled 가 시각 분기 (opacity inline 불필요).
function refreshInstallButton() {
  const n = selectedRows().length;
  installBtn.disabled = n === 0;
  exportBtn.disabled = n === 0;
  reportCustomerBtn.disabled = n === 0;
  reportEngineerBtn.disabled = n === 0;
  if (realtimeSelBtn) realtimeSelBtn.disabled = n === 0;
  if (metricsSelBtn) metricsSelBtn.disabled = n === 0;
}

const reportCustomerBtn = document.getElementById('report-customer-btn');
const reportEngineerBtn = document.getElementById('report-engineer-btn');

// 선택 N대 실시간/성능추이 — 환경 로직을 ids 한정으로 navigate (체크 서버 public_id 전달, #E4).
const realtimeSelBtn = document.getElementById('realtime-sel-btn');
const metricsSelBtn = document.getElementById('metrics-sel-btn');
function _selectedPublicIds() {
  return selectedRows().map(cb => cb.dataset.publicId).filter(Boolean);
}
realtimeSelBtn?.addEventListener('click', () => {
  const ids = _selectedPublicIds();
  if (ids.length) location.href = '/servers/environment/realtime?ids=' + encodeURIComponent(ids.join(','));
});
metricsSelBtn?.addEventListener('click', () => {
  const ids = _selectedPublicIds();
  if (ids.length) location.href = '/servers/environment/metrics?ids=' + encodeURIComponent(ids.join(','));
});

// 선택 N대 보고서 발행 모달 — 대시보드 액션 영역 '고객 보고서' / '엔지니어 보고서' 클릭 시 open.
// 서버 상세 모달 / 환경 보고서 모달과 동일 form (time_range + anchor). 발행 시 현재 탭 이동 (history.back 정합).
(function () {
  const modal = document.getElementById('multi-server-report-modal');
  if (!modal) return;
  const closeBtn = document.getElementById('multi-server-report-close');
  const submitBtn = document.getElementById('multi-server-report-submit');
  const titleEl = document.getElementById('multi-server-report-title');
  const countEl = document.getElementById('multi-server-report-count');
  const rangeSel = document.getElementById('multi-server-report-range');
  const anchorInput = document.getElementById('multi-server-report-anchor');
  const _VIEW_TITLES = { customer: '선택 서버 고객 보고서 발행', engineer: '선택 서버 엔지니어 보고서 발행' };
  let currentView = 'customer';
  let currentRows = [];

  function open(view) {
    const rows = selectedRows();
    if (!rows.length) return;
    currentView = view;
    currentRows = rows;
    titleEl.textContent = _VIEW_TITLES[view];
    countEl.textContent = rows.length;
    // 발행 버튼 활성 리셋 — 직전 발행 후 navigate + back(bfcache) 시 disabled=true 가 sticky 하게 남아 먹통 방지.
    submitBtn.disabled = false;
    modal.style.display = 'flex';
  }
  function close() { modal.style.display = 'none'; }

  async function publish() {
    const ids = currentRows.map(r => r.dataset.publicId).join(',');
    const params = new URLSearchParams();
    params.set('ids', ids);
    params.set('view', currentView);
    params.set('time_range', rangeSel.value);
    // PRG pattern — POST emit (record) → 응답 view_url 로 GET navigate (read-only 표시).
    // 다시 보기 / 북마크 / 직접 URL 은 GET 만 호출 → record 안 됨 → 중복 방지.
    submitBtn.disabled = true;
    try {
      const res = await fetch(`/servers/report/emit?${params.toString()}`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const viewUrl = data.view_url + `&back=${encodeURIComponent(location.pathname + location.search)}`;
      window.location.href = viewUrl;
    } catch (e) {
      if (window.ToastUtils) ToastUtils.show('서버 보고서 발행 실패: ' + e.message, 'err');
      submitBtn.disabled = false;
    }
    close();
  }

  if (anchorInput && window.ChartUtils && window.ChartUtils.initAnchor) {
    window.ChartUtils.initAnchor('multi-server-report-anchor');
  }

  reportCustomerBtn.addEventListener('click', () => open('customer'));
  reportEngineerBtn.addEventListener('click', () => open('engineer'));
  closeBtn.addEventListener('click', close);
  submitBtn.addEventListener('click', publish);
  modal.addEventListener('click', e => { if (e.target === modal) close(); });
})();

const exportBtn = document.getElementById('export-btn');

async function exportInventory() {
  const rows = selectedRows();
  if (!rows.length) return;
  exportBtn.disabled = true;
  const pending = ToastUtils.show(`Export 중 (${rows.length}대)...`, 'pending');
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
    ToastUtils.show('Export 실패: ' + e.message, 'err');
  } finally {
    exportBtn.disabled = false;
    refreshInstallButton();
  }
}

exportBtn.addEventListener('click', exportInventory);

function showInstallModal() {
  const rows = selectedRows();
  if (rows.length === 0) return;
  installCountEl.textContent = rows.length;
  // 모달 open 시점 마다 defaultValue 강제 reset — 브라우저 autocomplete · cache · 이전 입력 잔존 우회.
  // value attribute 가 immutable defaultValue 라 server-side zdm_defaults 값으로 항상 복귀.
  ['install-zdm-target', 'install-zdm-account'].forEach(id => {
    const el = document.getElementById(id);
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
function renderTaskCell(cell, detail) {
  const created = ChartUtils.fmtKst(detail.created_at);
  // 시각 글자크기·색은 SSR(list.html task cell) 과 동일 — 11px / .text-muted(#64748b). 새로고침 전후 일관.
  cell.innerHTML = `<a class="task-cell" href="#" data-task-id="${detail.task_id}" title="${detail.failure_label || ''}"><span class="badge ${detail.badge_class}">${detail.badge_label}</span><span class="text-muted" style="font-size:11px;">${created}</span></a>`;
}

// install 발행 직후 행별 last_task cell polling — TaskModal.pollUntilFinal 으로 final 도달 시 cell 갱신.
// pending row 의 cell HTML 을 미리 교체 -> 운영자가 즉시 "진행 중" 인지.
function pollAndUpdateRow(targetPublicId, taskId) {
  const row = document.querySelector(`.row-select[data-public-id="${targetPublicId}"]`)?.closest('tr');
  if (!row) return;
  const cell = row.querySelector('td:nth-last-child(2)');
  if (!cell) return;
  cell.innerHTML = `<a class="task-cell" href="#" data-task-id="${taskId}"><span class="badge rec-pending">진행 중</span></a>`;
  if (!window.TaskModal) return;
  window.TaskModal.pollUntilFinal(taskId, { onUpdate(detail) { renderTaskCell(cell, detail); } });
}

async function submitInstall() {
  const rows = selectedRows();
  if (rows.length === 0) { ToastUtils.show('선택된 호스트 없음', 'err'); return; }

  const zdmIp = document.getElementById('install-zdm-target').value.trim();
  const zdmUser = document.getElementById('install-zdm-account').value.trim();
  if (!zdmIp || !zdmUser) {
    ToastUtils.show('ZDM IP / 관리자 계정 필수', 'err');
    return;
  }

  const pending = ToastUtils.show(`Install 발행 중 (${rows.length}대)...`, 'pending');
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
    const data = await res.json();
    const list = Array.isArray(data) ? data : [];   // 5xx가 JSON object 반환 시 TypeError 방어
    const lines = list.map(t => `- ${rows.find(r => r.dataset.publicId === t.target_public_id)?.dataset.hostname || t.target_public_id} -> task ${t.task_id.slice(0, 8)}`);
    ToastUtils.show(
      `${list.length}대 Install 발행 완료<br><div style="margin-top:6px; font-family:monospace; font-size:12px;">${lines.join('<br>')}</div>`,
      'ok',
    );
    hideInstallModal();
    // 행별 "최근 작업" cell polling — task 완료 시 badge 갱신.
    list.forEach(t => pollAndUpdateRow(t.target_public_id, t.task_id));
  } catch (e) {
    pending.remove();
    ToastUtils.show('Install 요청 실패: ' + e.message, 'err');
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
    document.querySelectorAll('.row-select').forEach(cb => { cb.checked = selectAllCb.checked; });
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
  const el = document.getElementById(id);
  if (el && el.defaultValue) el.value = el.defaultValue;
});

// 필터 form 즉시 client-side 적용 — server reload 없이 row hide/show + URL 갱신.
// tr 마다 data-* attribute (data-hostname / data-is-online / data-os-id / data-classification / data-services)
// 가 박혀 있어 JS 가 그 값 비교. URL replaceState 로 deep link / 새로고침 시 server-side filter 와 정합.
const filterForm = document.getElementById('filter-form');
if (filterForm) {
  // 기본 표시 행 수 — 필터 비활성 시 처음 CLIP_SIZE 행만 보이고 "더보기"로 전체 노출.
  // 필터 활성(검색·온라인·서비스·OS·분류 중 하나라도) 시엔 clip 없이 조건 맞는 전부 노출.
  const CLIP_SIZE = 5;
  let expanded = false;  // "더보기" 클릭 여부 (필터 비활성 상태에서만 의미)

  function updateShowMore(visible, total) {
    const wrap = document.getElementById('show-more-wrap');
    if (wrap) wrap.style.display = visible ? '' : 'none';
    if (!visible) return;
    // 확장 상태면 "접기"(CLIP 복귀), 아니면 "전체보기 (CLIP/total)".
    const btn = document.getElementById('show-more-btn');
    const c = document.getElementById('show-more-count');
    if (btn && btn.firstChild) btn.firstChild.nodeValue = expanded ? '접기 ' : '전체보기 ';
    if (c) c.textContent = expanded ? '' : `(${CLIP_SIZE}/${total})`;
  }

  function applyFilters() {
    const rows = document.querySelectorAll('tr.server-row');  // 매번 재조회 — 자동갱신 행 교체 후에도 정합
    const searchInput = filterForm.querySelector('input[name=search]');
    const onlineSel = filterForm.querySelector('select[name=is_online]');
    const serviceSel = filterForm.querySelector('select[name=service]');
    const osSel = filterForm.querySelector('select[name=os_distro]');
    const classSel = filterForm.querySelector('select[name=classification]');
    const search = (searchInput?.value || '').toLowerCase().trim();
    const onlineState = onlineSel?.value || '';  // "" / "true" / "false"
    const service = serviceSel?.value || '';
    const osDistro = osSel?.value || '';
    const classification = classSel?.value || '';
    const active = !!(search || onlineState || service || osDistro || classification);
    let matchCount = 0;  // 필터 통과 행 수 (clip 이전)
    rows.forEach(tr => {
      const hostname = tr.dataset.hostname || '';
      const rowOnline = tr.dataset.isOnline === 'true';
      const rowOs = tr.dataset.osDistro || '';
      const rowClass = tr.dataset.classification || '';
      const rowServices = (tr.dataset.services || '').trim().split(/\s+/);
      let match = true;
      if (search && !hostname.includes(search)) match = false;
      if (onlineState === 'true' && !rowOnline) match = false;
      if (onlineState === 'false' && rowOnline) match = false;
      if (service && !rowServices.includes(service)) match = false;
      if (osDistro && rowOs !== osDistro) match = false;
      if (classification && rowClass !== classification) match = false;
      let visible = match;
      if (match) {
        matchCount += 1;
        // 필터 비활성 + 미확장이면 CLIP_SIZE 초과 행은 숨김 (더보기 대상).
        if (!active && !expanded && matchCount > CLIP_SIZE) visible = false;
      }
      tr.style.display = visible ? '' : 'none';
    });
    // 전체보기 버튼 — 필터 비활성·미확장·전체가 CLIP 초과일 때만. (CLIP_SIZE/total 표기)
    updateShowMore(!active && matchCount > CLIP_SIZE, matchCount);
    // URL 갱신 — deep link / 새로고침 시 server-side filter 가 같은 query 받음 (일관).
    const params = new URLSearchParams();
    if (search) params.set('search', search);
    if (onlineState) params.set('is_online', onlineState);
    if (service) params.set('service', service);
    if (osDistro) params.set('os_distro', osDistro);
    if (classification) params.set('classification', classification);
    const qs = params.toString();
    const newUrl = qs ? `${location.pathname}?${qs}` : location.pathname;
    history.replaceState(null, '', newUrl);
  }

  // 전체보기/접기 토글 — expanded 반전 후 재적용 (전체 노출 <-> CLIP 복귀).
  document.getElementById('show-more-btn')?.addEventListener('click', () => {
    expanded = !expanded;
    applyFilters();
  });

  // text input — typing 마다 debounce (200ms) client filter.
  let debounceTimer = null;
  filterForm.querySelector('input[name=search]')?.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(applyFilters, 200);
  });
  // select / checkbox — change 즉시.
  filterForm.querySelectorAll('select, input[type=checkbox]').forEach(el => {
    el.addEventListener('change', applyFilters);
  });
  // Enter 키 default form submit 방지 (page reload 없이).
  filterForm.addEventListener('submit', e => {
    e.preventDefault();
    applyFilters();
  });

  // 자동갱신(replaceServerRows) 후 client 필터 재적용용 — 모듈 외부 노출.
  window.__applyDashboardFilters = applyFilters;
  // 초기 1회 — 전체 로드된 행에 clip(20) 적용 + deep-link query(form 초기값) 반영.
  applyFilters();
}

// ─── 진행 중 작업 자동 추적 ──────────────────────────────────────────────
// 서버목록의 진행 중(rec-pending) task cell 을 pollUntilFinal 로 추적 — 완료까지 cell 갱신.
// 페이지 로드 시 + install 발행 직후 호출. task-cell 모달은 delegation(task-modal.js).
function trackPendingTasks() {
  document.querySelectorAll('.task-cell').forEach(a => {
    if (!a.querySelector('.badge.rec-pending')) return;
    const taskId = a.dataset.taskId;
    if (!taskId || !window.TaskModal) return;
    const cell = a.closest('td');
    window.TaskModal.pollUntilFinal(taskId, { onUpdate(detail) { renderTaskCell(cell, detail); } });
  });
}

trackPendingTasks();
