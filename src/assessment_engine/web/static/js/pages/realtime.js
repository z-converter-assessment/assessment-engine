/**
 * 실시간 메트릭 페이지 — 환경 실시간 메트릭 카드 자동 갱신.
 *
 * 30초마다 partial(fragment=realtime)을 fetch 해 #env-realtime-mount innerHTML 교체 (list.js 대시보드 폴링 패턴).
 * 갱신 주기(REFRESH_MS)는 본 JS 단일 진실 — stamp 의 '...초마다 자동 갱신' prefix 를 JS 가 조립(SSR 하드코딩 회피).
 * 외부 의존: 없음 (순수 fetch + DOM 교체).
 */
(function () {
  const REFRESH_MS = 30_000;
  const mount = document.getElementById('env-realtime-mount');
  if (!mount) return;
  // 선택 N대 한정(있으면) — 폴링 fetch 에 ids 전달. 없으면 전체 환경.
  const SELECTION_IDS = document.body.dataset.selectionIds || '';
  const FRAGMENT_URL = '/servers/environment/realtime?fragment=realtime' + (SELECTION_IDS ? '&ids=' + encodeURIComponent(SELECTION_IDS) : '');

  function paintStamp() {
    // 시각 data 는 카드 안(폴링 교체 대상), 표시 stamp 는 카드 밖 제목 줄 — 교체 후 data 를 읽어 밖 stamp 갱신.
    const data = mount.querySelector('#env-realtime-ts-data');
    const target = document.getElementById('env-realtime-stamp');
    if (data && target) target.textContent = `${REFRESH_MS / 1000}초마다 자동 갱신 · 최근 ${data.dataset.ts}`;
  }
  paintStamp();  // SSR 초기 stamp 에 갱신 주기 prefix 주입.

  async function refresh() {
    try {
      const res = await fetch(FRAGMENT_URL);
      if (res.ok) { mount.innerHTML = await res.text(); paintStamp(); }
    } catch (e) {
      // 네트워크 일시 오류 — 다음 주기 재시도 (fail-soft, 화면 유지).
    }
  }
  setInterval(refresh, REFRESH_MS);
})();
