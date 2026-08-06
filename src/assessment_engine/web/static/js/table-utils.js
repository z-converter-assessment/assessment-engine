
/* 표 정렬·zebra 줄무늬 공용 유틸 (TableUtils).
 *
 * 문제: 정렬로 행을 재배열하거나 필터/clip 으로 숨기면, CSS `:nth-child(even)` zebra 가 숨은 행까지 세어
 * 보이는 행의 줄무늬가 어긋난다(흰색이 뒤섞임). 이 로직이 서버목록·자원적정성 두 곳에 복붙돼 있어
 * 발행이력 등에 정렬을 붙일 때마다 같은 버그가 재발했다.
 *
 * 해결: sortable-table 은 CSS :nth-child 대신 JS 가 '보이는 행'만 .zebra 로 재줄무늬(restripe) — 정렬·필터·
 * clip 후 호출. makeSortable 로 칼럼 클릭 정렬을 한 줄로 부여(정렬 로직 단일화).
 *
 * TableUtils.restripe(table)              — 보이는(display!=none) tbody 행만 짝수번째 .zebra 부여.
 * TableUtils.sortByColumn(table, idx)     — idx 칼럼 값으로 행 재배열(방향 토글) + th 표식 + restripe.
 * TableUtils.makeSortable(table, onAfter) — thead th.sort-col 클릭 정렬 위임 부여 + 초기 restripe.
 *                                            onAfter(table): 정렬 후 필터·clip 재적용 훅(그쪽에서 restripe 재호출).
 */

/** @param {string} s */
function numOf(s) {
  const n = parseFloat(String(s).replace(/[^0-9.\-]/g, ''));
  return s === '' || isNaN(n) ? null : n;
}

/** @param {HTMLElement | null} table */
function restripe(table) {
  const tbody = table && table.querySelector('tbody');
  if (!tbody) return;
  let i = 0;
  for (const tr of /** @type {HTMLCollectionOf<HTMLElement>} */ (tbody.children)) {
    if (tr.tagName !== 'TR') continue;
    if (tr.style.display === 'none') { tr.classList.remove('zebra'); continue; } // 숨은 행은 계산에서 제외
    tr.classList.toggle('zebra', i % 2 === 1);
    i++;
  }
}

/**
 * @param {HTMLElement} table
 * @param {number} idx
 */
function sortByColumn(table, idx) {
  const tbody = table.querySelector('tbody');
  if (!tbody) return;
  const rows = Array.from(tbody.children).filter((el) => el.tagName === 'TR');
  const asc = String(table.dataset.sortCol) === String(idx) ? table.dataset.sortAsc !== 'true' : true;
  /** @param {Element} row */
  const keyOf = function (row) {
    const cell = /** @type {HTMLElement} */ (row.children[idx]);
    if (!cell) return '';
    return cell.dataset.sort !== undefined ? cell.dataset.sort : (/** @type {string} */ (cell.textContent)).trim();
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
  table.dataset.sortCol = /** @type {any} */ (idx);
  table.dataset.sortAsc = /** @type {any} */ (asc);
  // 방향 표식 — 활성 칼럼만 sort-asc/desc.
  table.querySelectorAll('th.sort-col').forEach(function (h) { h.classList.remove('sort-asc', 'sort-desc'); });
  const headRow = table.querySelector('thead tr');
  const th = headRow && headRow.children[idx];
  if (th && th.classList.contains('sort-col')) th.classList.add(asc ? 'sort-asc' : 'sort-desc');
  restripe(table);
}

/**
 * @param {HTMLElement} table
 * @param {(table: HTMLElement) => void} [onAfter]
 */
function makeSortable(table, onAfter) {
  if (!table) return;
  const thead = table.querySelector('thead');
  if (thead) {
    thead.addEventListener('click', function (e) {
      const th = /** @type {Element} */ (e.target).closest('th.sort-col');
      if (!th || !thead.contains(th)) return;
      const idx = Array.from((/** @type {Element} */ (th.parentNode)).children).indexOf(th);
      sortByColumn(table, idx);
      if (onAfter) onAfter(table);
    });
  }
  restripe(table); // 초기 줄무늬 (JS 제어라 최초 1회 필요)
}

export { restripe, sortByColumn, makeSortable };

// 초기 줄무늬 안전망 — sortable-table 은 CSS :nth-child 를 무력화하므로, 최초 렌더 시 전부 한 번 restripe.
// (fragment swap 되는 표는 각 페이지가 swap 후 restripe 재호출.)
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('table.sortable-table').forEach(t => restripe(/** @type {HTMLElement} */ (t)));
});
