// @ts-check
/**
 * 사이드바 네비게이션 — 상단바 토글(#sidebar-collapse) 접기/펼치기 + 아코디언 그룹 접기. 둘 다 localStorage persist.
 *
 * progressive enhancement: JS 없이도 사이드바·메뉴 전부 노출(기본 펼침). 본 파일은 토글·복원만 얹는다.
 * 마크업: base.html(#sidebar-collapse / #sidebar-backdrop) + _sidebar.html([data-sidebar-group][data-group-key]/[data-sidebar-toggle]).
 * 상태: body.sidebar-collapsed(데스크탑 접힘) / body.sidebar-open(좁은화면) / group.is-collapsed(아코디언).
 * persist: sidebarCollapsed(사이드바 전체) · sidebarGroups(그룹별 접힘 맵). 페이지 이동해도 유지 — 활성 항목만 서버 렌더로 갱신.
 * 첫 방문(저장값 없음): 활성 항목이 있는 그룹만 펼치고 나머지 접음.
 */
(function () {
  'use strict';

  var COLLAPSE_KEY = 'sidebarCollapsed';
  var GROUPS_KEY = 'sidebarGroups';

  /** @param {Element} g */
  function syncAria(g) {
    var btn = g.querySelector('[data-sidebar-toggle]');
    if (btn) btn.setAttribute('aria-expanded', g.classList.contains('is-collapsed') ? 'false' : 'true');
  }

  function saveGroups() {
    /** @type {Record<string, boolean>} */
    var state = {};
    document.querySelectorAll('[data-sidebar-group]').forEach(function (g) {
      var key = g.getAttribute('data-group-key');
      if (key) state[key] = g.classList.contains('is-collapsed');
    });
    try { localStorage.setItem(GROUPS_KEY, JSON.stringify(state)); } catch (e) { /* noop */ }
  }

  // 그룹 초기 상태 — 저장값 있으면 복원, 없으면 첫 방문 기본값(활성 그룹만 펼침) + 저장.
  function initGroups() {
    var groups = document.querySelectorAll('[data-sidebar-group]');
    /** @type {Record<string, boolean> | null} */
    var saved = null;
    try { saved = JSON.parse(localStorage.getItem(GROUPS_KEY) || 'null'); } catch (e) { saved = null; }
    if (saved && typeof saved === 'object') {
      groups.forEach(function (g) {
        var key = g.getAttribute('data-group-key');
        g.classList.toggle('is-collapsed', !!(key && saved && saved[key]));
        syncAria(g);
      });
    } else {
      var anyActive = document.querySelector('[data-sidebar-group] .sidebar-item.is-active');
      groups.forEach(function (g) {
        if (anyActive) g.classList.toggle('is-collapsed', !g.querySelector('.sidebar-item.is-active'));
        syncAria(g);
      });
      saveGroups();
    }
  }

  // 초기 복원 — transition 억제(no-sidebar-anim)로 로드 시 슬라이드 깜빡임 없이 즉시 반영.
  document.body.classList.add('no-sidebar-anim');
  try {
    if (localStorage.getItem(COLLAPSE_KEY) === '1') document.body.classList.add('sidebar-collapsed');
  } catch (e) { /* localStorage 불가 — 기본 펼침 */ }
  initGroups();
  requestAnimationFrame(function () {
    requestAnimationFrame(function () { document.body.classList.remove('no-sidebar-anim'); });
  });

  var collapseBtn = document.getElementById('sidebar-collapse'); // 상단바 토글 — 접기/펼치기(데스크탑) · 열기/닫기(모바일)
  var backdrop = document.getElementById('sidebar-backdrop');

  function isMobile() { return window.matchMedia('(max-width: 768px)').matches; }

  /** @param {boolean} open */
  function setOpen(open) {
    document.body.classList.toggle('sidebar-open', open);
    if (collapseBtn) collapseBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  // 상단바 토글 — 데스크탑: collapsed 토글(슬라이드) + persist / 좁은 화면: off-canvas open 토글.
  if (collapseBtn) {
    collapseBtn.addEventListener('click', function () {
      if (isMobile()) {
        setOpen(!document.body.classList.contains('sidebar-open'));
      } else {
        var collapsed = document.body.classList.toggle('sidebar-collapsed');
        try { localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0'); } catch (e) { /* noop */ }
      }
    });
  }

  if (backdrop) backdrop.addEventListener('click', function () { setOpen(false); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') setOpen(false); });

  // 아코디언 그룹 접기 — 그룹 헤더 클릭 시 is-collapsed 토글 + persist (caret 회전·항목 슬라이드는 CSS).
  document.querySelectorAll('[data-sidebar-toggle]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var group = btn.closest('[data-sidebar-group]');
      if (!group) return;
      var collapsed = group.classList.toggle('is-collapsed');
      btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      saveGroups();
    });
  });
})();
