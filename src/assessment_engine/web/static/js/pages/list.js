/**
 * 서버 목록 페이지 — 서버 발견 모달.
 *
 * 역할: IP 입력 → HTTP probe로 네트워크 도달성 확인.
 * Ansible 자동 배포 워크플로우의 1단계 (도달성 검사). 추후 SSH credential 등록 +
 * ansible-playbook 실행이 이 위에 얹힘.
 *
 * 한계: HTTP 도달 ≠ SSH 도달. 1차 필터일 뿐.
 *
 * 외부 의존: 없음 (모달은 list.html에 inline markup).
 */

const modal       = document.getElementById('discover-modal');
const openBtn     = document.getElementById('discover-btn');
const closeBtn    = document.getElementById('discover-close');
const probeBtn    = document.getElementById('probe-btn');
const ipInput     = document.getElementById('probe-ip');
const portInput   = document.getElementById('probe-port');
const schemeInput = document.getElementById('probe-scheme');
const resultEl    = document.getElementById('probe-result');

// probe target default — 브라우저 접근 host 우선, localhost 시 Lima dev fallback.
// dev 환경: web 컨테이너 → host.docker.internal (Docker Desktop magic — macOS host 도달 가능).
//   Lima vmnet IP(192.168.5.2)는 docker network 격리로 도달 불가라 docker host alias 사용.
// 운영 환경: window.location.hostname 그대로 — IP·hostname 둘 다 backend가 받음 (ProbeRequest.target).
function _defaultProbeTarget() {
  const h = window.location.hostname;
  if (h === 'localhost' || h === '127.0.0.1' || h === '0.0.0.0' || h === '') {
    return 'host.docker.internal';
  }
  return h;
}

function showModal() {
  modal.style.display = 'flex';
  if (!ipInput.value.trim()) {
    ipInput.value = _defaultProbeTarget();
  }
  ipInput.focus();
  ipInput.select();
  resultEl.style.display = 'none';
  resultEl.innerHTML = '';
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
    resultEl.textContent = '❌ ' + errMsg;
    return;
  }
  if (data.reachable) {
    resultEl.style.background = '#f0fdf4';
    resultEl.style.color = '#166534';
    resultEl.style.border = '1px solid #bbf7d0';
    resultEl.innerHTML = `✅ 도달 가능 — HTTP ${data.status_code}, ${data.elapsed_ms}ms`;
  } else {
    resultEl.style.background = '#fef2f2';
    resultEl.style.color = '#991b1b';
    resultEl.style.border = '1px solid #fecaca';
    resultEl.innerHTML = `❌ 도달 불가 — ${data.error || 'unknown error'} (${data.elapsed_ms}ms)`;
  }
}

