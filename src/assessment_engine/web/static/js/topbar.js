// @ts-check
/*
 * 전역 상단 바 (base.html, 전 페이지 공통) — 두 가지:
 *  1) fleet 상태 폴링: /api/fleet-status 30초 폴링 -> N/M (온라인/전체) · 마지막 수신 + 점 색(온라인 유무).
 *  2) 호스트 검색(jump-to): /api/host-search?q= 디바운스 -> 드롭다운 -> public_id 로 상세 이동. Ctrl+K 포커스.
 * 서버 파생 재계산 없음(P4) — 카운트·시각은 서버(FleetStatus) 그대로, 점 색만 온라인 유무로 분기.
 */
(function () {
  'use strict';

  /** @param {unknown} s */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // -- 1) 데이터 최신성 ----------------------------------------------
  var statusEl = document.getElementById('fleet-status');
  var statusText = document.getElementById('fleet-status-text');

  /** @param {string | null | undefined} iso -> "방금"/"N초 전"/... (표시 경계, KST 무관 상대시각) */
  function agoKo(iso) {
    if (!iso) return null;
    var t = new Date(iso).getTime();
    if (isNaN(t)) return null;
    var s = Math.max(0, Math.round((Date.now() - t) / 1000));
    if (s < 10) return '방금';
    if (s < 60) return s + '초 전';
    var m = Math.floor(s / 60);
    if (m < 60) return m + '분 전';
    var h = Math.floor(m / 60);
    if (h < 24) return h + '시간 전';
    return Math.floor(h / 24) + '일 전';
  }

  /** @param {import('./generated/api').components['schemas']['FleetStatus'] | null} st */
  function renderStatus(st) {
    if (!statusEl || !statusText) return;
    statusEl.classList.remove('live', 'warn', 'down');
    if (!st) {
      statusText.textContent = '수집 상태 불명';
      statusEl.classList.add('down');
      return;
    }
    // 점 색 3상태 — 전부 온라인=live(초록), 일부 온라인=warn(주황), 하나도 없음=down(빨강). 최신성은 title 로만.
    var cls = st.online_count === 0 ? 'down' : (st.online_count === st.total_count ? 'live' : 'warn');
    statusEl.classList.add(cls);
    statusEl.title = '데이터 최신성 — 마지막 수집 ' + (agoKo(st.last_collected_at) || '기록 없음');
    statusText.textContent = st.online_count + '/' + st.total_count + ' (온라인/전체)';
  }

  function pollStatus() {
    fetch('/api/fleet-status', { headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (st) { renderStatus(/** @type {any} */ (st)); })
      .catch(function () { renderStatus(null); });
  }
  if (statusEl) {
    pollStatus();
    setInterval(pollStatus, 30000);
  }

  // -- 2) 호스트 검색 (jump-to) --------------------------------------
  var input = /** @type {HTMLInputElement | null} */ (document.getElementById('host-search-input'));
  var results = document.getElementById('host-search-results');
  /** @type {number} */ var seq = 0;
  /** @type {ReturnType<typeof setTimeout> | undefined} */ var debounceTimer;
  /** @type {number} */ var active = -1;
  var hasItems = false;

  function closeResults() {
    if (results) { results.hidden = true; results.innerHTML = ''; }
    hasItems = false;
    active = -1;
  }

  /** @param {string | null} os -> 버전 꼬리 제거한 짧은 OS 이름 */
  function osShort(os) { return (os || '').replace(/\s+\d.*$/, ''); }

  /** @param {Array<import('./generated/api').components['schemas']['HostSearchItem']>} list */
  function renderResults(list) {
    if (!results) return;
    active = -1;
    if (!list.length) {
      results.innerHTML = '<div class="topbar-search-empty">일치하는 호스트 없음</div>';
      results.hidden = false;
      hasItems = false;
      return;
    }
    results.innerHTML = list.map(function (h) {
      return '<a class="topbar-search-item" href="/servers/' + encodeURIComponent(h.public_id) + '">' +
        '<span>' + esc(h.hostname) + '</span><span class="th-os">' + esc(osShort(h.os_id)) + '</span></a>';
    }).join('');
    results.hidden = false;
    hasItems = true;
  }

  /** @param {string} q */
  function doSearch(q) {
    var mySeq = ++seq;
    fetch('/api/host-search?q=' + encodeURIComponent(q), { headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (list) { if (mySeq === seq) renderResults(Array.isArray(list) ? list : []); })
      .catch(function () { if (mySeq === seq) closeResults(); });
  }

  /** @param {number} n */
  function setActive(n) {
    if (!results) return;
    var nodes = results.querySelectorAll('.topbar-search-item');
    if (!nodes.length) return;
    active = (n + nodes.length) % nodes.length;
    nodes.forEach(function (el, i) { el.classList.toggle('active', i === active); });
  }

  if (input && results) {
    var box = input;
    box.addEventListener('input', function () {
      var q = box.value.trim();
      clearTimeout(debounceTimer);
      if (q.length < 1) { closeResults(); return; }
      debounceTimer = setTimeout(function () { doSearch(q); }, 180);
    });
    box.addEventListener('keydown', function (e) {
      if (results && !results.hidden && hasItems) {
        if (e.key === 'ArrowDown') { e.preventDefault(); setActive(active + 1); return; }
        if (e.key === 'ArrowUp') { e.preventDefault(); setActive(active - 1); return; }
        if (e.key === 'Enter') {
          var nodes = results.querySelectorAll('.topbar-search-item');
          var target = active >= 0 ? nodes[active] : nodes[0];
          var href = target ? target.getAttribute('href') : null;
          if (href) { e.preventDefault(); window.location.href = href; return; }
        }
      }
      if (e.key === 'Escape') { closeResults(); box.blur(); }
    });
    box.addEventListener('focus', function () { if (hasItems && results) results.hidden = false; });
    document.addEventListener('click', function (e) {
      var parent = box.parentElement;
      if (parent && e.target instanceof Node && !parent.contains(e.target)) closeResults();
    });
  }
})();
