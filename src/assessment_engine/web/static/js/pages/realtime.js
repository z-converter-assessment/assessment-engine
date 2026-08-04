// @ts-check
/**
 * 실시간 현황 30초 자동 갱신 — fragment=realtime fetch 후 #rt-mount innerHTML 교체.
 *
 * mount(#rt-mount) = 현재 자원 현황 + 서버별 실시간 부하(최신 스냅샷, sortable-table). 운영 신호는 폴링 밖
 * (느린 신호, full-page 정적). 선택 N대(ids)는 body data-selection-ids 로 보존. fail-soft — 네트워크 오류 시
 * 현재 화면 유지.
 *
 * 칼럼 정렬 + 20개 초과 더보기/접기 — assessment.js(자원 부족 표)와 동일 관례. mount 는 30초마다 innerHTML
 * 통째로 교체되므로 클릭 위임을 안 바뀌는 mount 자체에 건다(swap 후에도 리스너 유지).
 */
(function () {
  const mount = /** @type {HTMLElement} */ (document.getElementById('rt-mount'));
  if (!mount) return;
  const ids = document.body.dataset.selectionIds || '';
  const url = '/environment/realtime?fragment=realtime' + (ids ? '&ids=' + encodeURIComponent(ids) : '');

  // --- 서버별 실시간 부하 20개 clip + 더보기/접기 --------------------------
  const LOAD_SHOWN = 20;
  let loadExpanded = false;

  function applyLoadClip() {
    const wrap = mount.querySelector('#load-wrap');
    const moreWrap = /** @type {HTMLElement | null} */ (mount.querySelector('#load-more-wrap'));
    const btn = mount.querySelector('#load-more-btn');
    if (!wrap) return;
    const table = /** @type {HTMLElement | null} */ (wrap.querySelector('.sortable-table'));
    const rows = /** @type {HTMLElement[]} */ (Array.from(wrap.querySelectorAll('.sortable-table tbody tr')));
    const serverCount = rows.length;
    if (serverCount <= LOAD_SHOWN) {
      if (moreWrap) moreWrap.style.display = 'none';
      rows.forEach((el) => { el.style.display = ''; });
    } else {
      if (moreWrap) moreWrap.style.display = '';
      const limit = loadExpanded ? serverCount : LOAD_SHOWN;
      rows.forEach((el, i) => { el.style.display = i < limit ? '' : 'none'; });
      if (btn) btn.textContent = loadExpanded ? '접기' : `전체보기 (${LOAD_SHOWN}/${serverCount})`;
    }
    if (window.TableUtils && table) window.TableUtils.restripe(table);
  }

  // 위임 — 30초 swap 으로 요소가 새로 생겨도 mount 자체는 안 바뀌어 리스너 유지.
  mount.addEventListener('click', function (e) {
    const target = /** @type {Element | null} */ (e.target);
    if (target && target.id === 'load-more-btn') {
      loadExpanded = !loadExpanded;
      applyLoadClip();
      return;
    }
    const th = target && target.closest && target.closest('th.sort-col');
    if (th) {
      const table = th.closest('table.sortable-table');
      if (!table || !th.parentNode) return;
      const idx = Array.from(th.parentNode.children).indexOf(th);
      window.TableUtils.sortByColumn(/** @type {HTMLElement} */ (table), idx);
      loadExpanded = false;
      applyLoadClip();
    }
  });

  let seq = 0;
  async function refresh() {
    if (!mount) return;
    const mySeq = ++seq; // capture-before-await — 더 늦은 요청이 우선
    try {
      const res = await fetch(url);
      if (mySeq !== seq) return; // stale 응답 무시
      if (!res.ok) return;
      mount.innerHTML = await res.text();
      loadExpanded = false; // 새 스냅샷 — 접힌 상태로 시작
      applyLoadClip();
    } catch (e) {
      // 네트워크 일시 오류 — 현재 화면 유지 (fail-soft)
    }
  }

  applyLoadClip(); // 초기 로드 clip
  ChartUtils.initAutoRefresh(refresh, 30000);  // 탭 비활성 시 일시정지 (chart-utils 단일 진실)
})();
