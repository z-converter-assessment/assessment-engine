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

  async function fetchTask(taskId) {
    const res = await fetch(`/api/tasks/${encodeURIComponent(taskId)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  // modal body 는 server fragment endpoint 가 partial HTML 반환 (P3 정공 — JS HTML 합성 폐기).
  // polling 은 위 fetchTask (JSON) 사용 — list cell 갱신 callback 전달 위해 JSON 필요.
  async function fetchTaskFragment(taskId) {
    const res = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/detail`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.text();
  }

  async function openTask(taskId) {
    bodyEl.innerHTML = '불러오는 중...';
    titleEl.textContent = '작업 상세';
    open();
    try {
      // title 은 JSON detail 의 task_id / target_hostname 필요 — JSON fetch 후 title set + fragment fetch.
      const detail = await fetchTask(taskId);
      titleEl.textContent = `작업 ${detail.task_id.slice(0, 8)} — ${detail.target_hostname || '—'}`;
      bodyEl.innerHTML = await fetchTaskFragment(taskId);
    } catch (e) {
      bodyEl.innerHTML = `<p class="text-danger">조회 실패: ${escapeHtml(e.message)}</p>`;
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
