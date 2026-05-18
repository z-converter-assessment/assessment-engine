// 보고서 이력 페이지 "더 보기" — cursor 기반 페이징.
// 첫 20건 SSR 후 fetch /reports/history.json?cursor=... 로 다음 20건 append.
// next_cursor 가 null 이면 버튼 자동 숨김 (마지막 페이지).

(function () {
  const moreBtn = document.getElementById('report-history-more');
  const tbody = document.getElementById('report-history-tbody');
  const table = document.getElementById('report-history-table');
  if (!moreBtn || !tbody || !table) return;

  const view = table.dataset.view || 'all';
  const days = table.dataset.days || '90';
  const initialServerIds = moreBtn.dataset.serverPublicIds || '';

  function fmtKstFromIso(iso) {
    const d = new Date(iso);
    return d.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', hour12: false });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function renderRow(it) {
    const tr = document.createElement('tr');
    const target = it.scope === 'environment'
      ? `환경 전체 (${it.server_count}대)`
      : `선택 ${it.server_count}대`;
    tr.innerHTML = `
      <td style="white-space:nowrap;">${escapeHtml(fmtKstFromIso(it.created_at))}</td>
      <td>${escapeHtml(it.view_label)}</td>
      <td>${target}</td>
      <td>${escapeHtml(it.window_label)}</td>
      <td><a href="${escapeHtml(it.result_link)}" class="btn" style="font-size:12px; padding:4px 10px;">보기 →</a></td>
    `;
    return tr;
  }

  async function loadMore() {
    const cursor = moreBtn.dataset.cursor;
    if (!cursor) return;
    moreBtn.disabled = true;
    moreBtn.textContent = '불러오는 중...';
    try {
      const params = new URLSearchParams();
      params.set('days', days);
      params.set('view', view);
      params.set('cursor', cursor);
      if (initialServerIds) {
        for (const sid of initialServerIds.split(',')) {
          if (sid) params.append('server_public_ids', sid);
        }
      }
      const res = await fetch(`/reports/history.json?${params.toString()}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const items = Array.isArray(data.items) ? data.items : [];
      for (const it of items) {
        tbody.appendChild(renderRow(it));
      }
      if (data.next_cursor) {
        moreBtn.dataset.cursor = data.next_cursor;
        moreBtn.disabled = false;
        moreBtn.textContent = '더 보기';
      } else {
        moreBtn.remove();
      }
    } catch (e) {
      moreBtn.disabled = false;
      moreBtn.textContent = '재시도 (' + e.message + ')';
    }
  }

  moreBtn.addEventListener('click', loadMore);

  // 전체 보기 — next_cursor 가 null 될 때까지 반복 fetch + append.
  const allBtn = document.getElementById('report-history-all');
  if (allBtn) {
    allBtn.addEventListener('click', async () => {
      allBtn.disabled = true;
      moreBtn.disabled = true;
      allBtn.textContent = '전체 불러오는 중...';
      try {
        while (moreBtn.dataset.cursor) {
          await loadMore();
        }
        allBtn.remove();
      } catch (e) {
        allBtn.disabled = false;
        moreBtn.disabled = false;
        allBtn.textContent = '재시도 (' + e.message + ')';
      }
    });
  }
})();
