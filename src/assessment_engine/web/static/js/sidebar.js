// @ts-check
/**
 * 사이드바 네비게이션 — 접기/펼치기(데스크탑) + off-canvas 토글(좁은 화면) + 아코디언 그룹 접기.
 *
 * progressive enhancement: JS 없이도 사이드바·메뉴 전부 노출(기본 펼침). 본 파일은 토글 동작만 얹는다.
 * 마크업: _sidebar.html(#sidebar / #sidebar-collapse / [data-sidebar-group] / [data-sidebar-toggle])
 *   + base.html(#sidebar-toggle 햄버거 / #sidebar-backdrop).
 * 상태: body.sidebar-collapsed(데스크탑 접힘) / body.sidebar-open(좁은화면 off-canvas 열림).
 */
(function () {
  'use strict';

  var toggleBtn = document.getElementById('sidebar-toggle');     // 햄버거 — 펼치기/열기
  var collapseBtn = document.getElementById('sidebar-collapse');  // 브랜드 옆 — 접기/닫기
  var backdrop = document.getElementById('sidebar-backdrop');

  function isMobile() { return window.matchMedia('(max-width: 768px)').matches; }

  function setOpen(open) {
    document.body.classList.toggle('sidebar-open', open);
    if (toggleBtn) toggleBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  // 햄버거 — 좁은 화면: off-canvas open 토글 / 데스크탑: collapsed 해제(펼치기).
  if (toggleBtn) {
    toggleBtn.addEventListener('click', function () {
      if (isMobile()) {
        setOpen(!document.body.classList.contains('sidebar-open'));
      } else {
        document.body.classList.remove('sidebar-collapsed');
      }
    });
  }

  // 브랜드 옆 접기 버튼 — 데스크탑: collapsed 설정 / 좁은 화면: off-canvas 닫기.
  if (collapseBtn) {
    collapseBtn.addEventListener('click', function () {
      if (isMobile()) {
        setOpen(false);
      } else {
        document.body.classList.add('sidebar-collapsed');
      }
    });
  }

  if (backdrop) backdrop.addEventListener('click', function () { setOpen(false); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') setOpen(false); });

  // 아코디언 그룹 접기 — 그룹 헤더 클릭 시 is-collapsed 토글 (caret 회전 + 항목 숨김은 CSS).
  document.querySelectorAll('[data-sidebar-toggle]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var group = btn.closest('[data-sidebar-group]');
      if (!group) return;
      var collapsed = group.classList.toggle('is-collapsed');
      btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    });
  });
})();
