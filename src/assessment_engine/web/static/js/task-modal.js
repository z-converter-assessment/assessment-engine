// Task 상세 modal + polling — base.html 의 #task-modal 요소를 채운다.
//
// 운영자 워크플로:
//   list.html "최근 작업" cell 클릭 / detail.html timeline row 클릭
//   -> data-task-id 속성에서 ID 추출 -> GET /api/tasks/{id} -> modal 채움
//
// install 발행 직후 status='pending' 인 task 를 polling — TaskModal.pollUntilFinal(taskId, cb).
// 각 호출자(list.js)가 polling 결과로 cell 시각 갱신.
//
// P4 client 연산 제한 — 표시 파생(badge_class·badge_label·failure_label)은 모두 server mapper precompute.
// 본 JS 는 fetch + DOM 삽입만.

(function () {
  const modal     = document.getElementById('task-modal');
  if (!modal) return;
  const closeBtn  = document.getElementById('task-modal-close');
  const titleEl   = document.getElementById('task-modal-title');
  const bodyEl    = document.getElementById('task-modal-body');

  function open() {
    modal.style.display = 'flex';
  }
  function close() {
    modal.style.display = 'none';
  }

  closeBtn.addEventListener('click', close);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && modal.style.display === 'flex') close();
  });

  // 페이지의 모든 [data-task-id] 요소 클릭 -> modal open. event delegation 으로 동적 추가도 처리.
  document.addEventListener('click', e => {
    const el = e.target.closest('[data-task-id]');
    if (!el) return;
    const taskId = el.dataset.taskId;
    if (!taskId) return;
    e.preventDefault();
    openTask(taskId);
  });

  function escapeHtml(s) {
    return (s || '').replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
  }

  function render(detail) {
    titleEl.textContent = `작업 ${detail.task_id.slice(0, 8)} — ${detail.target_hostname || '—'}`;
    const meta = `
      <div style="display:grid; grid-template-columns:120px 1fr; gap:6px 12px; margin-bottom:14px;">
        <div style="color:#64748b;">상태</div><div><span class="badge ${detail.badge_class}">${escapeHtml(detail.badge_label)}</span></div>
        <div style="color:#64748b;">유형</div><div>${escapeHtml(detail.task_type)}</div>
        <div style="color:#64748b;">발행</div><div>${escapeHtml(detail.created_at)}</div>
        <div style="color:#64748b;">완료</div><div>${detail.completed_at ? escapeHtml(detail.completed_at) : '—'}</div>
        <div style="color:#64748b;">exit_code</div><div>${detail.exit_code !== null ? detail.exit_code : '—'}</div>
        <div style="color:#64748b;">소요</div><div>${detail.duration_ms !== null ? detail.duration_ms + ' ms' : '—'}</div>
        <div style="color:#64748b;">실패 사유</div><div style="color:#b91c1c;">${detail.failure_label ? escapeHtml(detail.failure_label) : '—'}</div>
        ${detail.params && detail.params.zdm_ip ? `<div style="color:#64748b;">ZDM 주소</div><div><code style="font-size:11px; word-break:break-all;">${escapeHtml(detail.params.zdm_ip)}</code></div>` : ''}
        ${detail.params && detail.params.zdm_user ? `<div style="color:#64748b;">발행 유저 (ZDM)</div><div><code style="font-size:11px;">${escapeHtml(detail.params.zdm_user)}</code></div>` : ''}
      </div>`;
    const tail = (title, content) => `
      <div style="margin-bottom:10px;">
        <div style="font-size:12px; color:#64748b; margin-bottom:4px;">${title}</div>
        <pre style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:10px; font-size:11px; color:#1e293b; max-height:200px; overflow:auto; margin:0;">${escapeHtml(content) || '—'}</pre>
      </div>`;
    bodyEl.innerHTML = meta + tail('stdout tail (4KB)', detail.stdout_tail || '') + tail('stderr tail (4KB)', detail.stderr_tail || '');
  }

  async function fetchTask(taskId) {
    const res = await fetch(`/api/tasks/${encodeURIComponent(taskId)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  async function openTask(taskId) {
    bodyEl.innerHTML = '불러오는 중...';
    titleEl.textContent = '작업 상세';
    open();
    try {
      const detail = await fetchTask(taskId);
      render(detail);
    } catch (e) {
      bodyEl.innerHTML = `<p style="color:#b91c1c;">조회 실패: ${escapeHtml(e.message)}</p>`;
    }
  }

  // 발행 직후 task 가 status='pending' 인 동안 polling. final(success/failure) 도달 시 callback.
  // 호출자: list.js install 발행 후 응답 task_ids 각각에 대해 호출.
  // 최대 cap_seconds 동안 try, interval_ms 간격.
  async function pollUntilFinal(taskId, { intervalMs = 5000, capSeconds = 180, onUpdate } = {}) {
    const deadline = Date.now() + capSeconds * 1000;
    while (Date.now() < deadline) {
      try {
        const detail = await fetchTask(taskId);
        if (onUpdate) onUpdate(detail);
        if (detail.status === 'success' || detail.status === 'failure' || detail.status === 'failed') {
          return detail;
        }
      } catch (_) {
        // transient — 다음 tick 에서 재시도.
      }
      await new Promise(r => setTimeout(r, intervalMs));
    }
    return null;
  }

  window.TaskModal = { open: openTask, close, pollUntilFinal };
})();
