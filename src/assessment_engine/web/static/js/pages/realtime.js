// @ts-check
/**
 * 실시간 현황 30초 자동 갱신 — fragment=realtime fetch 후 #rt-mount innerHTML 교체.
 *
 * mount(#rt-mount) = 현재 자원 현황 + 현재 부하 상위(최신 스냅샷). 운영 신호는 폴링 밖(느린 신호, full-page 정적).
 * 선택 N대(ids)는 body data-selection-ids 로 보존. fail-soft — 네트워크 오류 시 현재 화면 유지.
 */
(function () {
  const mount = document.getElementById('rt-mount');
  if (!mount) return;
  const ids = document.body.dataset.selectionIds || '';
  const url = '/environment/realtime?fragment=realtime' + (ids ? '&ids=' + encodeURIComponent(ids) : '');

  let seq = 0;
  async function refresh() {
    if (!mount) return;
    const mySeq = ++seq; // capture-before-await — 더 늦은 요청이 우선
    try {
      const res = await fetch(url);
      if (mySeq !== seq) return; // stale 응답 무시
      if (!res.ok) return;
      mount.innerHTML = await res.text();
    } catch (e) {
      // 네트워크 일시 오류 — 현재 화면 유지 (fail-soft)
    }
  }

  ChartUtils.initAutoRefresh(refresh, 30000);  // 탭 비활성 시 일시정지 (chart-utils 단일 진실)
})();
