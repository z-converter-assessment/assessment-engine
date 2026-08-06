/*
 * SignalUtils — 포화 스냅샷 신호 렌더러 (자원 상세 탭 실시간 카드 공통).
 *
 * 실시간 신호는 순간값이라 평가(포화다/아니다)를 매기지 않는다 — 값·임계만 중립 노출(P4, 계산·판정 0).
 * 평가(임계 이상 여부·색 강조)는 14일 창 기준 "자원 이용률·포화" 카드(SSR, period_rows 매크로) 단일 소스가
 * 전담 — 실시간(순간 index)과 14일(p95 기준선)은 서로 다른 창이라, 실시간에 평가를 얹으면 14일 판정과
 * 혼동을 준다. 여기선 값·임계·가용성(N/A·표본 부족)만 표시, saturated 불리언은 렌더에 관여 안 함.
 * 4상태: measured(값/임계) · no_data(미측정 "—") · not_applicable(N/A) · insufficient(표본 부족).
 */
const SignalUtils = (() => {
  /** @type {Record<string, string>} */
  const UNIT_LABEL = { per_core: '/core', ms: 'ms', '%': '%', '/s': '/s', ops: '' };

  /**
   * 표식 — 비측정 가용성 상태 중 "표본 부족"만. not_applicable 은 값 자체가 이미 "N/A" 라 뱃지 중복 표기 안
   * 함(사유는 hover). 임계 이상도 값 색(sat-val-hi, 글로벌 주황) 단일 신호라 뱃지 없음 — 값 텍스트가 "—"뿐인
   * insufficient 상태만 뱃지가 유일한 정보 전달 수단.
   * @param {import('./generated/api').components['schemas']['SaturationSignal']} sig
   */
  function marker(sig) {
    switch (sig.state) {
      case 'insufficient':
        return { text: '표본 부족', cls: 'badge badge-muted' };
      default:
        // measured·not_applicable·no_data — 값 텍스트(N/A·—·실측값)만으로 충분, 뱃지 중복 없음.
        return null;
    }
  }

  /** 값 (임계 X단위) — 값 주인공, 임계+단위는 괄호 보조. not_applicable 은 "N/A"만(사유는 hover, sig.na_reason).
   * @param {import('./generated/api').components['schemas']['SaturationSignal']} sig */
  function valueText(sig) {
    if (sig.state === 'not_applicable') return 'N/A';
    if (sig.state !== 'measured' || sig.value == null) return '—';
    const u = UNIT_LABEL[sig.unit || ''] ?? (sig.unit || '');
    if (sig.threshold != null) {
      // 임계 0(페이징 등 이벤트 기반 — 발생 자체가 신호)은 "임계 0" 이 무의미 -> 단위 컨텍스트만 "(0/s)".
      const inner = sig.threshold === 0 ? sig.threshold + u : '임계 ' + sig.threshold + u;
      return sig.value + ' (' + inner + ')';
    }
    return u ? sig.value + ' ' + u : String(sig.value);
  }

  /** HTML 속성 값 이스케이프 (detail hover 안전). @param {string} s */
  function esc(s) {
    return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  /**
   * 포화 신호 리스트를 컨테이너에 렌더 — 각 신호 = .sat-line(라벨 + 값/임계, 근거 hover). 평가 없음(중립색
   * 고정) — 임계 이상 색 강조·상태 요약줄은 14일 카드(period_rows) 몫이라 여기선 안 함(순간과 창이 다름).
   * @param {HTMLElement | null} container
   * @param {ReadonlyArray<import('./generated/api').components['schemas']['SaturationSignal']> | null | undefined} signals
   */
  function renderSaturation(container, signals) {
    if (!container) return;
    const list = Array.isArray(signals) ? signals : [];
    container.innerHTML = list
      .map((sig) => {
        const m = marker(sig);
        const titleText = sig.detail || sig.na_reason;
        const title = titleText ? ` title="${esc(titleText)}"` : '';
        const muted = sig.state === 'measured' ? '' : ' sat-val-muted';
        const tag = m ? `<span class="${m.cls} sat-badge">${m.text}</span>` : '';
        return (
          `<div class="sat-line"${title}>` +
          `<span class="sat-name">${esc(sig.label)}</span>` +
          `<span class="sat-val${muted}">${esc(valueText(sig))}</span>` +
          tag +
          `</div>`
        );
      })
      .join('');
  }

  /**
   * 활동(I/O) 라인 렌더 — 디스크 어태치별 R/W(+IOPS), 네트워크 인터페이스별 RX/TX. 임계 없는 활동값이라 중립색.
   * 이용률·포화와 나란한 자원 3번째 축(활동/처리량). 물리 device/interface 만(서버가 이미 필터).
   * @param {HTMLElement | null} container
   * @param {ReadonlyArray<any> | null | undefined} items
   * @param {'disk' | 'net'} kind
   */
  function renderActivity(container, items, kind) {
    if (!container) return;
    const list = Array.isArray(items) ? items : [];
    if (list.length === 0) {
      container.innerHTML = '<span class="sat-val-muted">—</span>';
      return;
    }
    const fmt = (window.ChartUtils && window.ChartUtils.fmtThroughput) || ((/** @type {number|null} */ v) => (v == null ? '—' : String(v)));
    const iops = (/** @type {number|null} */ v) => (v == null ? '' : ` (${Math.round(v)} IOPS)`);
    container.innerHTML = list
      .map((it) => {
        // 표처럼 — device | 읽기/수신 | 쓰기/송신 3열 한 줄(부모 grid 가 열 정렬). 풀네임.
        let dev, l1, l2;
        if (kind === 'disk') {
          dev = it.device;
          l1 = `읽기 ${fmt(it.read_kbps)}${iops(it.read_iops)}`;
          l2 = `쓰기 ${fmt(it.write_kbps)}${iops(it.write_iops)}`;
        } else {
          dev = it.interface;
          l1 = `수신 ${fmt(it.rx_kbps)}`;
          l2 = `송신 ${fmt(it.tx_kbps)}`;
        }
        return (
          `<div class="act-line"><span class="act-dev">${esc(dev)}</span>` +
          `<span class="act-rw">${l1}</span><span class="act-rw">${l2}</span></div>`
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
      case 'not_applicable':
        return { text: 'N/A', cls: 'badge badge-muted' }; // 이 OS 구조적 미지원 — no_data 와 구분(hover 로 사유)
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

  return { renderSaturation, renderErrors, renderActivity };
})();
window.SignalUtils = SignalUtils;
