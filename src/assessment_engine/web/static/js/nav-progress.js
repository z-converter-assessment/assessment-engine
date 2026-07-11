/**
 * 전역 네비게이션 진행바 — full-page 이동(내부 링크 클릭·폼 전송) 시 상단 progress bar 로 즉시 피드백.
 *
 * SSR 멀티페이지라 클릭 ~ 새 문서 렌더 사이에 빈 구간이 생긴다. 그 구간에 "눌렸다/로딩 중" 신호를 줘
 * 사용자가 클릭이 먹었는지 확인 가능하게 한다. 새 문서가 로드되면 바 element 자체가 교체돼 자연 소멸.
 *
 * progressive enhancement: JS 없이도 네비게이션은 정상 동작(브라우저 기본). 본 파일은 피드백만 얹는다.
 * 마크업: base.html(#nav-progress + .nav-progress CSS). z-index 9999 로 off-canvas 사이드바보다 위.
 */
// @ts-check
(function () {
  'use strict';

  var bar = /** @type {HTMLElement} */ (document.getElementById('nav-progress'));
  if (!bar) return;

  var timer = null;       // trickle interval
  var safety = null;      // stuck 방지 fallback
  var progress = 0;

  function start() {
    if (timer) return;                    // 이미 진행 중 — 중복 시작 무시
    progress = 0;
    bar.classList.add('is-active');
    bar.style.width = '0';
    requestAnimationFrame(function () {   // 다음 프레임에 시작값 — width transition 적용되게
      progress = 12;
      bar.style.width = progress + '%';
      timer = setInterval(trickle, 300);
    });
    // 이동이 끝내 일어나지 않으면(클릭 후 JS 가 막은 경우 등) 12초 뒤 자동 정리.
    safety = setTimeout(reset, 12000);
  }

  function trickle() {
    // 90% 까지 점근(절대 도달 X) — 실제 완료는 새 문서 로드가 담당.
    if (progress < 90) { progress += (90 - progress) * 0.18; bar.style.width = progress + '%'; }
  }

  function reset() {
    if (timer)  { clearInterval(timer); timer = null; }
    if (safety) { clearTimeout(safety); safety = null; }
    bar.classList.remove('is-active');
    bar.style.width = '0';
    progress = 0;
  }

  // 내부 링크 클릭 — 새 페이지로 실제 이동하는 것만 트리거.
  // bubble 단계 + defaultPrevented 검사: 모달·JS 핸들러가 preventDefault 한 클릭(이동 X)은 제외.
  document.addEventListener('click', function (e) {
    if (e.defaultPrevented) return;
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;  // 새 탭·수정키 클릭 제외
    var a = /** @type {HTMLAnchorElement | null} */ (/** @type {Element} */ (e.target).closest('a[href]'));
    if (!a) return;
    if (a.target && a.target !== '_self') return;          // target=_blank 등 제외
    if (a.hasAttribute('download')) return;                // 다운로드는 이동 아님
    if (a.hasAttribute('data-no-progress')) return;        // 명시 opt-out
    if (a.hasAttribute('data-task-id')) return;            // task 모달 트리거(이동 X)
    var href = a.getAttribute('href');
    if (!href || href.charAt(0) === '#') return;           // 앵커/같은 페이지 점프
    if (/^(javascript:|mailto:|tel:)/i.test(href)) return;
    if (a.origin && a.origin !== window.location.origin) return;  // 외부 도메인 제외
    if (a.href === window.location.href) return;           // 동일 URL(이동 없음) 제외
    start();
  });

  // 폼 전송(검색·필터 등 full-page GET/POST) — 결과 페이지 도착까지 피드백.
  document.addEventListener('submit', function (e) {
    if (e.defaultPrevented) return;                        // JS 가 가로챈(fetch) 폼 제외
    var f = /** @type {HTMLFormElement} */ (e.target);
    if (f.hasAttribute('data-no-progress')) return;
    if (f.target && f.target !== '_self') return;
    start();
  });

  // 실제 페이지 이탈 — 새로고침(F5)·주소창 이동·뒤로/앞으로 등 클릭/전송으로 안 잡히는 경로 커버.
  // 이탈 직전 바 시작 -> 새 문서 로드 전까지 현재 문서 위에 표시. 클릭 경로와 겹쳐도 start() 의 중복 가드로 no-op.
  // (returnValue 미설정 -> 이탈 확인 대화상자 안 뜸. 다운로드·target=_blank 는 unload 없어 미발화.)
  window.addEventListener('beforeunload', start);

  // bfcache 복귀(뒤로가기) — 얼어있던 active 바 제거.
  window.addEventListener('pageshow', function (e) { if (e.persisted) reset(); });
})();
