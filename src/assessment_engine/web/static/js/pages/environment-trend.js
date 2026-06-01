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
    var el = document.getElementById('env-trend-chart');
    if (!el || typeof Chart === 'undefined' || typeof ChartUtils === 'undefined') return;
    var raw = el.getAttribute('data-trend');
    var range = el.getAttribute('data-range') || '14d';
    // 가이드선(grid) — 대시보드만 표시(data-grid="true"), 보고서는 미표시. 범례는 양쪽 동일(토글 제거).
    var showGrid = el.getAttribute('data-grid') === 'true';
    if (!raw) return;
    var pts;
    try {
      pts = JSON.parse(raw);
    } catch (e) {
      return;
    }
    if (!Array.isArray(pts) || !pts.length) return;

    var labels = pts.map(function (p) {
      return ChartUtils.fmtLabel(p.at, range);
    });
    var cpu = pts.map(function (p) {
      return p.cpu;
    });
    var mem = pts.map(function (p) {
      return p.mem;
    });
    var disk = pts.map(function (p) {
      return p.disk;
    });

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
            borderColor: '#3b82f6',
            backgroundColor: 'transparent',
            tension: 0.2,
            spanGaps: true,
            pointRadius: 0,
            borderWidth: 1.5,
          },
          {
            label: '메모리 평균',
            data: mem,
            borderColor: '#f59e0b',
            backgroundColor: 'transparent',
            tension: 0.2,
            spanGaps: true,
            pointRadius: 0,
            borderWidth: 1.5,
          },
          {
            label: '디스크 평균',
            data: disk,
            borderColor: '#22c55e',
            backgroundColor: 'transparent',
            tension: 0.2,
            spanGaps: true,
            pointRadius: 0,
            borderWidth: 1.5,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        // 성능 리포트 차트 모양 — 가이드선(grid) 없음, x축 시간 표기 적당 간격(maxTicksLimit).
        scales: {
          x: { ticks: { maxTicksLimit: 10, font: { size: 11 }, color: '#94a3b8' }, grid: { display: showGrid, color: '#f1f5f9' } },
          y: {
            beginAtZero: true,
            suggestedMax: 100,
            ticks: { callback: function (v) { return v + '%'; }, font: { size: 11 }, color: '#64748b' },
            grid: { display: showGrid, color: '#f1f5f9' },
          },
        },
        // 범례: 활성/비활성 범례 모양 그대로, 체크/언체크(클릭 토글) 기능만 제거 (onClick no-op).
        plugins: { legend: { position: 'bottom', onClick: function () {}, labels: { boxWidth: 12, font: { size: 11 } } } },
      },
    });
  }

  // 대시보드 자동갱신(list.js)이 fragment swap 후 재호출 — 보고서는 1회 렌더.
  window.EnvTrend = { render: render };
  if (document.readyState !== 'loading') render();
  else document.addEventListener('DOMContentLoaded', render);
})();
