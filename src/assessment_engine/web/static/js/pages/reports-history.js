// 보고서 이력 즉시 필터 — 서버 목록 (list.js) 와 동일 UX 의도.
// form change → fetch HTML fragment → tbody/footer 영역 (#report-history-results) 교체 + URL pushState.
// page reload 없음. server-side filter 흐름 유지 (deep link / 새로고침 시 SSR 정공).

(function () {
  const form = document.getElementById('report-history-filter');
  const resultsEl = document.getElementById('report-history-results');
  if (!form || !resultsEl) return;

  let inflight = 0;

  async function applyFilters() {
    const params = new URLSearchParams(new FormData(form));
    // empty 값 제거 (URL 깔끔)
    for (const [k, v] of Array.from(params.entries())) {
      if (!v) params.delete(k);
    }
    // URL 갱신 — deep link / 새로고침 시 server-side SSR 가 같은 query 받음.
    const qs = params.toString();
    const newUrl = qs ? `${location.pathname}?${qs}` : location.pathname;
    history.replaceState(null, '', newUrl);

    // fragment fetch — server 가 partial HTML 만 반환.
    const seq = ++inflight;
    try {
      const res = await fetch(`${location.pathname}?${qs}&fragment=1`);
      if (seq !== inflight) return;  // 최신 요청만 적용
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const html = await res.text();
      resultsEl.innerHTML = html;
    } catch (e) {
      if (window.ToastUtils) ToastUtils.show('보고서 이력 fetch 실패: ' + e.message, 'err');
    }
  }

  form.querySelectorAll('select, input[type=checkbox], input[type=text]').forEach(el => {
    el.addEventListener('change', applyFilters);
  });
  form.addEventListener('submit', e => {
    e.preventDefault();
    applyFilters();
  });
})();
