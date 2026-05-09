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

function showModal() {
  modal.style.display = 'flex';
  ipInput.focus();
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
  const ip     = ipInput.value.trim();
  const port   = parseInt(portInput.value, 10);
  const scheme = schemeInput.value;

  if (!ip) { renderResult(null, 'IP를 입력하세요'); return; }
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
      body: JSON.stringify({ ip, port, scheme }),
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
// 체크박스로 서버 선택 → ZDM IP 입력 → POST /api/v1/tasks/install.
// engine은 DB INSERT + Redis pending SET. agent가 다음 metrics 발행 시 RPC piggyback으로 명령 수신.

const installModal     = document.getElementById('install-modal');
const installBtn       = document.getElementById('install-btn');
const installCloseBtn  = document.getElementById('install-close');
const installSubmitBtn = document.getElementById('install-submit');
const installCountEl   = document.getElementById('install-count');
const installZdmInput  = document.getElementById('install-zdm');
const installResultEl  = document.getElementById('install-result');
const selectAllCb      = document.getElementById('select-all');

function selectedRows() {
  return [...document.querySelectorAll('.row-select:checked')];
}

function refreshInstallButton() {
  const n = selectedRows().length;
  installBtn.textContent = `ZConverter Install (${n})`;
  installBtn.disabled = n === 0;
  installBtn.style.opacity = n === 0 ? '0.5' : '1';
  exportBtn.textContent = `JSON Export (${n})`;
  exportBtn.disabled = n === 0;
  exportBtn.style.opacity = n === 0 ? '0.5' : '1';
  reportBtn.textContent = `보고서 (${n})`;
  reportBtn.disabled = n === 0;
  reportBtn.style.opacity = n === 0 ? '0.5' : '1';
}

const reportBtn = document.getElementById('report-btn');

function openReport() {
  const rows = selectedRows();
  if (!rows.length) return;
  const ids = rows.map(r => r.dataset.publicId).join(',');
  // 새 탭 — 큰 N이면 URL 길이 한계. 일단 GET (assessment-deliverables 1차 구현)
  window.open(`/servers/report?ids=${encodeURIComponent(ids)}`, '_blank');
}

reportBtn.addEventListener('click', openReport);

const exportBtn = document.getElementById('export-btn');

async function exportInventory() {
  const rows = selectedRows();
  if (!rows.length) return;
  exportBtn.disabled = true;
  try {
    const res = await fetch('/api/v1/exports/inventory', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_public_ids: rows.map(r => r.dataset.publicId) }),
    });
    if (!res.ok) {
      alert(`Export 실패 (HTTP ${res.status})`);
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
  } catch (e) {
    alert('Export 실패: ' + e.message);
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
  installModal.style.display = 'flex';
  installZdmInput.focus();
  installResultEl.style.display = 'none';
  installResultEl.innerHTML = '';
}

function hideInstallModal() {
  installModal.style.display = 'none';
}

function renderInstallResult(message, kind) {
  // kind: 'ok' | 'err' | 'pending'
  installResultEl.style.display = '';
  const styles = {
    ok:      { bg:'#f0fdf4', color:'#166534', border:'#bbf7d0' },
    err:     { bg:'#fef2f2', color:'#991b1b', border:'#fecaca' },
    pending: { bg:'#f1f5f9', color:'#64748b', border:'#e2e8f0' },
  }[kind] || {};
  installResultEl.style.background = styles.bg;
  installResultEl.style.color      = styles.color;
  installResultEl.style.border     = '1px solid ' + styles.border;
  installResultEl.innerHTML = message;
}

async function submitInstall() {
  const zdm = installZdmInput.value.trim();
  const rows = selectedRows();
  if (!zdm) { renderInstallResult('❌ ZDM 주소를 입력하세요', 'err'); return; }
  if (rows.length === 0) { renderInstallResult('❌ 선택된 서버 없음', 'err'); return; }

  renderInstallResult('⏳ 발행 중...', 'pending');
  installSubmitBtn.disabled = true;

  try {
    const res = await fetch('/api/v1/tasks/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target_public_ids: rows.map(r => r.dataset.publicId),
        zdm_ip: zdm,
      }),
    });
    if (!res.ok) {
      const detail = await res.text();
      renderInstallResult(`❌ 발행 실패 (HTTP ${res.status}): ${detail}`, 'err');
      return;
    }
    const data = await res.json();
    const list = Array.isArray(data) ? data : [];   // 5xx가 JSON object 반환 시 TypeError 방어
    const lines = list.map(t => `· ${rows.find(r => r.dataset.publicId === t.target_public_id)?.dataset.hostname || t.target_public_id} → task ${t.task_public_id.slice(0, 8)}…`);
    renderInstallResult(`✅ ${list.length}건 발행 완료<br><div style="margin-top:6px; font-family:monospace; font-size:12px;">${lines.join('<br>')}</div>`, 'ok');
  } catch (e) {
    renderInstallResult('❌ 요청 실패: ' + e.message, 'err');
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
installZdmInput.addEventListener('keypress', e => { if (e.key === 'Enter') submitInstall(); });
