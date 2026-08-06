// @ts-check
/**
 * 네트워크 토폴로지 카드 — Cytoscape.js 3계층 force-directed 렌더러 (P4: 라이브러리 옵션·레이아웃·스타일·인터랙션만).
 *
 * 데이터: #topology-graph[data-elements] 에 mapper precompute 한 Cytoscape elements(JSON)가 박혀 옴.
 *   노드/엣지 조립·subnet 그룹핑·gateway 라우터 도출은 전부 서버(mappers/topology.py) 책임 — 본 JS 는 계산 0.
 * 3계층: gateway(라우터) -> subnet -> host. 기본은 전부 펼쳐 cose(force-directed) 로 배치 — 서브넷별 성단으로
 *   뭉치고 멀티홈(2+ 서브넷) 호스트가 성단 사이 다리로 드러난다. 대규모(호스트 > COLLAPSE_THRESHOLD) 환경만
 *   골격(gateway+subnet)으로 시작해 subnet 클릭 시 펼친다 (hairball 회피).
 * 인터랙션:
 *   - 노드 hover -> 이웃(연결 노드/엣지)만 강조 + 이웃 라벨 노출 + 상세 툴팁 (평시 host 라벨은 잡음이라 숨김).
 *   - subnet 노드 클릭 -> 해당 서브넷 host 펼침/접기 (선택 declutter).
 *   - host 노드 클릭 -> /servers/<publicId> 상세 이동.
 *   host 색은 OS(linux/windows) 원으로 통일 — 멀티홈은 그래프에 표기 안 함(서브넷별 소속 서버 표에서만 노출).
 *
 * 외부 의존: cytoscape (topology.html 에서 본 파일보다 먼저 defer 로드). 미로드 시 silent skip.
 */