async function runProbe() {
  const target = ipInput.value.trim();
  const port   = parseInt(portInput.value, 10);
  const scheme = schemeInput.value;

  if (!target) { renderResult(null, '대상(IP 또는 hostname)을 입력하세요'); return; }
  if (!port || port < 1 || port > 65535) { renderResult(null, '포트는 1~65535 범위'); return; }

  // 진행 표시
  resultEl.style.display = '';
  resultEl.style.background = '#f1f5f9';
  resultEl.style.color = '#64748b';
  resultEl.style.border = '1px solid #e2e8f0';
  resultEl.textContent = '⏳ 확인 중...';
  probeBtn.disabled = true;

  try {
    const res = await fetch('/api/v1/discovery/probe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target, port, scheme }),
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
// 체크박스로 호스트 선택 -> POST /api/v1/tasks/install.
// engine 은 DB INSERT + agent.tasks.<machine_id> 큐 동적 declare + task.install publish.
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

// selection 버튼 상태 + 체크박스 헤더 카운트 갱신. label 자체는 정적 (체크박스 헤더 #select-count 가 동적 N 표시).
// CSS .btn-primary:disabled 가 시각 분기 (opacity inline 불필요).
function refreshInstallButton() {
  const n = selectedRows().length;
  const countEl = document.getElementById('select-count');
  if (countEl) {
    countEl.textContent = n;
    countEl.style.color = n > 0 ? '#2563eb' : '#cbd5e1';
  }
  installBtn.disabled = n === 0;
  exportBtn.disabled = n === 0;
  reportCustomerBtn.disabled = n === 0;
  reportEngineerBtn.disabled = n === 0;
}

const reportCustomerBtn = document.getElementById('report-customer-btn');
const reportEngineerBtn = document.getElementById('report-engineer-btn');

// 환경 보고서 발행 — 카드 본문 두 버튼 (고객/엔지니어) 이 view 사전 결정 후 모달 open.
// 모달은 윈도우·anchor 입력 + 발행 단일 버튼. 발행 시 새 탭으로 /reports/environment.
(function () {
  const modal = document.getElementById('env-report-modal');
  if (!modal) return;
  const customerOpenBtn = document.getElementById('env-report-customer-open');
  const engineerOpenBtn = document.getElementById('env-report-engineer-open');
  const closeBtn = document.getElementById('env-report-close');
  const submitBtn = document.getElementById('env-report-submit');
  const titleEl = document.getElementById('env-report-title');
  const descEl = document.getElementById('env-report-desc');
  const rangeSel = document.getElementById('env-report-range');
  const anchorInput = document.getElementById('env-report-anchor');
  // 문구 단일 진실 — AI 진단 발행 모달 패턴(#diagnose-modal) 과 어조 통일.
  const _VIEW_TITLES = { customer: '환경 고객 보고서 발행', engineer: '환경 엔지니어 보고서 발행' };
  const _VIEW_DESCS = {
    customer: '전체 등록 서버 대상 Right-sizing 규칙 기반 고객 보고서 발행. 새 탭으로 이동합니다.',
    engineer: '전체 등록 서버 대상 Right-sizing 규칙 기반 엔지니어 보고서 발행. 새 탭으로 이동합니다.',
  };
  let currentView = 'customer';

  function open(view) {
    currentView = view;
    titleEl.textContent = _VIEW_TITLES[view];
    descEl.textContent = _VIEW_DESCS[view];
    modal.style.display = 'flex';
  }
  function close() { modal.style.display = 'none'; }

  function publish() {
    const params = new URLSearchParams();
    params.set('view', currentView);
    params.set('time_range', rangeSel.value);
    const anchor = anchorInput.value;
    if (anchor) params.set('anchor_at', anchor + ':00+09:00');
    params.set('back', location.pathname);
    // 모든 보고서 발행 = 현재 탭 이동 (history.back / back query 로 referrer 자연 복귀, 단일 원칙).
    window.location.href = `/reports/environment?${params.toString()}`;
    close();
  }

  // 페이지 로드 시점에 anchor 기본값 채움 — 모달 open 시 reset 안 함 (사용자 변경 값 보존).
  if (anchorInput && window.ChartUtils && window.ChartUtils.initAnchor) {
    window.ChartUtils.initAnchor('env-report-anchor');
  }

  customerOpenBtn.addEventListener('click', () => open('customer'));
  engineerOpenBtn.addEventListener('click', () => open('engineer'));
  closeBtn.addEventListener('click', close);
  submitBtn.addEventListener('click', publish);
  modal.addEventListener('click', e => { if (e.target === modal) close(); });
})();

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
    modal.style.display = 'flex';
  }
  function close() { modal.style.display = 'none'; }

  function publish() {
    const ids = currentRows.map(r => r.dataset.publicId).join(',');
    const params = new URLSearchParams();
    params.set('ids', ids);
    params.set('view', currentView);
    params.set('time_range', rangeSel.value);
    params.set('back', location.pathname);
    // anchor 는 server scope 라우터가 받지 않음 — UI 일관성. submit 미사용.
    window.location.href = `/servers/report?${params.toString()}`;
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
    const res = await fetch('/api/v1/exports/inventory', {
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

// install 발행 직후 행별 last_task cell polling — TaskModal.pollUntilFinal 으로 final 도달 시 cell 갱신.
// pending row 의 cell HTML 을 미리 교체 -> 운영자가 즉시 "진행 중" 인지.
function pollAndUpdateRow(targetPublicId, taskId) {
  const row = document.querySelector(`.row-select[data-public-id="${targetPublicId}"]`)?.closest('tr');
  if (!row) return;
  const cell = row.querySelector('td:nth-last-child(2)');
  if (!cell) return;
  cell.innerHTML = `<a class="task-cell" href="#" data-task-id="${taskId}"><span class="badge rec-pending">진행 중</span></a>`;
  if (!window.TaskModal) return;
  window.TaskModal.pollUntilFinal(taskId, {
    onUpdate(detail) {
      const created = new Date(detail.created_at).toLocaleString('ko-KR');
      cell.innerHTML = `<a class="task-cell" href="#" data-task-id="${detail.task_id}" title="${detail.failure_label || ''}"><span class="badge ${detail.badge_class}">${detail.badge_label}</span><span style="font-size:11px; color:#64748b;">${created}</span></a>`;
    },
  });
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
    const res = await fetch('/api/v1/tasks/install', {
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
      const detail = await res.text();
      ToastUtils.show(`Install 발행 실패 (HTTP ${res.status}): ${detail}`, 'err');
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

// --- 서버 진단 batch 발행 (선택 N대, scope=server) ---
const diagModal     = document.getElementById('diagnose-modal');
const diagBtn       = document.getElementById('diagnose-btn');
const diagCloseBtn  = document.getElementById('diag-close');
const diagSubmitBtn = document.getElementById('diag-submit');
const diagCountEl   = document.getElementById('diag-count');
const diagRangeSel  = document.getElementById('diag-range');

function refreshDiagButton() {
  if (!diagBtn) return;
  const n = selectedRows().length;
  diagBtn.disabled = n === 0;
}

// row checkbox change listener는 이미 line 254에 등록됨 (refreshInstallButton).
// reassign으로는 등록된 reference가 안 갱신되므로 별도 listener 추가.
document.querySelectorAll('.row-select').forEach(cb => {
  cb.addEventListener('change', refreshDiagButton);
});
if (selectAllCb) {
  selectAllCb.addEventListener('change', refreshDiagButton);
}
refreshDiagButton();

function showDiagModal() {
  const rows = selectedRows();
  if (rows.length === 0) return;
  diagCountEl.textContent = rows.length;
  diagModal.style.display = 'flex';
}

// 페이지 로드 시점에 #diag-anchor 기본값 채움 — 모달 open 시 reset 안 함 (사용자 변경값 보존).
if (window.ChartUtils && window.ChartUtils.initAnchor && document.getElementById('diag-anchor')) {
  window.ChartUtils.initAnchor('diag-anchor');
}

// Install 모달 input — 브라우저 autocomplete 우회. value attribute 값(defaultValue) 강제 적용.
// autocomplete="off" 만으로 일부 브라우저(Chrome 일부 버전)에서 history 덮어쓰기 가능 — 2중 가드.
['install-zdm-target', 'install-zdm-account'].forEach(id => {
  const el = document.getElementById(id);
  if (el && el.defaultValue) el.value = el.defaultValue;
});

function hideDiagModal() {
  diagModal.style.display = 'none';
}

async function submitDiag() {
  const rows = selectedRows();
  if (rows.length === 0) { ToastUtils.show('선택된 서버 없음', 'err'); return; }
  const time_range = diagRangeSel.value;
  let anchor_at = null;
  if (window.ChartUtils && window.ChartUtils.getAnchorEnd) {
    const d = window.ChartUtils.getAnchorEnd('diag-anchor');
    if (d) anchor_at = d.toISOString();
  }

  diagSubmitBtn.disabled = true;
  const pending = ToastUtils.show(`서버 진단 발행 중 (${rows.length}대)...`, 'pending');
  try {
    const res = await fetch('/api/v1/diagnostics', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scope: 'server',
        server_ids: rows.map(r => r.dataset.publicId),
        time_range,
        anchor_at,
      }),
    });
    pending.remove();
    if (!res.ok) {
      const detail = await res.text();
      ToastUtils.show(`서버 진단 발행 실패 (HTTP ${res.status}): ${detail}`, 'err');
      return;
    }
    const data = await res.json();
    const ids = (data.job_ids || []).join(',');
    if (!ids) {
      ToastUtils.show('발행 결과 없음', 'err');
      return;
    }
    // 결과 페이지로 이동 — 페이지에서 N개 polling
    window.location.href = `/diagnostics?ids=${encodeURIComponent(ids)}`;
  } catch (e) {
    pending.remove();
    ToastUtils.show('서버 진단 요청 실패: ' + e.message, 'err');
  } finally {
    diagSubmitBtn.disabled = false;
  }
}

if (diagBtn) {
  diagBtn.addEventListener('click', showDiagModal);
  diagCloseBtn.addEventListener('click', hideDiagModal);
  diagModal.addEventListener('click', e => { if (e.target === diagModal) hideDiagModal(); });
  diagSubmitBtn.addEventListener('click', submitDiag);
}
