// 보고서 narrative inline polling (engineer 보고서 전용).
//
// 발행된 engineer 보고서는 worker 가 narrative 를 채울 때까지 job status=pending. 본 스크립트는
// 페이지 안 단일 [data-report-job] 의 job_id 를 GET /api/diagnostics/{job_id} 로 polling,
// job status=succeeded 시 result.narratives 를 읽어 각 [data-narrative-slot][data-narrative-key] cell 을 채운다.
//
// narratives = { key: {narrative, classification_label_kr, recommendation_action, status, error} }
//   server scope: key=public_id (행별) · environment scope: key="environment" (단일 slot).
//
// 흐름:
// 1. [data-report-job] 에서 job_id + status. 없거나 status 가 succeeded/failed/none 이면 종료 (정적 렌더 완료).
// 2. 3초마다 GET /api/diagnostics/{job_id}.
// 3. item.status (job status):
//    - succeeded → item.result.narratives 로 각 slot 채움 + 중단.
//    - failed    → 모든 slot "진단 실패" + 중단.
//    - pending/running → 다음 tick.
// 4. 404 (job 미존재) → 중단. 5분 cap.

(function () {
  const POLL_INTERVAL_MS = 3000;
  const POLL_TIMEOUT_MS = 5 * 60 * 1000;

  const jobEl = document.querySelector('[data-report-job]');
  if (!jobEl) return;
  // capture-before-await — 폴링 중 DOM 교체 없으므로 job_id 는 시작 시 1회 고정.
  const jobId = jobEl.getAttribute('data-job-id');
  const status = jobEl.getAttribute('data-status');
  if (!jobId || status === 'succeeded' || status === 'failed' || status === 'none') return;

  function slots() {
    return document.querySelectorAll('[data-narrative-slot]');
  }

  function renderEntry(el, entry) {
    const body = el.querySelector('.diag-inline-body');
    const meta = el.querySelector('.diag-inline-meta');
    if (entry && entry.status === 'succeeded' && entry.narrative) {
      if (body) {
        body.textContent = entry.narrative;
        // 다중 보고서 안 .diag-clamp 활성 시 title 호버로 전체 narrative (시각 truncate 보완).
        if (body.classList.contains('diag-clamp')) body.setAttribute('title', entry.narrative);
      }
      // narrative 만 표시 — 분류·권장액션은 표시 안 함 (모든 보고서 AI 카드 양식 통일).
      if (meta) meta.innerHTML = '';
    } else {
      const reason = entry && entry.error ? ' (' + entry.error + ')' : '';
      if (body) body.textContent = '진단 실패' + reason;
      if (meta) meta.innerHTML = '';
    }
  }

  function fillNarratives(narratives) {
    slots().forEach(function (el) {
      const key = el.getAttribute('data-narrative-key');
      renderEntry(el, narratives ? narratives[key] : null);
    });
  }

  function failAll(reason) {
    slots().forEach(function (el) {
      const body = el.querySelector('.diag-inline-body');
      if (body) body.textContent = '진단 실패' + (reason ? ' (' + reason + ')' : '');
      const meta = el.querySelector('.diag-inline-meta');
      if (meta) meta.innerHTML = '';
    });
  }

  async function pollOnce() {
    const res = await fetch('/api/diagnostics/' + encodeURIComponent(jobId), { credentials: 'same-origin' });
    if (!res.ok) {
      if (res.status === 404) return 'stop';
      throw new Error('poll http ' + res.status);
    }
    const item = await res.json();
    if (item.status === 'succeeded') {
      const narratives = (item.result && item.result.narratives) || {};
      fillNarratives(narratives);
      return 'stop';
    }
    if (item.status === 'failed') {
      failAll(item.error_message || null);
      return 'stop';
    }
    return 'continue';
  }

  async function startPolling() {
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    while (Date.now() < deadline) {
      try {
        if ((await pollOnce()) === 'stop') return;
      } catch (err) {
        // 일시 장애 = 다음 tick 재시도
        console.warn('report narrative poll error', err);
      }
      await new Promise(function (resolve) { setTimeout(resolve, POLL_INTERVAL_MS); });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startPolling);
  } else {
    startPolling();
  }
})();
