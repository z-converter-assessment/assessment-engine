/* 토스트 알림 — install/export/report 등 작업 결과 일관 표시.
 *
 * ToastUtils.show(message, kind, duration_ms?)
 *   kind: 'ok' | 'warn' | 'err' | 'pending'
 *     warn = advisory(비차단) — 성공했으나 운영자가 알아야 할 조건(예: 오프라인 호스트 큐 적재)
 *   duration_ms: 기본 4200ms (HTML 예시 동일). 0 전달 시 자동 dismiss 안 함 (수동 close)
 *   pending kind는 자동 dismiss 안 함 — 작업 완료 후 ok/err 호출로 갱신
 *
 * 화면 우측 상단 고정 위치. 동시 N개 호출 시 스택 누적.
 */
(function () {
  const TOAST_CONTAINER_ID = 'toast-container';
  const DEFAULT_DURATION_MS = 4200;

  const KIND_STYLES = {
    ok:      { bg: '#f0fdf4', color: '#166534', border: '#bbf7d0' },
    warn:    { bg: '#fffbeb', color: '#92400e', border: '#fde68a' },
    err:     { bg: '#fef2f2', color: '#991b1b', border: '#fecaca' },
    pending: { bg: '#f1f5f9', color: '#64748b', border: '#e2e8f0' },
  };

  function ensureContainer() {
    let c = document.getElementById(TOAST_CONTAINER_ID);
    if (c) return c;
    c = document.createElement('div');
    c.id = TOAST_CONTAINER_ID;
    c.style.cssText = 'position:fixed; top:20px; right:20px; z-index:100; display:flex; flex-direction:column; gap:8px; max-width:420px;';
    document.body.appendChild(c);
    return c;
  }

  function show(message, kind, durationMs) {
    const container = ensureContainer();
    const style = KIND_STYLES[kind] || KIND_STYLES.pending;
    const dur = (durationMs === undefined) ? DEFAULT_DURATION_MS : durationMs;

    const toast = document.createElement('div');
    toast.style.cssText = `
      background:${style.bg}; color:${style.color}; border:1px solid ${style.border};
      padding:12px 14px; border-radius:8px; font-size:13px; line-height:1.5;
      box-shadow:0 4px 12px rgba(0,0,0,0.08); cursor:pointer;
      animation:toast-in 0.2s ease-out;
    `;
    toast.innerHTML = message;
    toast.addEventListener('click', () => toast.remove());
    container.appendChild(toast);

    // pending은 자동 dismiss 안 함 — 호출자가 ok/err로 갱신 호출 필요
    if (kind !== 'pending' && dur > 0) {
      setTimeout(() => toast.remove(), dur);
    }
    return toast;
  }

  function dismissAll() {
    const c = document.getElementById(TOAST_CONTAINER_ID);
    if (c) c.innerHTML = '';
  }

  // 페이드인 keyframes 1회 주입
  if (!document.getElementById('toast-keyframes')) {
    const st = document.createElement('style');
    st.id = 'toast-keyframes';
    st.textContent = '@keyframes toast-in { from { opacity:0; transform:translateY(-8px); } to { opacity:1; transform:translateY(0); } }';
    document.head.appendChild(st);
  }

  window.ToastUtils = { show, dismissAll };
})();
