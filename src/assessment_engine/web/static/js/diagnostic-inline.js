// 보고서 페이지 안 AI 진단 inline polling (engineer view 전용).
//
// 본 스크립트는 `[data-diagnostic-inline][data-job-id="..."]` 요소를 수집해 본 페이지 안에서 그대로
// 결과를 갱신한다. 별도 결과 페이지 이동 없음 — 보고서 = 진단 narrative inline 통합.
//
// 흐름:
// 1. DOM 안 `[data-diagnostic-inline][data-job-id]` 요소 수집 (빈 job-id 제외 = 이미 succeeded)
// 2. 3초마다 `/api/diagnostics?ids=<job_ids>` GET — 본 job_ids 전체 batch query
// 3. job 별 status 확인:
//    - succeeded + narrative 있음 → 본 cell 안 `.diag-inline-body` + `.diag-inline-meta` DOM 교체
//    - pending / running → 진행 중 표시 유지 (다음 tick 재polling)
//    - failed → "진단 실패" 표시 + polling 중단
// 4. 모든 pending job 종료 시 polling stop. 5분 cap.
//
// data-job-id 가 비어 있으면 = 이미 succeeded narrative 표시 중 (SSR 안 완료). 본 cell 무시.

(function () {
  const POLL_INTERVAL_MS = 3000;
  const POLL_TIMEOUT_MS = 5 * 60 * 1000;

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function collectPendingCells() {
    const cells = document.querySelectorAll('[data-diagnostic-inline][data-job-id]');
    const out = [];
    cells.forEach(function (el) {
      const jobId = el.getAttribute('data-job-id');
      if (jobId && jobId.length > 0) {
        out.push({ el: el, jobId: jobId });
      }
    });
    return out;
  }

  function renderSuccess(el, panel) {
    const body = el.querySelector('.diag-inline-body');
    const meta = el.querySelector('.diag-inline-meta');
    if (body) {
      const text = panel.narrative || '';
      body.textContent = text;
      // 다중 보고서 안 .diag-clamp 활성 시 title 호버 tooltip 으로 전체 narrative 표시 (시각 truncate 보완).
      if (body.classList.contains('diag-clamp')) body.setAttribute('title', text);
    }
    if (meta) {
      const parts = [];
      const cls = panel.classification_label_kr;
      const action = panel.recommendation_action;
      if (cls) parts.push('분류: <strong>' + escapeHtml(cls) + '</strong>');
      if (action) parts.push('권장 액션: <strong>' + escapeHtml(action) + '</strong>');
      meta.innerHTML = parts.join(' · ');
    }
    el.removeAttribute('data-job-id');
  }

  function renderFailure(el, reason) {
    const body = el.querySelector('.diag-inline-body');
    if (body) body.textContent = '진단 실패' + (reason ? ' (' + reason + ')' : '');
    el.removeAttribute('data-job-id');
  }

  async function pollOnce(pending) {
    const ids = pending.map(function (p) { return p.jobId; }).join(',');
    const res = await fetch('/api/diagnostics?ids=' + encodeURIComponent(ids), { credentials: 'same-origin' });
    if (!res.ok) throw new Error('poll http ' + res.status);
    const payload = await res.json();
    const byId = {};
    if (Array.isArray(payload)) {
      payload.forEach(function (item) {
        if (item && item.job_id) byId[item.job_id] = item;
      });
    }
    pending.forEach(function (p) {
      const item = byId[p.jobId];
      if (!item) return;
      const status = item.status;
      if (status === 'succeeded') {
        renderSuccess(p.el, item);
      } else if (status === 'failed') {
        renderFailure(p.el, item.error_message || null);
      }
      // pending / running = 다음 tick 재polling (DOM 유지)
    });
  }

  async function startPolling() {
    let pending = collectPendingCells();
    if (pending.length === 0) return;
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    while (Date.now() < deadline) {
      try {
        await pollOnce(pending);
      } catch (err) {
        // 일시 장애 = 다음 tick 재시도
        console.warn('diagnostic-inline poll error', err);
      }
      pending = collectPendingCells();
      if (pending.length === 0) return;
      await new Promise(function (resolve) { setTimeout(resolve, POLL_INTERVAL_MS); });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startPolling);
  } else {
    startPolling();
  }
})();
