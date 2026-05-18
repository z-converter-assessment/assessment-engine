// 환경 진단 결과 카드 안 view tab (AI / 고객 / 엔지니어) toggle.
// SSR 미리 렌더된 3 view (AI panel + 보고서 iframe 2개) 중 클릭한 tab 만 display:block.
// scope=server 카드는 tab 영역 자체가 없어서 본 핸들러는 noop.
//
// 책임 분담: 본 스크립트는 display toggle + active class만. iframe src 채움·AI polling은 SSR/diagnostic-results.js.

(function () {
  function activateView(card, viewName) {
    card.querySelectorAll('.view-tab').forEach(function (tab) {
      var active = tab.dataset.view === viewName;
      tab.classList.toggle('active', active);
      tab.style.color = active ? '#1e293b' : '#64748b';
      tab.style.borderBottomColor = active ? '#3b82f6' : 'transparent';
    });
    card.querySelectorAll('.view-panel').forEach(function (panel) {
      panel.style.display = panel.dataset.view === viewName ? 'block' : 'none';
    });
  }

  document.addEventListener('click', function (e) {
    var tab = e.target.closest('.view-tab');
    if (!tab) return;
    var card = tab.closest('[data-diagnostic-result]');
    if (!card) return;
    activateView(card, tab.dataset.view);
  });
})();
