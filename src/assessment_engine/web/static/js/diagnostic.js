// 진단 SSR latest 카드 + 모달 발행 (ADR 0004 단계 4·UI 통일).
//
// 사용: 페이지에 다음 HTML을 두고 본 스크립트를 defer로 로드.
//   <div class="card" data-diagnostic-panel data-scope="server" data-server-public-id="...">
//     <h2>서버 진단</h2>
//     <button class="diag-submit btn">서버 진단</button>
//     <div class="diag-status"></div>
//     <div class="diag-result"></div>
//     <div class="diag-modal" style="display:none; ...">
//       <select class="diag-range">...</select>
//       <input type="datetime-local" class="diag-anchor">
//       <button class="diag-confirm">발행</button>
//       <button class="diag-cancel">취소</button>
//     </div>
//     <script type="application/json">...SSR latest payload...</script>
//   </div>
//
// 흐름:
// 1. page load → SSR inline JSON으로 latest 결과 즉시 렌더 (없으면 빈 카드)
// 2. 진단 버튼 클릭 → 모달 열기 (anchor input 자동 init)
// 3. 모달 confirm → POST → 결과 페이지(`/diagnostics?ids=...`)로 이동
// 4. 결과 페이지의 diagnostic-results.js가 polling으로 완료까지 추적
//
// list batch 모달(#diagnose-modal)과 동일 UX 패턴 — 운영자 학습 비용 0.
// E1 P3 — 본 JS는 SSR 결과 표시 + 모달 토글 + form submit만. polling은 결과 페이지가 담당.

