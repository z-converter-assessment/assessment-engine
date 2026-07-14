// @ts-check
/*
 * 환경 부하 추이 차트 — 보고서(정적 스냅샷) inline 렌더.
 *
 * #env-trend-chart canvas 의 data-trend(JSON: [{at, cpu, mem}])·data-range 를 읽어 Chart.js 라인 차트.
 * 보고서는 정적이라 toggle 없음 — 발행 시점 윈도우 고정. 대시보드 live 버전은 별도(fetch).
 * 외부 의존: Chart.js(CDN, 본 파일보다 먼저 defer 로드) + ChartUtils(base.html). 미로드 시 silent skip.
 *
 * P4 (라이브러리 인터랙션 예외) — 차트 옵션 조립·라벨 포매팅만. 비즈니스 임계 분류 없음.
 */
(function () {
  function render() {
    var el = /** @type {HTMLCanvasElement | null} */ (document.getElementById('env-trend-chart'));
    if (!el || typeof Chart === 'undefined' || typeof ChartUtils === 'undefined') return;
    var raw = el.getAttribute('data-trend');
    var range = el.getAttribute('data-range') || '7d';
    // 가이드선(grid) — 대시보드만 표시(data-grid="true"), 보고서는 미표시. 범례는 양쪽 동일(토글 제거).
    var showGrid = el.getAttribute('data-grid') === 'true';
    if (!raw) return;
    /** @type {Array<{ at: string, [k: string]: any }>} */
    var pts;
    try {
      pts = JSON.parse(raw);
    } catch (e) {
      return;
    }
    if (!Array.isArray(pts) || !pts.length) return;

    // 윈도우 전체 고정 그리드 — 서버 상세 차트와 동일 정책(makeBucketGrid + joinToGrid).
    // 빈 구간은 null(gap), 최신(마지막 pt)이 오른쪽 끝. 데이터 있는 범위만 그리던 옛 방식 폐기.
    var bucketKey = ChartUtils.AUTO_BUCKET[range] || '6h';
    var bMs = ChartUtils.BUCKET_MS[bucketKey];
    var anchor = new Date(pts[pts.length - 1].at);
    // 로컬 캐스트 — globals.d.ts 의 makeBucketGrid 선언(number,number,number)이 실제
    // 시그니처(rangeKey,bucketKey,anchorEnd)와 어긋나 우회. globals_issue 로 보고.
    var grid = /** @type {any} */ (ChartUtils).makeBucketGrid(range, bucketKey, anchor);
    var labels = grid.map(function (/** @type {number} */ t) {
      return ChartUtils.fmtLabel(new Date(t).toISOString(), range);
    });
    /** @param {string} key */
    function series(key) {
      var rows = pts.map(function (/** @type {{ at: string, [k: string]: any }} */ p) {
        return { collected_at: p.at, value: p[key] };
      });
      // 로컬 캐스트 — globals.d.ts joinToGrid 는 2인자 선언이나 실제는 (grid,rows,bMs) 3인자. globals_issue 보고.
      return /** @type {any} */ (ChartUtils).joinToGrid(grid, rows, bMs);
    }
    var cpu = series('cpu');
    var mem = series('mem');
    var disk = series('disk');

    // 대시보드 자동갱신 fragment swap 후 재호출 대비 — 기존 인스턴스 정리(중복·메모리 누수 방지).
    var existing = Chart.getChart(el);
    if (existing) existing.destroy();
    new Chart(el, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [
          {
            label: 'CPU 평균',
            data: cpu,
            // 로컬 캐스트 — globals.d.ts themeColor(name:string) 이나 실제는 무인자. globals_issue 보고.
            borderColor: /** @type {any} */ (ChartUtils).themeColor(),
            backgroundColor: 'transparent',
            tension: 0.2,
            spanGaps: false,
            pointRadius: 0,
            borderWidth: 1.5,
          },
          {
            label: '메모리 평균',
            data: mem,
            borderColor: '#f59e0b',
            backgroundColor: 'transparent',
            tension: 0.2,
            spanGaps: false,
            pointRadius: 0,
            borderWidth: 1.5,
          },
          {
            label: '디스크 평균',
            data: disk,
            borderColor: '#22c55e',
            backgroundColor: 'transparent',
            tension: 0.2,
            spanGaps: false,
            pointRadius: 0,
            borderWidth: 1.5,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        // 성능 추이 차트 모양 — 가이드선(grid) 없음, x축 시간 표기 적당 간격(maxTicksLimit).
        scales: {
          x: { ticks: { maxTicksLimit: 10, font: { size: 11 }, color: '#94a3b8' }, grid: { display: showGrid, color: '#f1f5f9' } },
          y: {
            beginAtZero: true,
            suggestedMax: 100,
            ticks: { callback: function (v) { return Number(v).toFixed(1) + '%'; }, font: { size: 11 }, color: '#64748b' },
            grid: { display: showGrid, color: '#f1f5f9' },
          },
        },
        // 범례: 네트워크 토폴로지 범례와 동일 모양 — 원형 dot + 라벨(usePointStyle circle, color #64748b).
        // 체크/언체크(클릭 토글) 기능은 제거 (onClick no-op).
        plugins: { legend: { position: 'bottom', onClick: function () {}, labels: { usePointStyle: true, pointStyle: 'circle', boxWidth: 8, boxHeight: 8, font: { size: 11 }, color: '#64748b' } } },
      },
    });
  }

  // 포화 추이(3축 이진 0/1) — 개별 서버 보고서(engineer) "부하 추이" 옆 카드. cpu.saturation/
  // mem.paging_pressure/disk.saturation 은 이미 0.0/1.0 판정(서버 상세 단일 진실 이식, #F10 창 일관) —
  // 본 렌더러는 3계열을 한 차트에 구분해 보이기 위한 순수 시각 표현(lane 오프셋)만 담당, 재분류 0(P4).
  /** @type {Record<string, number>} */
  var LANE = { cpu: 2, mem: 1, disk: 0 }; // 위->CPU, 중간->메모리, 아래->디스크 I/O (왼쪽 부하 추이 범례 순서 정합)
  /** @type {Record<number, string>} */
  var LANE_LABEL = { 2: 'CPU 실행 큐', 1: '메모리 페이징', 0: '디스크 I/O' };
  var LANE_STEP = 1.1; // band 간격 — 0.8 높이 + 0.3 여백으로 인접 lane 시각 분리
  var LANE_HEIGHT = 0.8;

  /** @param {number} lane @param {number | null} v */
  function laneOffset(lane, v) {
    return v === null || v === undefined ? null : lane * LANE_STEP + v * LANE_HEIGHT;
  }

  /** @param {string} hex @param {number} alpha */
  function withAlpha(hex, alpha) {
    var h = hex.replace('#', '');
    if (h.length === 3) h = h.split('').map(function (c) { return c + c; }).join('');
    var r = parseInt(h.substring(0, 2), 16);
    var g = parseInt(h.substring(2, 4), 16);
    var b = parseInt(h.substring(4, 6), 16);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
  }

  function renderSaturation() {
    var el = /** @type {HTMLCanvasElement | null} */ (document.getElementById('sat-trend-chart'));
    if (!el || typeof Chart === 'undefined' || typeof ChartUtils === 'undefined') return;
    var raw = el.getAttribute('data-sat-trend');
    var range = el.getAttribute('data-range') || '7d';
    if (!raw) return;
    /** @type {Array<{ at: string, [k: string]: any }>} */
    var pts;
    try {
      pts = JSON.parse(raw);
    } catch (e) {
      return;
    }
    if (!Array.isArray(pts) || !pts.length) return;

    var bucketKey = ChartUtils.AUTO_BUCKET[range] || '6h';
    var bMs = ChartUtils.BUCKET_MS[bucketKey];
    var anchor = new Date(pts[pts.length - 1].at);
    var grid = /** @type {any} */ (ChartUtils).makeBucketGrid(range, bucketKey, anchor);
    var labels = grid.map(function (/** @type {number} */ t) {
      return ChartUtils.fmtLabel(new Date(t).toISOString(), range);
    });
    /** @param {string} key */
    function rawSeries(key) {
      var rows = pts.map(function (/** @type {{ at: string, [k: string]: any }} */ p) {
        return { collected_at: p.at, value: p[key] };
      });
      return /** @type {any} */ (ChartUtils).joinToGrid(grid, rows, bMs);
    }
    var cpuRaw = rawSeries('cpu_sat');
    var memRaw = rawSeries('mem_sat');
    var diskRaw = rawSeries('disk_sat');

    /** @param {any[]} rawValues @param {number} lane */
    function laneData(rawValues, lane) {
      return rawValues.map(function (/** @type {number | null} */ v) { return laneOffset(lane, v); });
    }

    // 이진 상태 가시성 — 선만으로는 "포화 순간"이 눈에 잘 안 띈다는 지적 반영. lane 바닥부터 stepped 선까지
    // 옅게 채워(fill:{value: lane 바닥}) 포화 구간을 면적으로 강조 — Gantt 류 상태 타임라인과 동일 관용.
    var cpuColor = /** @type {any} */ (ChartUtils).themeColor();
    var memColor = '#f59e0b';
    var diskColor = '#22c55e';
    var existing = Chart.getChart(el);
    if (existing) existing.destroy();
    new Chart(el, {
      type: 'line',
      data: {
        labels: labels,
        datasets: /** @type {any} */ ([
          {
            label: 'CPU 실행 큐 포화',
            data: laneData(cpuRaw, LANE.cpu),
            rawValues: cpuRaw,
            borderColor: cpuColor,
            backgroundColor: withAlpha(cpuColor, 0.35),
            fill: { value: LANE.cpu * LANE_STEP },
            stepped: 'before',
            spanGaps: false,
            pointRadius: 0,
            borderWidth: 1.5,
          },
          {
            label: '메모리 페이징 압박',
            data: laneData(memRaw, LANE.mem),
            rawValues: memRaw,
            borderColor: memColor,
            backgroundColor: withAlpha(memColor, 0.35),
            fill: { value: LANE.mem * LANE_STEP },
            stepped: 'before',
            spanGaps: false,
            pointRadius: 0,
            borderWidth: 1.5,
          },
          {
            label: '디스크 I/O 포화',
            data: laneData(diskRaw, LANE.disk),
            rawValues: diskRaw,
            borderColor: diskColor,
            backgroundColor: withAlpha(diskColor, 0.35),
            fill: { value: LANE.disk * LANE_STEP },
            stepped: 'before',
            spanGaps: false,
            pointRadius: 0,
            borderWidth: 1.5,
          },
        ]),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: { ticks: { maxTicksLimit: 10, font: { size: 11 }, color: '#94a3b8' }, grid: { display: false } },
          y: {
            min: 0,
            max: LANE.cpu * LANE_STEP + LANE_HEIGHT,
            afterBuildTicks: function (/** @type {any} */ axis) {
              axis.ticks = [0, 1, 2].map(function (lane) { return { value: lane * LANE_STEP + LANE_HEIGHT / 2 }; });
            },
            ticks: {
              callback: function (/** @type {string | number} */ value) {
                return LANE_LABEL[Math.round((Number(value) - LANE_HEIGHT / 2) / LANE_STEP)] || '';
              },
              font: { size: 11 }, color: '#64748b',
            },
            grid: { color: '#f1f5f9' },
          },
        },
        plugins: {
          legend: { position: 'bottom', onClick: function () {}, labels: { usePointStyle: true, pointStyle: 'circle', boxWidth: 8, boxHeight: 8, font: { size: 11 }, color: '#64748b' } },
          tooltip: {
            callbacks: {
              label: function (/** @type {any} */ ctx) {
                var real = ctx.dataset.rawValues ? ctx.dataset.rawValues[ctx.dataIndex] : null;
                var state = real === 1 ? '포화' : real === 0 ? '정상' : '—';
                return ctx.dataset.label + ': ' + state;
              },
            },
          },
        },
      },
    });
  }

  // 대시보드 자동갱신(list-table.js)이 fragment swap 후 재호출 — 보고서는 1회 렌더.
  // 로컬 캐스트 — EnvTrend 는 globals.d.ts Window 에 미선언(프로젝트 전역). globals_issue 보고.
  /** @type {any} */ (window).EnvTrend = { render: render, renderSaturation: renderSaturation };
  if (document.readyState !== 'loading') {
    render();
    renderSaturation();
  } else {
    document.addEventListener('DOMContentLoaded', function () {
      render();
      renderSaturation();
    });
  }
})();