(function () {
  'use strict';

  // themeColor() 는 인자 없이 테마 primary 색 반환 (globals.d.ts 선언은 name 필수라 로컬 any 캐스트).
  var OS_COLOR = /** @type {Record<string, string>} */ ({ linux: /** @type {any} */ (ChartUtils).themeColor(), windows: '#8b5cf6' });
  var GW_COLOR = '#d97706';      // 라우터(게이트웨이) — amber, 네트워크 인프라로 구분
  var SUBNET_COLOR = '#475569';  // slate — 흰 텍스트 대비 확보
  var COLLAPSE_THRESHOLD = 150;  // 호스트 이보다 많으면 골격 뷰로 시작 (hairball 회피)

  // cose(force-directed) — 서브넷별 성단 분리. componentSpacing 크게 잡아 성단 간 여백 확보.
  var COSE = {
    name: 'cose', animate: false, padding: 30, nodeRepulsion: 9000, idealEdgeLength: 70,
    componentSpacing: 90, nodeOverlap: 12, gravity: 0.3, numIter: 1200,
  };

  var STYLE = [
    {
      selector: 'node[kind = "gateway"]',
      style: {
        'background-color': GW_COLOR,
        shape: 'round-diamond',
        label: function (/** @type {any} */ n) { return n.data('label') + '  · ' + n.data('subnetCount') + '서브넷'; },
        color: '#fff',
        'font-size': 12,
        'font-weight': 600,
        'font-family': 'monospace',
        'text-valign': 'center',
        'text-halign': 'center',
        'text-outline-color': GW_COLOR,
        'text-outline-width': 2,
        'z-index': 20,   // 호스트 점 위로 (가려짐 방지)
        width: 'label',
        height: 34,
        padding: '12px',
      },
    },
    {
      selector: 'node[kind = "subnet"]',
      style: {
        'background-color': SUBNET_COLOR,
        shape: 'round-rectangle',
        label: function (/** @type {any} */ n) { return n.data('label') + '  (' + n.data('hostCount') + ')'; },
        color: '#fff',
        'font-size': 12,
        'font-weight': 600,
        'font-family': 'monospace',
        'text-valign': 'center',
        'text-halign': 'center',
        'z-index': 10,   // 호스트 점 위로 (가려짐 방지)
        width: 'label',
        height: 30,
        padding: '10px',
      },
    },
    {
      selector: 'node[kind = "host"]',
      style: {
        'background-color': function (/** @type {any} */ n) { return OS_COLOR[n.data('osFamily')] || '#94a3b8'; },
        shape: 'ellipse',
        label: function (/** @type {any} */ n) { return n.data('label'); },
        color: '#1e293b',
        'font-size': 11,
        'font-weight': 500,
        'text-valign': 'bottom',
        'text-halign': 'center',
        'text-margin-y': 4,
        'text-opacity': 0,   // 평시 숨김 — 70+ 라벨 잡음 회피. hover 이웃만 노출(.hl).
        width: 22,
        height: 22,
      },
    },
    {
      // hover 시 이웃 호스트 라벨 노출.
      selector: 'node[kind = "host"].hl',
      style: { 'text-opacity': 1 },
    },
    {
      // route 엣지 (gateway -> subnet) — 라우팅 골격, 굵고 amber.
      selector: 'edge[kind = "route"]',
      style: { width: 2.5, 'line-color': GW_COLOR, 'curve-style': 'bezier', opacity: 0.75 },
    },
    {
      // member 엣지 (host -> subnet) — 얇은 회색.
      selector: 'edge[kind = "member"]',
      style: { width: 1.2, 'line-color': '#cbd5e1', 'curve-style': 'bezier', opacity: 0.7 },
    },
    {
      // 접힌 host/member 엣지 (대규모 환경 골격 뷰 / subnet 선택 접기) — 렌더·레이아웃 제외.
      selector: '.collapsed',
      style: { display: 'none' },
    },
    {
      // hover 강조 — 이웃 아닌 요소 흐리게.
      selector: '.faded',
      style: { opacity: 0.12, 'text-opacity': 0 },
    },
  ];

  /** @type {any} */
  var cy = null; // 직전 인스턴스 — 재렌더 시 destroy (leak 방지).
  /** @type {HTMLElement | null} */
  var tip = null; // 툴팁 div (그래프 컨테이너 내 절대배치).

  // 보이는 요소만 cose 배치. display:none 은 :visible 로 제외.
  function runLayout() {
    cy.elements(':visible').layout(COSE).run();
  }

  // host 표시 = 인접 subnet 중 펼쳐진(expanded) 게 하나라도 있으면. member 엣지는 양 끝 visible 일 때만.
  // gateway·subnet·route 엣지(라우팅 골격)는 항상 표시.
  function applyVisibility() {
    cy.batch(function () {
      cy.nodes('[kind = "host"]').forEach(function (/** @type {any} */ h) {
        var show = h.connectedEdges('[kind = "member"]').connectedNodes('[kind = "subnet"]').some(function (/** @type {any} */ s) {
          return s.data('expanded');
        });
        h.toggleClass('collapsed', !show);
      });
      cy.edges('[kind = "member"]').forEach(function (/** @type {any} */ e) {
        e.toggleClass('collapsed', e.source().hasClass('collapsed') || e.target().hasClass('collapsed'));
      });
    });
  }

  function esc(/** @type {any} */ s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return /** @type {Record<string,string>} */ ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c];
    });
  }

  // 노드 종류별 툴팁 HTML — host: 인터페이스 상세 / subnet: 게이트웨이·호스트수 / gateway: 서브넷수.
  function tipHtml(/** @type {any} */ n) {
    var kind = n.data('kind');
    if (kind === 'gateway') {
      return '<div class="tt-title">라우터 ' + esc(n.data('label')) + '</div><div class="tt-row">서브넷 ' + esc(n.data('subnetCount')) + '개 연결</div>';
    }
    if (kind === 'subnet') {
      var gw = n.data('gateway');
      return '<div class="tt-title">' + esc(n.data('label')) + '</div>'
        + '<div class="tt-row">호스트 ' + esc(n.data('hostCount')) + '대</div>'
        + '<div class="tt-row">게이트웨이 ' + (gw ? esc(gw) : '—') + '</div>';
    }
    // host
    var roles = n.data('roles') || [];
    var ifaces = n.data('ifaces') || [];
    var html = '<div class="tt-title">' + esc(n.data('label')) + '</div>';
    html += '<div class="tt-row">' + esc(n.data('osFamily')) + (roles.length ? ' · ' + esc(roles.join(', ')) : '') + '</div>';
    for (var i = 0; i < ifaces.length; i++) {
      var f = ifaces[i];
      var parts = [f.name, f.mac, f.mtu ? 'mtu ' + f.mtu : null, f.gateway ? 'gw ' + f.gateway : null].filter(Boolean);
      html += '<div class="tt-iface">' + esc(parts.join(' · ')) + '</div>';
    }
    return html;
  }

  function showTip(/** @type {any} */ n) {
    if (!tip) return;
    tip.innerHTML = tipHtml(n);
    var p = n.renderedPosition();
    tip.style.display = 'block';
    // 노드 우하단에 배치하되 컨테이너 밖으로 나가면 좌측으로 접기.
    var cw = cy.width();
    var left = p.x + 14;
    if (left + tip.offsetWidth > cw) left = p.x - tip.offsetWidth - 14;
    tip.style.left = Math.max(4, left) + 'px';
    tip.style.top = Math.max(4, p.y + 10) + 'px';
  }

  function hideTip() {
    if (tip) tip.style.display = 'none';
  }

  function render() {
    var el = document.getElementById('topology-graph');
    if (!el || typeof cytoscape === 'undefined') {
      if (cy) { cy.destroy(); cy = null; }
      return;
    }

    var elements;
    try {
      elements = JSON.parse(el.getAttribute('data-elements') || '[]');
    } catch (e) {
      return;
    }
    if (!Array.isArray(elements) || elements.length === 0) {
      if (cy) { cy.destroy(); cy = null; }
      return;
    }

    if (cy) { cy.destroy(); cy = null; }
    tip = document.getElementById('topology-tooltip');

    cy = cytoscape({
      container: el,
      elements: elements,
      style: STYLE,
      minZoom: 0.2,
      maxZoom: 2.5,
      // 휠·트랙패드 두 손가락 스크롤 줌 비활성 — 페이지 스크롤이 토폴로지 줌으로 가로채이는 것 방지.
      // 줌은 +/- 버튼(cy.zoom())으로만. 드래그 pan 은 유지.
      userZoomingEnabled: false,
      userPanningEnabled: true,
    });

    // 기본 = 전부 펼쳐 constellation 으로 캔버스를 채운다. 대규모(> THRESHOLD)만 골격으로 시작.
    var big = cy.nodes('[kind = "host"]').length > COLLAPSE_THRESHOLD;
    cy.batch(function () {
      cy.nodes('[kind = "subnet"]').forEach(function (/** @type {any} */ s) { s.data('expanded', !big); });
      if (!big) cy.elements().removeClass('collapsed');
    });
    runLayout();
    cy.fit(cy.elements(':visible'), 28);

    cy.on('tap', 'node[kind = "subnet"]', function (/** @type {any} */ evt) {
      var n = evt.target;
      n.data('expanded', !n.data('expanded'));
      applyVisibility();
      runLayout();
    });

    cy.on('tap', 'node[kind = "host"]', function (/** @type {any} */ evt) {
      var pid = evt.target.data('publicId');
      if (pid) window.location.href = '/servers/' + pid;
    });

    // hover 강조 + 툴팁 — 이웃(연결 노드/엣지)만 남기고 나머지 흐리게, 이웃 host 라벨 노출.
    cy.on('mouseover', 'node', function (/** @type {any} */ evt) {
      var n = evt.target;
      var neighborhood = n.closedNeighborhood();
      cy.elements().not(neighborhood).addClass('faded');
      neighborhood.nodes('[kind = "host"]').addClass('hl');
      showTip(n);
    });
    cy.on('mouseout', 'node', function () {
      cy.elements().removeClass('faded').removeClass('hl');
      hideTip();
    });
    cy.on('pan zoom', hideTip);

    bindZoomControls();
  }

  // 확대/축소·전체보기 버튼 -> cy.zoom()/fit (minZoom·maxZoom 자동 클램프). 뷰포트 중앙 기준 줌.
  function bindZoomControls() {
    document.querySelectorAll('[data-topo-zoom]').forEach(function (btn) {
      var action = btn.getAttribute('data-topo-zoom');
      /** @type {HTMLElement} */ (btn).onclick = function () {
        if (!cy) return;
        if (action === 'fit') {
          cy.fit(cy.elements(':visible'), 28);
          return;
        }
        var factor = action === 'in' ? 1.3 : 1 / 1.3;
        cy.zoom({
          level: cy.zoom() * factor,
          renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 },
        });
      };
    });
  }


  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', render);
  } else {
    render();
  }
})();