(function () {
  'use strict';

  const RANGE_LABEL_KR = {
    "15m": "15분", "1h": "1시간", "6h": "6시간",
    "24h": "24시간", "7d": "7일", "14d": "14일", "30d": "30일",
  };

  function fmtWindow(period) {
    if (!period) return '';
    const tr = period.time_range;
    if (tr && RANGE_LABEL_KR[tr]) return RANGE_LABEL_KR[tr];
    const d = period.days;
    if (d == null) return '14일';
    if (d >= 1) return Math.floor(d) + '일';
    return Math.round(d * 24) + '시간';
  }

  const CLASSIFICATION_KR = {
    idle:              'idle',
    shutdown:          'shutdown 검토',
    over_provisioned:  'over-provisioned',
    under_provisioned: 'under-provisioned',
    optimal:           'optimal',
    insufficient_data: '데이터 부족',
  };

  const CLASSIFICATION_BADGE = {
    idle:              'badge-warn',
    shutdown:          'badge-warn',
    over_provisioned:  'badge-warn',
    under_provisioned: 'badge-danger',
    optimal:           'badge-ok',
    insufficient_data: 'badge',
  };

  let _anchorIdSeq = 0;

  class DiagnosticPanel {
    constructor(rootEl) {
      this.root = rootEl;
      this.scope = rootEl.dataset.scope;
      this.serverPublicId = rootEl.dataset.serverPublicId || null;
      this.button = rootEl.querySelector('.diag-submit');
      this.statusEl = rootEl.querySelector('.diag-status');
      this.resultEl = rootEl.querySelector('.diag-result');

      // 모달 elements (카드 안 inline 모달)
      this.modal = rootEl.querySelector('.diag-modal');
      this.rangeSel = rootEl.querySelector('.diag-range');
      this.anchorInput = rootEl.querySelector('.diag-anchor');
      this.confirmBtn = rootEl.querySelector('.diag-confirm');
      this.cancelBtn = rootEl.querySelector('.diag-cancel');

      // anchor input은 id 필요 — ChartUtils.initAnchor가 getElementById 기반
      if (this.anchorInput && !this.anchorInput.id) {
        this.anchorInput.id = 'diag-anchor-' + (++_anchorIdSeq);
      }
      // 페이지 로드 시점에 anchor 기본값 채움 — 모달 open 시 reset 안 함 (사용자 변경값 보존).
      if (this.anchorInput && window.ChartUtils && window.ChartUtils.initAnchor) {
        window.ChartUtils.initAnchor(this.anchorInput.id);
      }

      // 카드 안 모달이 있으면 (server scope detail) 클릭 → 모달 open.
      // 모달 없으면 (list env card 본문 inline form) 클릭 → 즉시 submit.
      this.button.addEventListener('click', () => {
        if (this.modal) this._openModal();
        else this.submit();
      });
      if (this.confirmBtn) this.confirmBtn.addEventListener('click', () => this.submit());
      if (this.cancelBtn) this.cancelBtn.addEventListener('click', () => this._closeModal());
      if (this.modal) {
        this.modal.addEventListener('click', e => { if (e.target === this.modal) this._closeModal(); });
      }
      this._renderInitialFromSsr();
    }

    _openModal() {
      if (!this.modal) return;
      this.modal.style.display = 'flex';
    }

    _closeModal() {
      if (this.modal) this.modal.style.display = 'none';
    }

    _renderInitialFromSsr() {
      const dataEl = this.root.querySelector('script[type="application/json"]');
      if (!dataEl) return;
      try {
        const job = JSON.parse(dataEl.textContent);
        this._render(job);
      } catch (e) {
        // 무시 — 빈 카드 그대로
      }
    }

    async submit() {
      // modal 흐름은 confirmBtn 비활성, inline form 흐름은 메인 button 비활성.
      const activeBtn = this.confirmBtn || this.button;
      activeBtn.disabled = true;
      const prevStatus = this.statusEl.textContent;
      this.statusEl.textContent = '진단 요청 중...';

      const time_range = this.rangeSel ? this.rangeSel.value : '14d';
      let anchor_at = null;
      if (this.anchorInput && window.ChartUtils && window.ChartUtils.getAnchorEnd) {
        const d = window.ChartUtils.getAnchorEnd(this.anchorInput.id);
        if (d) anchor_at = d.toISOString();
      }

      const body = { scope: this.scope, time_range, anchor_at };
      if (this.scope === 'server') {
        body.server_ids = [this.serverPublicId];
      }

      try {
        const res = await fetch('/api/v1/diagnostics', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!res.ok) {
          const text = await res.text();
          throw new Error(`HTTP ${res.status} ${text.slice(0, 200)}`);
        }
        const data = await res.json();
        const ids = (data.job_ids || []).join(',');
        if (!ids) throw new Error('job_id not returned');
        // navigate 전 모달 close — 결과 페이지에서 뒤로가기 시 BFCache 복원되어도 모달 떠있지 않게.
        this._closeModal();
        // 결과 페이지로 이동 — polling은 거기서 진행
        window.location.href = `/diagnostics?ids=${encodeURIComponent(ids)}`;
      } catch (e) {
        // 에러는 toast 로 — 카드 본문에 영구 노출되지 않게 (사용자 의도). statusEl 은 이전 상태 복원.
        this.statusEl.textContent = prevStatus || '';
        if (window.ToastUtils) {
          ToastUtils.show(`AI 진단 발행 실패: ${e.message}`, 'err');
        }
        activeBtn.disabled = false;
      }
    }

    _render(job) {
      if (job.status === 'succeeded' && job.result) {
        const finishedAtKst = (window.ChartUtils && job.finished_at)
          ? window.ChartUtils.fmtKst(job.finished_at)
          : (job.finished_at || '');
        this.statusEl.textContent = `최근 진단: ${finishedAtKst}`;
        this.resultEl.innerHTML = this.scope === 'server'
          ? renderServerResult(job.result)
          : renderEnvironmentResult(job.result);
      } else if (job.status === 'failed') {
        this.statusEl.textContent = '최근 진단 실패';
        this.resultEl.innerHTML = `<div style="color:#991b1b; padding:12px; background:#fee2e2; border-radius:6px;">${escapeHtml(job.error_message || '알 수 없는 오류')}</div>`;
      }
      // pending/running은 SSR latest 흐름에 없음 — get_latest_by_context이 succeeded만 반환
    }
  }

  function renderServerResult(r) {
    const use = r.use_method || {};
    const cpu = use.cpu || {};
    const mem = use.memory || {};
    const cls = r.classification || 'insufficient_data';
    const clsKr = CLASSIFICATION_KR[cls] || cls;
    const clsBadge = CLASSIFICATION_BADGE[cls] || 'badge';
    const rec = r.recommendation || {};
    const period = r.period_window || {};

    return `
      <div style="display:flex; align-items:center; gap:12px; margin-bottom:14px;">
        <span class="badge ${clsBadge}">${clsKr}</span>
        <span style="color:#64748b; font-size:12px;">평가 윈도우 ${fmtWindow(period)}</span>
        <span style="color:#64748b; font-size:12px;">|</span>
        <span style="color:#64748b; font-size:12px;">추천 액션: <strong>${escapeHtml(rec.action || 'n/a')}</strong></span>
      </div>
      <table style="margin-bottom:14px;">
        <thead><tr><th>리소스</th><th style="text-align:right;">p95 (%)</th><th style="text-align:right;">peak (%)</th></tr></thead>
        <tbody>
          <tr><td>CPU</td><td style="text-align:right;">${fmtNum(cpu.p95)}</td><td style="text-align:right;">${fmtNum(cpu.peak)}</td></tr>
          <tr><td>Memory</td><td style="text-align:right;">${fmtNum(mem.p95)}</td><td style="text-align:right;">${fmtNum(mem.peak)}</td></tr>
        </tbody>
      </table>
      <div style="padding:12px; background:#f8fafc; border-radius:6px; line-height:1.6;">${escapeHtml(r.narrative || '')}</div>
    `;
  }

  function renderEnvironmentResult(r) {
    const cov = r.coverage || {};
    const cls = r.classification || {};
    const period = r.period_window || {};
    const topActions = r.top_actions || [];

    const classRows = [
      ['over_provisioned',  cls.over_provisioned],
      ['under_provisioned', cls.under_provisioned],
      ['idle',              cls.idle],
      ['swap_pressure',     cls.swap_pressure],
      ['optimal',           cls.optimal],
    ].map(([key, v]) => `
      <tr>
        <td>${CLASSIFICATION_KR[key] || key}</td>
        <td style="text-align:right;">${(v && v.count) || 0}</td>
      </tr>
    `).join('');

    const topActionsList = topActions.map(a => `
      <tr><td>${escapeHtml(a.action_type)}</td><td style="text-align:right;">${a.count}</td></tr>
    `).join('') || '<tr><td colspan="2" style="color:#94a3b8;">권장 액션 없음</td></tr>';

    return `
      <div style="display:flex; align-items:center; gap:12px; margin-bottom:14px;">
        <span style="color:#64748b; font-size:12px;">평가 윈도우 ${fmtWindow(period)}</span>
        <span style="color:#64748b; font-size:12px;">|</span>
        <span style="color:#64748b; font-size:12px;">대상 ${cov.evaluated_servers || 0}대 / 전체 ${cov.total_servers || 0}대 (${cov.coverage_pct || 0}%)</span>
      </div>
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:14px;">
        <table>
          <thead><tr><th>분류</th><th style="text-align:right;">서버 수</th></tr></thead>
          <tbody>${classRows}</tbody>
        </table>
        <table>
          <thead><tr><th>권장 액션</th><th style="text-align:right;">서버 수</th></tr></thead>
          <tbody>${topActionsList}</tbody>
        </table>
      </div>
      <div style="padding:12px; background:#f8fafc; border-radius:6px; line-height:1.6;">${escapeHtml(r.narrative || '')}</div>
    `;
  }

  function fmtNum(v) {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'number') return v.toFixed(1);
    return String(v);
  }

  function escapeHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-diagnostic-panel]').forEach(el => new DiagnosticPanel(el));
  });

  // BFCache 복원 시(진단 발행 후 결과 페이지에서 뒤로가기) fresh SSR 보장 — 옛 SSR(빈 카드) 그대로 복원 회피.
  // panel 있는 페이지(list/server detail)만 reload — 다른 페이지는 정상 BFCache 활용.
  window.addEventListener('pageshow', e => {
    if (e.persisted && document.querySelector('[data-diagnostic-panel]')) {
      location.reload();
    }
  });
})();
