/**
 * 네트워크 토폴로지 카드 — Cytoscape.js 렌더러 (P4: 라이브러리 옵션 조립·레이아웃·스타일·클릭 바인딩만).
 *
 * 데이터: #topology-graph[data-elements] 에 mapper precompute 한 Cytoscape elements(JSON)가 박혀 옴.
 *   노드/엣지 조립·subnet 그룹핑·가상망 필터는 전부 서버(mappers/topology.py) 책임 — 본 JS 는 계산 0.
 * 모델: subnet 허브 노드 + host 노드 bipartite. host 가 여러 subnet 에 걸치면(multiHomed) 라우팅 지점으로 강조.
 * 인터랙션: host 노드 클릭 -> /servers/<publicId> 상세로 이동.
 *
 * 자동갱신 정합: 토폴로지 카드는 자동갱신 fragment(#dashboard-live) 안이라 30초 폴링이 innerHTML 을 교체하면
 *   캔버스 컨테이너가 새 빈 div 로 바뀐다. list.js 가 교체 직후 window.NetworkTopology.render() 를 동기 호출
 *   (브라우저 repaint 전이라 깜빡임 0). 노드 위치를 id 별 캐시해, 데이터(노드 id 집합) 불변 시 preset 으로
 *   동일 배치 재현(force 레이아웃 재실행 점프 회피) — 변하면 cose 재배치 후 캐시 갱신.
 *
 * 외부 의존: cytoscape (CDN, list.html 에서 본 파일보다 먼저 defer 로드). 미로드 시 silent skip.
 */
(function () {
  'use strict';

  var OS_COLOR = { linux: '#3b82f6', windows: '#0ea5e9' };
  var MULTI_HOMED = '#f59e0b';
  var SUBNET = '#64748b';

  var STYLE = [
    {
      selector: 'node[kind = "subnet"]',
      style: {
        'background-color': SUBNET,
        shape: 'round-rectangle',
        label: 'data(label)',
        color: '#fff',
        'font-size': 10,
        'font-family': 'monospace',
        'text-valign': 'center',
        'text-halign': 'center',
        width: 'label',
        height: 22,
        padding: '6px',
      },
    },
    {
      selector: 'node[kind = "host"]',
      style: {
        'background-color': function (n) {
          if (n.data('multiHomed')) return MULTI_HOMED;
          return OS_COLOR[n.data('osFamily')] || '#94a3b8';
        },
        shape: 'ellipse',
        label: 'data(label)',
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
  ];

  var cy = null; // 직전 인스턴스 — 재렌더 시 destroy (leak 방지).
  var savedPos = {}; // node id -> {x, y}. 폴링 재렌더 간 배치 고정.

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

    // 모든 노드 id 가 캐시에 있으면 preset(동일 배치), 새 노드가 있으면 cose 재배치.
    var nodeIds = elements
      .filter(function (e) { return e.data && e.data.id; })
      .map(function (e) { return e.data.id; });
    var allCached = nodeIds.length > 0 && nodeIds.every(function (id) { return savedPos[id]; });

    // cy 를 먼저 생성한 뒤 layout 을 실행한다. cose 의 stop/layoutstop 콜백이 cy 를 참조하는데, layout 을
    // 생성자(cytoscape({layout})) 안에 넣으면 animate:false cose 가 생성자 안에서 동기로 stop 을 발화 ->
    // 그 시점 cy 변수는 아직 미대입(null) -> cy.nodes() throw -> render 중단(그래프·버튼 둘 다 죽음).
    cy = cytoscape({
      container: el,
      elements: elements,
      style: STYLE,
      minZoom: 0.3,
      maxZoom: 2.5,
      // 휠·트랙패드 두 손가락 스크롤 줌 비활성 — 페이지 스크롤이 토폴로지 줌으로 가로채이는 것 방지.
      // 줌은 +/- 버튼(cy.zoom(), 프로그래밍 줌이라 영향 없음)으로만. 드래그 pan 은 유지.
      userZoomingEnabled: false,
      userPanningEnabled: true,
    });

    var layoutOpts = allCached
      ? { name: 'preset', fit: true, padding: 24, positions: function (n) { return savedPos[n.id()]; } }
      : { name: 'cose', animate: false, padding: 24, nodeRepulsion: 6000, idealEdgeLength: 90 };

    var lay = cy.layout(layoutOpts);
    if (!allCached) {
      // cy 대입 후 실행이라 layoutstop 시점 cy 유효 — 노드 위치 캐시(폴링 재렌더 배치 고정).
      lay.one('layoutstop', function () {
        cy.nodes().forEach(function (n) {
          var p = n.position();
          savedPos[n.id()] = { x: p.x, y: p.y };
        });
      });
    }
    lay.run();

    cy.on('tap', 'node[kind = "host"]', function (evt) {
      var pid = evt.target.data('publicId');
      if (pid) window.location.href = '/servers/' + pid;
    });

    bindZoomControls();
  }

  // 확대/축소·전체보기 버튼 -> cy.zoom()/fit (minZoom·maxZoom 자동 클램프). 뷰포트 중앙 기준 줌.
  // 버튼은 fragment 안이라 폴링 swap 마다 새 엘리먼트 — render()에서 매번 재바인딩(onclick 속성이라 중복 listener 0).
  function bindZoomControls() {
    document.querySelectorAll('[data-topo-zoom]').forEach(function (btn) {
      var action = btn.getAttribute('data-topo-zoom');
      btn.onclick = function () {
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

  window.NetworkTopology = { render: render };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', render);
  } else {
    render();
  }
})();
