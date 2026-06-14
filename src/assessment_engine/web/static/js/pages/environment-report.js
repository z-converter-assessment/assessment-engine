/**
 * 환경 보고서 live preview 발행 컨트롤(reports/environment.html, job 없는 preview 전용).
 *
 * 보고서 양식(고객/엔지니어)·윈도우·앵커 select 는 발행 버튼 누를 때만 반영 — 변경 자체로는 navigate 안 함(발행 전 본문·설명 없음).
 * 발행 버튼 -> POST /reports/environment/emit?view=&time_range=&anchor_at= -> {view_url} navigate (PRG).
 * 앵커 초기값은 URL anchor_at(이미 KST 문자열) 우선, 없으면 현재(ChartUtils.initAnchor) — KST 변환 연산 0(F2).
 * 외부 의존: chart-utils.js(ChartUtils.initAnchor), toast(ToastUtils).
 */
(function () {
  const viewSel = document.getElementById('report-view');
  const range = document.getElementById('report-range');
  const anchor = document.getElementById('report-anchor');
  const emit = document.getElementById('report-emit');
  if (!range || !anchor || !viewSel) return; // 발행된 스냅샷(컨트롤 없음)이면 skip

  // 앵커 초기값 — URL anchor_at("YYYY-MM-DDTHH:MM:SS+09:00", 이미 KST) 앞 16자만, 없으면 현재.
  const urlAnchor = new URLSearchParams(location.search).get('anchor_at');
  if (urlAnchor) anchor.value = urlAnchor.slice(0, 16);
  else if (window.ChartUtils && window.ChartUtils.initAnchor) window.ChartUtils.initAnchor('report-anchor');

  function buildParams() {
    const p = new URLSearchParams();
    p.set('view', viewSel.value);
    p.set('time_range', range.value);
    if (anchor.value) p.set('anchor_at', anchor.value + ':00+09:00');
    return p.toString();
  }

  // 발행 -> POST emit -> 발행된 스냅샷으로 navigate. (select 변경은 navigate 없이 발행 시점 값만 사용.)
  if (emit) {
    emit.addEventListener('click', async function () {
      emit.disabled = true;
      const pending = window.ToastUtils ? window.ToastUtils.show('보고서 발행 중...', 'pending') : null;
      try {
        const res = await fetch('/reports/environment/emit?' + buildParams(), { method: 'POST' });
        if (pending) pending.remove();
        if (!res.ok) {
          let msg;
          try { msg = (await res.json()).detail; } catch (_) { msg = '요청을 처리하지 못했습니다.'; }
          if (window.ToastUtils) window.ToastUtils.show('발행 실패: ' + (msg || '요청을 처리하지 못했습니다.'), 'err');
          emit.disabled = false;
          return;
        }
        const data = await res.json();
        window.location.href = data.view_url;
      } catch (e) {
        if (pending) pending.remove();
        if (window.ToastUtils) window.ToastUtils.show('발행 요청 실패: ' + e.message, 'err');
        emit.disabled = false;
      }
    });
  }
})();
