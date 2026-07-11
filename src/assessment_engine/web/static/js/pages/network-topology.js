// @ts-check
/**
 * 네트워크 토폴로지 카드 — Cytoscape.js 집계 뷰 렌더러 (P4: 라이브러리 옵션 조립·레이아웃·스타일·클릭 바인딩만).
 *
 * 데이터: #topology-graph[data-elements] 에 mapper precompute 한 Cytoscape elements(JSON)가 박혀 옴.
 *   노드/엣지 조립·subnet 그룹핑·가상망 필터는 전부 서버(mappers/topology.py) 책임 — 본 JS 는 계산 0.
 * 집계 뷰: subnet 허브 노드만 기본 표시(hostCount 라벨), host 노드/엣지는 "collapsed"(display:none) 로 시작.
 *   대규모 호스트(수백대)가 한 번에 펼쳐져 hairball 이 되는 것을 막고, 서브넷을 1급 노드로 둔다.
 * 인터랙션:
 *   - subnet 노드 클릭 -> 해당 서브넷 host 펼침/접기. 펼쳐진(visible) 노드만 cose 배치(display:none 은 레이아웃 제외).
 *   - host 노드 클릭 -> /servers/<publicId> 상세 이동.
 *   host 색은 OS(linux/windows)로만 구분.
 *
 * 외부 의존: cytoscape (CDN, topology.html 에서 본 파일보다 먼저 defer 로드). 미로드 시 silent skip.
 */
(function () {
  'use strict';

  // themeColor() 는 인자 없이 테마 primary 색 반환 (globals.d.ts 선언은 name 필수라 로컬 any 캐스트).
  var OS_COLOR = { linux: /** @type {any} */ (ChartUtils).themeColor(), windows: '#8b5cf6' };
  // 서브넷 노드 — 연한 회색 테마색(base.html --color-table-head) 추종. getComputedStyle 실패 시 fallback.
  var SUBNET = (getComputedStyle(document.documentElement).getPropertyValue('--color-table-head').trim() || '#a7b2c0');

  var COSE = { name: 'cose', animate: false, padding: 24, nodeRepulsion: 6000, idealEdgeLength: 90 };

  var STYLE = [
    {
      selector: 'node[kind = "subnet"]',
      style: {
        'background-color': SUBNET,
        shape: 'round-rectangle',
        label: function (n) { return n.data('label') + '  (' + n.data('hostCount') + ')'; },
        color: '#fff',
        'font-size': 10,
        'font-family': 'monospace',
        'text-valign': 'center',
        'text-halign': 'center',
        width: 'label',
        height: 24,
        padding: '8px',
      },
    },
    {
      selector: 'node[kind = "host"]',
      style: {
        'background-color': function (n) { return OS_COLOR[n.data('osFamily')] || '#94a3b8'; },
        shape: 'ellipse',
        // 라벨 = 호스트명 + (있으면) 워크로드 역할 — 서브넷을 app tier 구조로 읽히게. 색은 OS 유지.
        label: function (n) { var r = n.data('roles'); return n.data('label') + (r && r.length ? '\n' + r.join(', ') : ''); },
        'text-wrap': 'wrap',
        color: '#334155',
        'font-size': 10,
        'text-valign': 'bottom',
        'text-halign': 'center',
        'text-margin-y': 3,
        width: 18,
        height: 18,
      },
    },
    {
      selector: 'edge',
      style: { width: 1.5, 'line-color': '#cbd5e1', 'curve-style': 'bezier' },
    },
    {
      // 집계 뷰 — 접힌 host/edge 는 렌더·레이아웃에서 제외.
      selector: '.collapsed',
      style: { display: 'none' },
    },
  ];

  var cy = null; // 직전 인스턴스 — 재렌더 시 destroy (leak 방지).

  // host 표시 = 인접 subnet 중 펼쳐진(expanded) 게 하나라도 있으면. edge 는 양 끝 노드가 모두 visible 일 때만.
  function applyVisibility() {
    cy.batch(function () {
      cy.nodes('[kind = "host"]').forEach(function (h) {
        var show = h.connectedEdges().connectedNodes('[kind = "subnet"]').some(function (s) {
          return s.data('expanded');
        });
        h.toggleClass('collapsed', !show);
      });
      cy.edges().forEach(function (e) {
        e.toggleClass('collapsed', e.source().hasClass('collapsed') || e.target().hasClass('collapsed'));
      });
    });
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

    cy = cytoscape({
      container: el,
      elements: elements,
      style: STYLE,
      minZoom: 0.3,
      maxZoom: 2.5,
      // 휠·트랙패드 두 손가락 스크롤 줌 비활성 — 페이지 스크롤이 토폴로지 줌으로 가로채이는 것 방지.
      // 줌은 +/- 버튼(cy.zoom())으로만. 드래그 pan 은 유지.
      userZoomingEnabled: false,
      userPanningEnabled: true,
    });

    // 초기 — subnet 만 보임(host/edge collapsed). 펼쳐진 게 없으니 subnet 노드만 배치된다.
    cy.layout(COSE).run();

    cy.on('tap', 'node[kind = "subnet"]', function (evt) {
      var n = evt.target;
      n.data('expanded', !n.data('expanded'));
      applyVisibility();
      cy.layout(COSE).run();
    });

    cy.on('tap', 'node[kind = "host"]', function (evt) {
      var pid = evt.target.data('publicId');
      if (pid) window.location.href = '/servers/' + pid;
    });

    bindZoomControls();
  }

  // 확대/축소·전체보기 버튼 -> cy.zoom()/fit (minZoom·maxZoom 자동 클램프). 뷰포트 중앙 기준 줌.
  function bindZoomControls() {
    document.querySelectorAll('[data-topo-zoom]').forEach(function (btn) {
      var action = btn.getAttribute('data-topo-zoom');
      /** @type {HTMLElement} */ (btn).onclick = function () {
        if (!cy) return;
        if (action === 'fit') {
          cy.fit(null, 24);
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

  /** @type {any} */ (window).NetworkTopology = { render: render };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', render);
  } else {
    render();
  }
})();
