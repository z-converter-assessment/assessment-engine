// 진단 결과 페이지 (`/diagnostics?ids=...`) — N개 카드 polling + render (ADR 0004 단계 2).
//
// diagnostic.js의 DiagnosticPanel은 submit 흐름. 본 모듈은 polling만 — submit 없음.
// 초기 SSR JSON으로 즉시 렌더, pending/running만 3초 polling.

(function () {
  'use strict';

  const POLL_INTERVAL_MS = 3000;
  const POLL_TIMEOUT_MS = 5 * 60 * 1000;

  const STAGE_LABEL = {
    queued:               '대기 중',
    extracting_stats:     '통계 추출 중',
    applying_rules:       '추천 판정 중',
    retrieving_context:   '관련 자료 검색 중',
    generating_narrative: 'AI 분석 작성 중',
  };

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

  class DiagnosticResultCard {
    constructor(rootEl) {
      this.root = rootEl;
      this.jobId = rootEl.dataset.jobId;
      this.statusEl = rootEl.querySelector('.diag-status');
      this.metaEl = rootEl.querySelector('.diag-meta');
      this.resultEl = rootEl.querySelector('.diag-result');
      this.poller = null;
      this.timeoutAt = 0;

      const initial = this._readInline();
      if (initial) {
        this._render(initial);
        if (initial.status === 'pending' || initial.status === 'running') {
          this._startPolling();
        }
      } else {
        // SSR에서 record 못 찾음 — job_id로 polling 시도
        this.statusEl.textContent = '조회 중...';
        this._startPolling();
      }
    }

    _readInline() {
      const el = this.root.querySelector('script.diag-initial[type="application/json"]');
      if (!el) return null;
      try {
        return JSON.parse(el.textContent);
      } catch (e) {
        return null;
      }
    }

    _startPolling() {
      this.timeoutAt = Date.now() + POLL_TIMEOUT_MS;
      const tick = async () => {
        if (Date.now() > this.timeoutAt) {
          this._stopPolling();
          this.statusEl.textContent = '시간 초과';
          return;
        }
        try {
          const res = await fetch(`/api/diagnostics/${this.jobId}`);
          if (!res.ok) {
            if (res.status === 404) {
              this._stopPolling();
              this.statusEl.textContent = '진단 job을 찾을 수 없음';
              return;
            }
            throw new Error(`HTTP ${res.status}`);
          }
          const job = await res.json();
          this._render(job);
          if (job.status === 'succeeded' || job.status === 'failed') {
            this._stopPolling();
          }
        } catch (e) {
          this._stopPolling();
          this.statusEl.textContent = '조회 실패: ' + e.message;
        }
      };
      this.poller = setInterval(tick, POLL_INTERVAL_MS);
      tick();
    }

    _stopPolling() {
      if (this.poller) {
        clearInterval(this.poller);
        this.poller = null;
      }
    }

    _render(job) {
      this._renderStatusBadge(job);
      this._renderMeta(job);

      if (job.status === 'succeeded' && job.result) {
        this.resultEl.innerHTML = job.scope === 'server'
          ? renderServerResult(job.result)
          : renderEnvironmentResult(job.result);
      } else if (job.status === 'failed') {
        this.resultEl.innerHTML = `<div style="color:#991b1b; padding:12px; background:#fee2e2; border-radius:6px;">${escapeHtml(job.error_message || '알 수 없는 오류')}</div>`;
      } else {
        // pending/running — 단계 표시만, 결과 영역은 비어 있음
        this.resultEl.innerHTML = '';
      }
    }

    _renderStatusBadge(job) {
      const cls = {
        pending:   'badge',
        running:   'badge-warn',
        succeeded: 'badge-ok',
        failed:    'badge-danger',
      }[job.status] || 'badge';
      let label;
      if (job.status === 'pending' || job.status === 'running') {
        label = STAGE_LABEL[job.progress_stage] || job.status;
      } else {
        label = job.status;
      }
      this.statusEl.className = 'diag-status badge ' + cls;
      this.statusEl.textContent = label;
    }

    _renderMeta(job) {
      const parts = [];
      if (job.time_range) parts.push('윈도우 ' + (RANGE_LABEL_KR[job.time_range] || job.time_range));
      if (job.anchor_at) {
        const at = window.ChartUtils && window.ChartUtils.fmtKst
          ? window.ChartUtils.fmtKst(job.anchor_at)
          : job.anchor_at;
        parts.push('anchor ' + at);
      }
      if (job.finished_at) {
        const at = window.ChartUtils && window.ChartUtils.fmtKst
          ? window.ChartUtils.fmtKst(job.finished_at)
          : job.finished_at;
        parts.push('완료 ' + at);
      }
      this.metaEl.textContent = parts.join(' | ');
    }
  }

  function renderServerResult(r) {
    const server = r.server || {};
    const use = r.use_method || {};
    const cpu = use.cpu || {};
    const mem = use.memory || {};
    const cls = r.classification || 'insufficient_data';
    const clsKr = CLASSIFICATION_KR[cls] || cls;
    const clsBadge = CLASSIFICATION_BADGE[cls] || 'badge';
    const rec = r.recommendation || {};
    return `
      <div style="display:flex; align-items:center; gap:12px; margin-bottom:14px;">
        <span class="badge ${clsBadge}">${clsKr}</span>
        <span class="text-muted">추천 액션: <strong>${escapeHtml(rec.action || 'n/a')}</strong></span>
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
      <div class="text-muted" style="margin-bottom:14px;">
        대상 ${cov.evaluated_servers || 0}대 / 전체 ${cov.total_servers || 0}대 (${cov.coverage_pct || 0}%)
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
    document.querySelectorAll('[data-diagnostic-result]').forEach(el => new DiagnosticResultCard(el));
  });
})();
