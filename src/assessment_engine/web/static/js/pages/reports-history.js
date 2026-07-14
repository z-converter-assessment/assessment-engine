// @ts-check
// 보고서 이력 즉시 필터 + 더보기 — 서버 목록 (list-table.js) 와 동일 UX 의도.
// form change → fetch HTML fragment → 결과 영역 (#report-history-results) 교체 + URL replaceState.
// "더보기" → 전체보기(limit=total)로 fragment 재조회 후 교체, "접기" → 첫 페이지(20건) 복귀 (page reload 없음). shown/total 카운트는 서버 precompute.

(function () {
  const form = /** @type {HTMLFormElement} */ (document.getElementById('report-history-filter'));
  const resultsEl = /** @type {HTMLElement} */ (document.getElementById('report-history-results'));
  if (!form || !resultsEl) return;

  let inflight = 0;

  // fragment 교체 후 정렬 부여 — 표가 매 swap 마다 새로 생기므로 재부여. 공용 TableUtils(정렬·zebra 단일화, 흰색 버그 방지).
  function wireTable() {
    const table = /** @type {HTMLElement | null} */ (resultsEl.querySelector('table.sortable-table'));
    if (table && window.TableUtils) window.TableUtils.makeSortable(table);
  }

  // limit 미지정(필터 변경) 이면 기본 20 으로 리셋. 더보기는 누적 limit 전달.
  /** @param {number} [limit] */
  async function applyFilters(limit) {
    const params = new URLSearchParams(/** @type {any} */ (new FormData(form)));
    // empty 값 제거 (URL 깔끔)
    for (const [k, v] of Array.from(params.entries())) {
      if (!v) params.delete(k);
    }
    // limit 은 기본(20) 초과일 때만 URL 에 노출 — 새로고침 시 SSR 가 같은 누적분 렌더.
    if (limit && limit > 20) {
      params.set('limit', String(limit));
    } else {
      params.delete('limit');
    }
    const qs = params.toString();
    const newUrl = qs ? `${location.pathname}?${qs}` : location.pathname;
    history.replaceState(null, '', newUrl);

    // fragment fetch — server 가 partial HTML 만 반환.
    const seq = ++inflight;
    try {
      const res = await fetch(`${location.pathname}?${qs}${qs ? '&' : ''}fragment=1`);
      if (seq !== inflight) return;  // 최신 요청만 적용
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      resultsEl.innerHTML = await res.text();
      wireTable();  // 새 표에 정렬 재부여
    } catch (e) {
      if (window.ToastUtils) ToastUtils.show('보고서 이력 fetch 실패: ' + (/** @type {Error} */ (e)).message, 'err');
    }
  }

  // 필터 변경 — limit 리셋(첫 20건).
  form.querySelectorAll('select, input[type=checkbox], input[type=text]').forEach(el => {
    el.addEventListener('change', () => applyFilters());
  });
  form.addEventListener('submit', e => {
    e.preventDefault();
    applyFilters();
  });

  // 더보기/접기 — 결과 영역은 매 fetch 로 재생성되므로 이벤트 위임.
  resultsEl.addEventListener('click', e => {
    const target = /** @type {Element} */ (e.target);
    const more = /** @type {HTMLElement | null} */ (target.closest('#history-more-btn'));
    if (more) { applyFilters(parseInt(/** @type {string} */ (more.dataset.nextLimit), 10)); return; }
    // 접기 — 첫 페이지(20건) 로 복귀.
    if (target.closest('#history-collapse-btn')) { applyFilters(); return; }
  });

  wireTable();  // 초기(SSR) 표 정렬 부여
})();
