// 보고서 비동기 생성 폴링 — pending/running job 상태를 주기 조회, succeeded/failed 되면 reload.
// P3 정공 예외(static-assets.md): P4 차트 아님, 완료 신호까지 1회 fetch 반복. 완료 시 같은 ?job= URL 이
// 스냅샷(succeeded)·실패 화면(failed)을 렌더하므로 location.reload() 로 전환.
(function () {
  const el = /** @type {HTMLElement | null} */ (document.querySelector('.report-pending'));
  if (!el) return;
  const jobId = el.dataset.jobId;
  // failed 는 이미 종료 상태 — 폴링 불필요.
  if (!jobId || el.dataset.status === 'failed') return;

  const INTERVAL_MS = 2000;

  async function poll() {
    try {
      // 상단 가드(!jobId 이면 early return)로 jobId 는 여기서 string 확정 — 클로저 narrowing 유실 보정.
      const res = await fetch(`/reports/${encodeURIComponent(/** @type {string} */ (jobId))}/status`, {
        headers: { Accept: 'application/json' },
      });
      if (res.ok) {
        // /reports/{job_id}/status 응답은 response_model 미선언이라 생성 api.ts 에서 unknown —
        // 폴링이 소비하는 status 필드만 로컬 shape 로 좁힌다.
        const data = /** @type {{ status?: string }} */ (await res.json());
        if (data.status === 'succeeded' || data.status === 'failed') {
          location.reload();
          return;
        }
      }
    } catch (e) {
      // 일시 네트워크 오류 — 다음 주기 재시도 (워커가 살아있으면 결국 완료).
    }
    setTimeout(poll, INTERVAL_MS);
  }

  setTimeout(poll, INTERVAL_MS);
})();
