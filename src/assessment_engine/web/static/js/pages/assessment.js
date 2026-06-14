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
    if (btn) btn.textContent = underExpanded ? '접기' : `더보기 (${serverCount - UNDER_SHOWN}개 더)`;
  }

  // 더보기/접기 — 위임 (fragment swap 으로 버튼이 새로 생겨도 동작).
  result.addEventListener('click', function (e) {
    if (e.target && e.target.id === 'under-more-btn') {
      underExpanded = !underExpanded;
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
    try {
      const res = await fetch('/environment/assessment?' + params.toString());
      if (mySeq !== seq) return; // stale 응답 무시
      if (!res.ok) return;
      result.innerHTML = await res.text();
      underExpanded = false; // 새 결과 — 접힌 상태로 시작
      applyUnderClip();
      // 'N대 기준' 뱃지 갱신 (윈도우별 평가 대상 수 변동).
      const totalEl = result.querySelector('#assess-total');
      const badge = document.getElementById('assess-total-badge');
      if (totalEl && badge) badge.textContent = totalEl.dataset.total + '대 기준';
    } catch (e) {
      // 네트워크 일시 오류 — 현재 화면 유지 (fail-soft).
    }
  }

  rangeSel.addEventListener('change', refresh);
  if (anchorInput) anchorInput.addEventListener('change', refresh);

  applyUnderClip(); // 초기 로드 clip
})();
