/**
 * 서버 목록 화면 risk 카드 — Chart.js doughnut으로 CPU/MEM/DISK p95 시각화.
 *
 * - 임계 색상은 mapper의 _USAGE_DANGER_PCT(90) / _USAGE_WARN_PCT(75)와 동기화.
 * - data-pct=-1은 데이터 없음 — 회색 도넛 + "—" 표시.
 * - 가운데 라벨/% 표시는 absolute 오버레이 (Chart.js center-text 플러그인 회피).
 * - 외부 fetch 없음 — server-side에서 ViewModel로 모든 값 주입 (P5).
 */

const COLOR_DANGER = '#ef4444';
const COLOR_WARN   = '#f59e0b';
const COLOR_OK     = '#22c55e';
const COLOR_EMPTY  = '#cbd5e1';
const COLOR_BG     = '#f1f5f9';
const USAGE_DANGER_PCT = 90;
const USAGE_WARN_PCT   = 75;

function pickColor(pct) {
  if (pct < 0)              return COLOR_EMPTY;
  if (pct >= USAGE_DANGER_PCT) return COLOR_DANGER;
  if (pct >= USAGE_WARN_PCT)   return COLOR_WARN;
  return COLOR_OK;
}

function renderDonut(canvas) {
  const pct = parseFloat(canvas.dataset.pct);
  const label = canvas.dataset.label;
  const noData = pct < 0 || Number.isNaN(pct);
  const drawnPct = noData ? 0 : Math.min(100, Math.max(0, pct));
  const color = pickColor(pct);

  new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels: ['used', 'free'],
      datasets: [{
        data: noData ? [0, 100] : [drawnPct, 100 - drawnPct],
        backgroundColor: [color, COLOR_BG],
        borderWidth: 0,
      }],
    },
    options: {
      cutout: '70%',
      animation: { duration: 300 },
      plugins: {
        legend:  { display: false },
        tooltip: { enabled: false },
      },
      responsive: true,
      maintainAspectRatio: false,
    },
  });

  // 가운데 텍스트 — absolute 오버레이 (canvas 부모 cell이 position:relative)
  const overlay = document.createElement('div');
  overlay.style.cssText = [
    'position:absolute', 'inset:0',
    'display:flex', 'flex-direction:column',
    'align-items:center', 'justify-content:center',
    'pointer-events:none', 'font-family:system-ui, sans-serif',
  ].join(';');
  overlay.innerHTML = `
    <div style="font-size:16px; font-weight:600; color:${noData ? '#94a3b8' : '#1e293b'}; line-height:1;">
      ${noData ? '—' : Math.round(pct)}<span style="font-size:10px; font-weight:400;">${noData ? '' : '%'}</span>
    </div>
    <div style="font-size:10px; color:#64748b; margin-top:2px;">${label}</div>
  `;
  canvas.parentElement.appendChild(overlay);
}

document.addEventListener('DOMContentLoaded', () => {
  if (typeof Chart === 'undefined') {
    console.warn('Chart.js 미로드 — risk 카드 도넛 표시 안 됨');
    return;
  }
  document.querySelectorAll('.risk-donut-cell canvas').forEach(renderDonut);
});
