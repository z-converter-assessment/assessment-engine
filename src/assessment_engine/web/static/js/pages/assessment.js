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
  const result = document.getElementById('assessment-result');
  if (!rangeSel || !result) return;

  // 앵커 datetime-local 초기값 — 현재 시각(KST). 비우면 서버가 현재 기준 평가.
  if (anchorInput && window.ChartUtils && window.ChartUtils.initAnchor) {
    window.ChartUtils.initAnchor('assess-anchor');
  }

  // ─── 자원 부족 20개 clip + 더보기/접기 ──────────────────────────────────
  const UNDER_SHOWN = 20;
  let underExpanded = false;

  function applyUnderClip() {
    const wrap = result.querySelector('#under-wrap');
    const moreWrap = result.querySelector('#under-more-wrap');
    const btn = result.querySelector('#under-more-btn');
    if (!wrap) return;
    const rows = Array.from(wrap.querySelectorAll('tbody tr')); // 서버당 1 표 행
    const serverCount = rows.length;
    if (serverCount <= UNDER_SHOWN) {
      if (moreWrap) moreWrap.style.display = 'none';
      rows.forEach((el) => { el.style.display = ''; });
      return;
    }
    if (moreWrap) moreWrap.style.display = '';
    const limit = underExpanded ? serverCount : UNDER_SHOWN;
    rows.forEach((el, i) => { el.style.display = i < limit ? '' : 'none'; });
    // 서버목록(list-table.js) '전체보기 (CLIP/total)' / '접기' 문구와 동기화.
    if (btn) btn.textContent = underExpanded ? '접기' : `전체보기 (${UNDER_SHOWN}/${serverCount})`;
  }

  // ─── 칼럼 클릭 정렬 (조치 대상 표) ──────────────────────────────────────
  // th.sort-col 클릭 -> 해당 칼럼 값으로 행 재배열(모양 유지). data-sort 있으면 그 값, 없으면 셀 텍스트.
  // 숫자(%, ms, 순위)는 수치 비교, 그 외는 한국어 문자열. 같은 칼럼 재클릭은 방향 토글. 정렬 후 clip 재적용.
  function numOf(s) {
    const n = parseFloat(String(s).replace(/[^0-9.\-]/g, ''));
    return s === '' || isNaN(n) ? null : n;
  }
  function sortTable(table, idx) {
    const tbody = table.querySelector('tbody');
    if (!tbody) return;
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const asc = String(table.dataset.sortCol) === String(idx) ? table.dataset.sortAsc !== 'true' : true;
    const keyOf = function (row) {
      const cell = row.children[idx];
      if (!cell) return '';
      return cell.dataset.sort !== undefined ? cell.dataset.sort : cell.textContent.trim();
    };
    rows.sort(function (a, b) {
      const ka = keyOf(a), kb = keyOf(b);
      const na = numOf(ka), nb = numOf(kb);
      let cmp;
      if (na !== null && nb !== null) cmp = na - nb;
      else if (na !== null) cmp = -1; // 숫자가 텍스트/빈값보다 앞
      else if (nb !== null) cmp = 1;
      else cmp = ka.localeCompare(kb, 'ko');
      return asc ? cmp : -cmp;
    });
    rows.forEach(function (r) { tbody.appendChild(r); });
    table.dataset.sortCol = idx;
    table.dataset.sortAsc = asc;
  }

  // 위임 (fragment swap 으로 요소가 새로 생겨도 동작) — 더보기/접기 + 칼럼 정렬.
  result.addEventListener('click', function (e) {
    if (e.target && e.target.id === 'under-more-btn') {
      underExpanded = !underExpanded;
      applyUnderClip();
      return;
    }
    const th = e.target.closest && e.target.closest('th.sort-col');
    if (th) {
      const table = th.closest('table.sortable-table');
      if (!table) return;
      const idx = Array.from(th.parentNode.children).indexOf(th);
      sortTable(table, idx);
      // 방향 표식 — 활성 칼럼만 sort-asc/desc, 나머지 해제.
      table.querySelectorAll('th.sort-col').forEach(function (h) { h.classList.remove('sort-asc', 'sort-desc'); });
      th.classList.add(table.dataset.sortAsc === 'true' ? 'sort-asc' : 'sort-desc');
      underExpanded = false;
      applyUnderClip();
    }
  });

  // ─── 윈도우/앵커 변경 -> 결과 partial swap ───────────────────────────────
  let seq = 0;
  async function refresh() {
    const params = new URLSearchParams();
    params.set('fragment', 'result');
    params.set('time_range', rangeSel.value);
    const anchor = anchorInput ? anchorInput.value : '';
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
      const totalEl = result.querySelector('#assess-total');
      const badge = document.getElementById('assess-total-badge');
      if (totalEl && badge) badge.textContent = totalEl.dataset.total + '대 기준';
    } catch (e) {
      if (mySeq === seq) result.innerHTML = '<div class="empty-state">자원 평가를 불러오는 중 오류가 발생했습니다.</div>';
    }
  }

  rangeSel.addEventListener('change', refresh);
  if (anchorInput) anchorInput.addEventListener('change', refresh);

  applyUnderClip(); // 초기 로드 clip
})();
