// @ts-check
/* 발행/제출 -> POST -> 응답 view_url navigate 공용 헬퍼 (EmitUtils.submitNavigate).
 *
 * "버튼 disabled -> POST -> view_url 로 이동" 패턴을 한 곳으로 모은다. 이 패턴을 파일마다 복붙하면
 * bfcache(뒤로가기 캐시) 복원 시 버튼이 disabled 로 남는 먹통 fix 를 하나씩 빠뜨린다(재발 원인).
 * 헬퍼가 그 복구를 내장 — 발행 흐름(환경/서버/선택 N대 리포트 emit)이 전부 같은 동작을 공유한다.
 *
 * EmitUtils.submitNavigate(btn, urlFn, opts)
 *   btn    : 발행 버튼 요소
 *   urlFn  : () => POST 할 URL 문자열 (params 조립은 호출자 몫)
 *   opts   : { pendingMsg?, errPrefix?, viewUrlTransform?(url, data), onRestore?() }
 *   동작: btn 비활성 -> pending 토스트 -> POST -> !ok 면 에러 토스트+재활성 / ok 면 view_url(변환) navigate.
 *         bfcache 복원(pageshow persisted) 시 btn 재활성 + onRestore 실행 — 버튼당 1회 arm(먹통 방지 내장).
 * 외부 의존: window.ToastUtils (선택 — 없으면 토스트 생략).
 */
(function () {
  /**
   * @param {HTMLButtonElement} btn
   * @param {(() => void) | undefined} onRestore
   */
  function armRestore(btn, onRestore) {
    if (btn.dataset.emitRestoreArmed) return; // 버튼당 1회만 등록 (재클릭 시 리스너 중복 방지)
    btn.dataset.emitRestoreArmed = '1';
    window.addEventListener('pageshow', function (e) {
      if (!e.persisted) return; // bfcache 복원일 때만 — 일반 로드는 init 이 이미 정상 상태
      btn.disabled = false;
      if (onRestore) onRestore();
    });
  }

  /**
   * @param {HTMLButtonElement | null} btn
   * @param {() => string} urlFn
   * @param {any} opts
   */
  async function submitNavigate(btn, urlFn, opts) {
    opts = opts || {};
    if (!btn || btn.disabled) return; // 이미 진행 중이면 무시 (더블클릭 방지)
    armRestore(btn, opts.onRestore);
    btn.disabled = true;
    const pending = opts.pendingMsg && window.ToastUtils ? window.ToastUtils.show(opts.pendingMsg, 'pending') : null;
    try {
      const res = await fetch(urlFn(), { method: 'POST' });
      if (pending) pending.remove();
      if (!res.ok) {
        let msg = null;
        try { msg = (await res.json()).detail; } catch (_) { /* non-json 응답 */ }
        if (window.ToastUtils) {
          window.ToastUtils.show((opts.errPrefix || '발행 실패') + ': ' + (msg || '요청을 처리하지 못했습니다.'), 'err');
        }
        btn.disabled = false;
        return;
      }
      const data = await res.json();
      let url = data.view_url;
      if (opts.viewUrlTransform) url = opts.viewUrlTransform(url, data);
      window.location.href = url;
    } catch (e) {
      if (pending) pending.remove();
      if (window.ToastUtils) window.ToastUtils.show((opts.errPrefix || '발행 요청 실패') + ': ' + (/** @type {Error} */ (e)).message, 'err');
      btn.disabled = false;
    }
  }

  window.EmitUtils = { submitNavigate };
})();
