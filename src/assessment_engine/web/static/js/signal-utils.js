// @ts-check
/*
 * SignalUtils — 포화 스냅샷 신호 렌더러 (개요·자원 탭 스냅샷 카드 공통).
 *
 * 서버가 os-aware 판정을 끝낸 구조화 SaturationSignal(값·임계·saturated·4상태)을 그대로 렌더만 한다(P4).
 * 임계 재계산·os 분기·양 OS 설명 인라인 금지 — 이 호스트 OS 값만 오고, 근거는 hover(detail)로.
 * 상세는 라이브 모니터링이라 신호를 데이터(값/임계)로 보여준다 — per-signal "포화" verdict 를 박지 않는다.
 * 포화 판정은 dual-gate(신호 AND 이용률, 자원 평가 14일 창) 단일 소스 몫. 여기선 임계 이상 신호만 중립 표식.
 * 4상태: measured(값/임계 · 임계 이상이면 표식) · no_data(수집 대기) · not_applicable(N/A) · insufficient(표본 부족).
 */
const SignalUtils = (() => {
  /** @type {Record<string, string>} */
  const UNIT_LABEL = { per_core: '/core', ms: 'ms', '%': '%', '/s': '/s', ops: '' };

  /**
   * 표식 — 측정 신호는 verdict 아님: 임계 이상이면 중립 표식(badge-warn), 임계 이내면 표식 없음(null).
   * 비측정은 가용성 상태(수집 대기/N/A/표본 부족). "포화" 단어는 dual-gate 판정 전용이라 여기 안 씀.
   * @param {import('./generated/api').components['schemas']['SaturationSignal']} sig
   */
  function marker(sig) {
    switch (sig.state) {
      case 'measured':
        return sig.saturated ? { text: '임계 이상', cls: 'badge badge-warn' } : null;
      case 'not_applicable':
        return { text: 'N/A', cls: 'badge badge-muted' };
      case 'insufficient':
        return { text: '표본 부족', cls: 'badge badge-muted' };
      default:
        return { text: '수집 대기', cls: 'badge badge-muted' }; // no_data
    }
  }

  /** @param {import('./generated/api').components['schemas']['SaturationSignal']} sig */
  function valueText(sig) {
    if (sig.state === 'not_applicable') return sig.na_reason || 'N/A';
    if (sig.state !== 'measured' || sig.value == null) return '—';
    const u = UNIT_LABEL[sig.unit || ''] ?? (sig.unit || '');
    const suffix = u ? ' ' + u : '';
    const thr = sig.threshold != null ? ' / ' + sig.threshold + suffix : suffix;
    return sig.value + thr;
  }

  /** HTML 속성 값 이스케이프 (detail hover 안전). @param {string} s */
  function esc(s) {
    return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  /**
   * 포화 신호 리스트를 컨테이너에 렌더 — 각 신호 = .sat-line(라벨 + 값/임계 + 임계 이상 표식, 근거 hover).
   * 임계 이상 신호는 값 강조색(sat-val-hi) + 중립 표식. 임계 이내는 표식 없이 값만. 포화 verdict 표기 안 함.
   * @param {HTMLElement | null} container
   * @param {ReadonlyArray<import('./generated/api').components['schemas']['SaturationSignal']> | null | undefined} signals
   */
  function renderSaturation(container, signals) {
    if (!container) return;
    const list = Array.isArray(signals) ? signals : [];
    container.innerHTML = list
      .map((sig) => {
        const m = marker(sig);
        const title = sig.detail ? ` title="${esc(sig.detail)}"` : '';
        const hi = sig.state === 'measured' && sig.saturated ? ' sat-val-hi' : '';
        const muted = sig.state === 'measured' ? '' : ' sat-val-muted';
        const tag = m ? `<span class="${m.cls} sat-badge">${m.text}</span>` : '';
        return (
          `<div class="sat-line"${title}>` +
          `<span class="sat-name">${esc(sig.label)}</span>` +
          `<span class="sat-val${hi}${muted}">${esc(valueText(sig))}</span>` +
          tag +
          `</div>`
        );
      })
      .join('');
  }

  /** @param {import('./generated/api').components['schemas']['ErrorSignal']} e */
  function errBadge(e) {
    switch (e.state) {
      case 'occurred':
        return { text: e.count != null ? e.count + '건' : '발생', cls: 'badge badge-danger' };
      case 'clean':
        return { text: '이상 없음', cls: 'badge badge-ok' };
      default:
        return { text: '수집 대기', cls: 'badge badge-muted' }; // no_data
    }
  }

  /**
   * 에러 축 표시자 렌더 — 카운트형(정상=0 발화 E9). 발생 시 카운트·종류·창 노출.
   * @param {HTMLElement | null} container
   * @param {ReadonlyArray<import('./generated/api').components['schemas']['ErrorSignal']> | null | undefined} errors
   */
  function renderErrors(container, errors) {
    if (!container) return;
    const list = Array.isArray(errors) ? errors : [];
    container.innerHTML = list
      .map((e) => {
        const b = errBadge(e);
        const title = e.detail ? ` title="${esc(e.detail)}"` : '';
        const occurred = e.state === 'occurred';
        const ctx = occurred && e.context ? `<span class="err-ctx">${esc(e.context)}</span>` : '';
        const win = occurred && e.window_label ? `<span class="err-win">${esc(e.window_label)}</span>` : '';
        return (
          `<div class="err-item"${title}>` +
          `<span class="err-label">${esc(e.label)}</span>` +
          `<span class="${b.cls} sat-badge">${b.text}</span>` +
          ctx +
          win +
          `</div>`
        );
      })
      .join('');
  }

  return { renderSaturation, renderErrors };
})();
window.SignalUtils = SignalUtils;
