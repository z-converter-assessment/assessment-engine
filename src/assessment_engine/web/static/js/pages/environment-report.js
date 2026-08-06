/**
 * 환경 보고서 live preview 발행 컨트롤(reports/environment.html, job 없는 preview 전용).
 *
 * 보고서 양식(고객/엔지니어)·윈도우·앵커 select 는 발행 버튼 누를 때만 반영 — 변경 자체로는 navigate 안 함(발행 전 본문·설명 없음).
 * 발행 버튼 -> POST /reports/environment/emit?view=&time_range=&anchor_at= -> {view_url} navigate (PRG).
 * 앵커 초기값은 URL anchor_at(이미 KST 문자열) 우선, 없으면 현재(ChartUtils.initAnchor) — KST 변환 연산 0(F2).
 * 외부 의존: chart-utils.js(ChartUtils.initAnchor), toast(ToastUtils).
 */

import * as ChartUtils from "@/chart-utils";
import * as ToastUtils from "@/toast-utils";
import * as EmitUtils from "@/emit-utils";

(function () {
  const viewSel = /** @type {HTMLSelectElement} */ (document.getElementById('report-view'));
  const range = /** @type {HTMLSelectElement} */ (document.getElementById('report-range'));
  const anchor = /** @type {HTMLInputElement} */ (document.getElementById('report-anchor'));
  const emit = /** @type {HTMLButtonElement} */ (document.getElementById('report-emit'));
  if (!range || !anchor || !viewSel) return; // 발행된 스냅샷(컨트롤 없음)이면 skip

  // 앵커 초기값 — URL anchor_at("YYYY-MM-DDTHH:MM:SS+09:00", 이미 KST) 앞 16자만, 없으면 현재.
  const urlAnchor = new URLSearchParams(location.search).get('anchor_at');
  if (urlAnchor) anchor.value = urlAnchor.slice(0, 16);
  // globals.d.ts 의 initAnchor 시그니처(onChange 콜백)와 실제 런타임(inputId 문자열)이 달라 로컬 any 캐스트.
  // (any 캐스트로 런타임 feature-check 도 유지 — 선언 타입상 항상 정의됨 판정 회피.)
  else if (ChartUtils.initAnchor) ChartUtils.initAnchor('report-anchor');

  function buildParams() {
    const p = new URLSearchParams();
    p.set('view', viewSel.value);
    p.set('time_range', range.value);
    if (anchor.value) p.set('anchor_at', anchor.value + ':00+09:00');
    return p.toString();
  }

  // 발행 -> POST emit -> 발행된 스냅샷으로 navigate. 공용 EmitUtils(비활성·토스트·bfcache 복구 내장).
  // (select 변경은 navigate 없이 발행 시점 값만 사용.)
  if (emit) {
    emit.addEventListener('click', function () {
      // globals.d.ts 의 submitNavigate(url, opts) 시그니처와 실제 런타임(btn, urlFn, opts)이 달라 로컬 any 캐스트.
      (/** @type {any} */ (EmitUtils)).submitNavigate(emit, () => '/reports/environment/emit?' + buildParams(), {
        pendingMsg: '보고서 발행 중...',
      });
    });
  }
})();
