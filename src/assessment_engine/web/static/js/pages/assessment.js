// @ts-check
/**
 * 환경 자원 평가 페이지(assessment.html) — 윈도우/앵커 변경 시 결과 partial fetch + swap + 자원 부족 clip.
 *
 * select(윈도우)·datetime-local(앵커) 변경 -> ?fragment=result fetch -> #assessment-result innerHTML 교체.
 * 자원 부족: 데이터는 전체 렌더, 20개 초과 시 21+ 행 숨김 + 더보기/접기 토글 (표시 디테일만 JS, P3 서버 렌더 유지).
 * 외부 의존: chart-utils.js(ChartUtils.initAnchor).
 */
(function () {
  const rangeSel = document.getElementById('assess-range');
  const anchorInput = document.getElementById('assess-anchor');
  const result = /** @type {HTMLElement} */ (document.getElementById('assessment-result'));
  if (!rangeSel || !result) return;

  // 앵커 datetime-local 초기값 — 현재 시각(KST). 비우면 서버가 현재 기준 평가.
  if (anchorInput && window.ChartUtils && /** @type {any} */ (window.ChartUtils).initAnchor) {
    // globals.d.ts 의 initAnchor 선언(onChange 콜백)과 실제 시그니처(inputId 문자열)가 어긋나 로컬 캐스트.
    /** @type {any} */ (window.ChartUtils).initAnchor('assess-anchor');
  }

  // --- 자원 부족 20개 clip + 더보기/접기 ----------------------------------
  const UNDER_SHOWN = 20;
  let underExpanded = false;

  function applyUnderClip() {
    const wrap = result.querySelector('#under-wrap');
    const moreWrap = /** @type {HTMLElement | null} */ (result.querySelector('#under-more-wrap'));
    const btn = result.querySelector('#under-more-btn');
    if (!wrap) return;
    // 화면 표만 — action_targets_table 은 print-only 복제표 2개를 더 렌더한다. #under-wrap 감싸는 section 이
    // .no-print 라 '.no-print tbody tr' 은 조상 매칭으로 print 표까지 잡힌다(3배). 화면 표만 가진 .sortable-table 로 스코프.
    const table = /** @type {HTMLElement | null} */ (wrap.querySelector('.sortable-table'));
    const rows = /** @type {HTMLElement[]} */ (Array.from(wrap.querySelectorAll('.sortable-table tbody tr'))); // 서버당 1 행
    const serverCount = rows.length;
    if (serverCount <= UNDER_SHOWN) {
      if (moreWrap) moreWrap.style.display = 'none';
      rows.forEach((el) => { el.style.display = ''; });
    } else {
      if (moreWrap) moreWrap.style.display = '';
      const limit = underExpanded ? serverCount : UNDER_SHOWN;
      rows.forEach((el, i) => { el.style.display = i < limit ? '' : 'none'; });
      // 서버목록(list-table.js) '전체보기 (CLIP/total)' / '접기' 문구와 동기화.
      if (btn) btn.textContent = underExpanded ? '접기' : `전체보기 (${UNDER_SHOWN}/${serverCount})`;
    }
    // 보이는 행만 zebra 재줄무늬 — clip 으로 숨은 행 제외(흰색 어긋남 방지, table-utils).
    if (window.TableUtils && table) window.TableUtils.restripe(table);
  }

  // --- 칼럼 클릭 정렬 (조치 대상 표) — 공용 TableUtils(정렬·zebra 단일화). 정렬 후 clip 재적용(restripe 포함). ---
  // 위임 (fragment swap 으로 요소가 새로 생겨도 동작) — 더보기/접기 + 칼럼 정렬.
  result.addEventListener('click', function (e) {
    const target = /** @type {Element | null} */ (e.target);
    if (target && target.id === 'under-more-btn') {
      underExpanded = !underExpanded;
      applyUnderClip();
      return;
    }
    const th = target && target.closest && target.closest('th.sort-col');
    if (th) {
      const table = th.closest('table.sortable-table');
      if (!table) return;
      if (!th.parentNode) return;
      const idx = Array.from(th.parentNode.children).indexOf(th);
      window.TableUtils.sortByColumn(/** @type {HTMLElement} */ (table), idx);
      underExpanded = false;
      applyUnderClip();
    }
  });

  // --- 윈도우/앵커 변경 -> 결과 partial swap -------------------------------
  let seq = 0;
  async function refresh() {
    const params = new URLSearchParams();
    params.set('fragment', 'result');
    params.set('time_range', /** @type {HTMLSelectElement} */ (rangeSel).value);
    const anchor = anchorInput ? /** @type {HTMLInputElement} */ (anchorInput).value : '';
    if (anchor) params.set('anchor_at', anchor + ':00+09:00');
    const mySeq = ++seq; // capture-before-await — 더 늦은 요청이 우선
    // 로딩 표시 — 윈도우/앵커 변경 즉시 (재계산이 길 수 있어 진행 중임을 명시).
    result.innerHTML = '<div class="empty-state">자원 평가를 불러오는 중…</div>';
    try {
      const res = await fetch('/environment/assessment?' + params.toString());
      if (mySeq !== seq) return; // stale 응답 무시
      if (!res.ok) { result.innerHTML = '<div class="empty-state">자원 평가를 불러오지 못했습니다. 다시 시도해 주세요.</div>'; return; }
      result.innerHTML = await res.text();
      underExpanded = false; // 새 결과 — 접힌 상태로 시작
      applyUnderClip();
      // 'N대 기준' 뱃지 갱신 (윈도우별 평가 대상 수 변동).
      const totalEl = /** @type {HTMLElement | null} */ (result.querySelector('#assess-total'));
      const badge = document.getElementById('assess-total-badge');
      if (totalEl && badge) badge.textContent = /** @type {string} */ (totalEl.dataset.total) + '대 기준';
    } catch (e) {
      if (mySeq === seq) result.innerHTML = '<div class="empty-state">자원 평가를 불러오는 중 오류가 발생했습니다.</div>';
    }
  }

  rangeSel.addEventListener('change', refresh);
  if (anchorInput) anchorInput.addEventListener('change', refresh);

  applyUnderClip(); // 초기 로드 clip
})();
