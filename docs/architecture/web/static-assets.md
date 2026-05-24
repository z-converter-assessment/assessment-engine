# Web 정적 자원 — JS·차트 UI·표준 컴포넌트

정책: CLAUDE.md #E6 (JS 외부화 의무) · #E8 (차트·도넛 UI, P4) · #F5 (외부화 강제 채널). 본 문서는 JS 디렉토리·`ChartUtils` API·P4 5 의무 규약·차트 UI·report.html print CSS·base.html 표준 컴포넌트 카탈로그·네비게이션 규약 단일 진실.

```
src/assessment_engine/web/static/js/
├── chart-utils.js              ← base.html에서 단일 로드, 전역 ChartUtils
└── pages/
    ├── cpu.js / memory.js / storage.js / network.js / performance.js
    └── list.js                 ← 서버 목록 4 액션 (발견·Install·Export·보고서)
```

## ChartUtils API (chart-utils.js)

`base.html` `<head>`에서 단일 로드 → 전역 `ChartUtils`. 페이지 .js가 destructure.

| 함수 | 용도 |
|------|------|
| `RANGE_LABEL` / `AUTO_BUCKET` / `BUCKET_LABEL` / `RANGE_MS` / `BUCKET_MS` / `COLORS` | 매핑·상수 |
| `fmtKst(iso)` | KST 시각 포맷 (`YYYY-MM-DD HH:MM:SS`) — server `kst` 필터와 동일, "시간 표기" 절 단일 진실 |
| `fmtLabel(iso, range)` | 차트 X축 라벨 (range 별 `MM/DD HH:MM` / `HH:MM`) — 표시 시각과 별개 컨텍스트 |
| `fmtKbChart(v)` | bytes/sec → kB/MB/s |
| `getAnchorEnd(inputId)` / `initAnchor(inputId)` | datetime input 처리 |
| `makeBucketGrid(range, bucket, anchor)` / `joinToGrid(grid, rows, bMs)` | 버킷 그리드 + 응답 join |
| `bindToggle(groupId, onChange)` | range/agg 토글 바인딩 |
| `initSse(serverId, onMessage)` | SSE 초기화 + dot 표시 |
| `safeArray(arr)` | `Array.isArray` 방어 (P4 c) |
| `fetchRebootEvents(serverId, range, anchor)` | reboot/restart 이벤트 fetch (vertical marker용) |
| `applyRebootMarkers(chart, events, gridMs)` | 차트 인스턴스에 marker 옵션 주입 + redraw |
| `rebootMarkersPlugin` | Chart.js 글로벌 plugin (`afterDraw`로 dashed line 그림). chart-utils 로드 시 자동 등록 |

## 페이지별 .js 패턴

| 페이지 | 차트 인스턴스 패턴 |
|--------|-------------------|
| `performance.js` | `chartInstances` 객체 + 통합 `loadAllCharts()` — 한 곳에서 모든 차트에 marker 일괄 적용 |
| `cpu/memory/network/storage.js` | 차트 인스턴스 개별 변수 + 차트별 range/anchor — 각 loader 끝에 marker 적용 |
| `list.js` | 4 모달 (서버 발견 / Install / JSON Export / 보고서). 차트 없음 — 체크박스 + bulk action |

## P4 차트 JS 5 의무 규약 (loader 표준)

| 규약 | 내용 |
|------|------|
| (a) sequence counter | `let xxxSeq=0; const seq=++xxxSeq; ... if (seq !== xxxSeq) return;` — stale 응답 폐기 |
| (b) capture-before-await | range·anchor를 `await` 직전 로컬 캡처 → 인자로 전달 |
| (c) Array.isArray | 5xx가 JSON object 반환 가능 — `safeArray()` 사용 |
| (d) 404 분기 | `/metrics/latest` 등 데이터 부재는 `res.status === 404` 분기 |
| (e) suggestedMax 명명 상수 | `PERF_IOPS_SUGGESTED_MAX = 200` 형식 + 임계값 색상도 `USAGE_DANGER_PCT` 등 명명 상수 |

5개 페이지 모두 (a)~(e) 적용. `performance.js`가 11개 차트 loader 모두 `(seq, capturedRange, capturedAnchor)` 시그니처 표준.

## 차트 UI 디테일

### Y축 정책
- 추이 차트 (cpu/network 페이지): 분해력 우선 — 작은 값도 보이게 `suggestedMax` 낮게
- 진단 리포트 (performance 페이지): 절대 기준 — `PERF_IOPS_SUGGESTED_MAX=200`(HDD 한계) / `PERF_NET_SUGGESTED_MAX=10MB/s`(1Gbps 8%)

### avg + max ghost 패턴
1차 dataset = avg (visible). 2차 dataset = max (`borderColor:'transparent'`, `realData` 보유) — tooltip에서 `realData`로 max 표시. legend는 짝수 인덱스만.

### Reboot/Restart marker
plugin이 `chart.options.plugins.rebootMarkers.events`를 `afterDraw`에서 그림. 색상: `reboot=#ef4444`, `restart=#f59e0b`. 라벨 위치: chartArea top + 11px.

## report.html print CSS

```css
@media print {
  .report-section { page-break-inside: avoid; }
  .kpi-grid { page-break-inside: avoid; }
  body { background: #fff; }
}
```

`.no-print` 클래스로 navbar/검색폼/버튼 인쇄 시 숨김 (base.html). 컨설턴트가 브라우저 인쇄 → PDF/PPT 캡처. 백엔드 PDF export는 미도입 (`docs/tradeoffs.md` 참조).

## 의존성

| 도구 | 로드 |
|------|------|
| Chart.js (`chart.umd.min.js`) | 차트 페이지에서 `<script src=...>` |
| ChartUtils | base.html `<head>`에서 단일 로드 |

번들 도구 미도입 — IIFE 노출 패턴 (`docs/tradeoffs.md` T9).

## 표준 컴포넌트 카탈로그 (base.html)

원칙: 새 페이지 추가 시 아래 카탈로그의 클래스 먼저 적용. 같은 패턴을 inline 으로 재구현 금지 — 표준에 없으면 base.html 에 새 표준 추가 후 사용. P2 (mapper 단일 진실)·P3 (템플릿 순수 렌더링) 와 동급의 표시 계층 규약.

### 보조 컴포넌트 폰트 (h1/h2/h3 외)

본 표는 표시 컴포넌트 (KPI / metric / badge / 버튼) 만 — h1/h2/h3 + .page-meta / .section-meta 위계 제목은 별도 "폰트 위계 — 단일 진실" 절 참조.

| 슬롯 | 값 | 용도 |
|------|----|------|
| .stat-value | 20px / 700 | 실시간 메트릭 dashboard 값 |
| .metric-value | 24px / 700 / #1e293b | 보고서·KPI 큰 값 |
| .metric-value-md | 18px / 700 / #1e293b | 보고서 보조 값 (긴 텍스트 위주) |
| body | 14px / #1a1a1a | 일반 본문 |
| .btn / .btn-action / .btn-print | 13px / 500 | 버튼 |
| .stat-label | 11px / uppercase / #94a3b8 | stat-box 라벨 |
| .metric-label | 12px / #64748b | metric-card 라벨 |
| .metric-sub | 11px / #94a3b8 | metric-card 부가 문구 |
| .badge | 12px / 600 | 분류·카테고리 표시 |
| .rec-badge | 11px / 600 | table cell 안 분류 badge (좁은 셀용) |
| code | 12px / monospace | inline code (식별자) |

금지: 9px·10px·32px 등 카탈로그 외 값을 inline 으로 박지 않음. 새 위계가 필요하면 base.html 에 명명 클래스 추가 후 사용.

### 박스 컴포넌트 (대형부터)

| 클래스 | 용도 | bg / border |
|--------|------|-------------|
| `.card` | 카드 컨테이너 (페이지 최상위 단위) | #fff / shadow |
| `.kpi` + `.kpi-grid` + `.kpi-grid-{2,3,4}` | KPI 카운트 카드 (12px label + 24px strong) | #fff / 1px #e2e8f0 |
| `.metric-card` + `.metric-grid` + `.metric-grid-{2,3}` | 보고서 메트릭 카드 (12px label + 24/18px value + 11px sub) | transparent / 1px #e2e8f0 |
| `.stat-box` + `.stat-grid` | 실시간 메트릭 dashboard (작은 grid, soft bg) | #f8fafc |
| `.alert-warn` + `.alert-list` | 운영 신호 발화 박스 (warn 톤) | #fef3c7 / 1px #fde68a |

금지: `<div style="border:1px solid #e2e8f0; border-radius:6px; padding:14px;">` 같은 inline 박스 재구현. 위 클래스로 치환. (P3 직접 위반 — 모양 통일성 + 추후 일괄 조정 시 단일 진실.)

### Badge 카탈로그

| 클래스 | 용도 |
|--------|------|
| `.badge` | base (변형 클래스와 함께) |
| `.badge-ok` / `.badge-warn` / `.badge-danger` | semantic 상태 |
| `.badge-cat-{web,db,cache,mq,container,monitor,unknown}` | 서비스 카테고리 |
| `.rec-{under_provisioned,over_provisioned,optimal,idle,shutdown,right_size,swap,success,failure,pending,unknown,insufficient_data}` | 분류 결과 |
| `.attn-active` | 운영 신호 발화 |
| `.rec-badge` | table cell 안 컴팩트 badge (위 `.rec-*` 와 함께) |

금지: inline `style="background:#fef3c7; color:#92400e; padding:2px 8px; border-radius:4px;"` 같은 직접 색·padding 으로 badge 재현. 새 색·새 의미가 필요하면 base.html `.rec-*` 또는 `.badge-*` 변형 추가 후 사용.

### Label 컴포넌트

| 클래스 | 용도 |
|--------|------|
| `.kpi-label` | KPI 그리드 위 카테고리 라벨 (11px uppercase #94a3b8) |
| `.kpi-label-sub` | 라벨 옆 부가 문구 (400, #cbd5e1) |
| `.section-title` | 카드 안 sub-section 구분 (13px 600, top border) |

## 폰트 위계 — 단일 진실 (예외 0 의무)

base.html `<style>` 정의 — 모든 page 가 같은 위계 사용. inline `style="font-size:...; font-weight:..."` 로 override 금지.

| 위계 | 정의 | 용도 |
|------|------|------|
| `h1` | 20px / 700 / #0f172a | 페이지 제목 (대시보드 / 환경 보고서 / 서버 상세) — 페이지당 1 개 |
| `h2` | 16px / 700 / #1e293b | 카드 섹션 제목 (요약 / 환경 메트릭 / Right-sizing 분류 등) |
| `h3` | 13px / 600 / #475569 | 소제목 (환경 현황 / 분포 / 조치 필요 호스트 등) — h2 보다 영향력 약함 |
| `.page-meta` | 14px / 400 / #64748b / margin-left 8px | h1 옆 sub-text (예: view_title 양식 라벨) |
| `.section-meta` | 12px / 400 / #94a3b8 / margin-left 8px | h2 옆 sub-text (예: 윈도우 / 부가 설명) |

금지:
- `<h2 style="font-size:14px; color:#475569;">` 같은 inline style override — base 단일 진실 위반.
- `<h3 style="margin:0 0 10px; font-size:14px;">` — h3 가 h2 size 와 같으면 위계 깨짐.
- h1/h2/h3 외 임의 `<div style="font-size:18px; font-weight:700;">` 같은 의사-제목 — h1/h2/h3 사용 의무.
- modal 안 제목은 별개 (`#title-id style="..."` 인라인 허용 — modal 컴포넌트 별도 위계).

## 폰트 체 — sans-serif 단일 + monospace 식별자 한정

base.html `<body>` 가 system-ui sans-serif 기본. 모든 텍스트 동일 family. 별도 폰트 (serif / display) 안 씀.

monospace 적용 위치 (예외 0 의무):
- 식별자: hostname / UUID / IP address / file path / unit name (예: nginx.service)
- agent path / mount path (예: /var/lib/postgresql/data)
- inline code 표시 (`<code>` element)

class:
- `<code>...</code>` — inline code 표시 (background + padding + monospace, base 단일 진실)
- `.identifier` — code element 아닌 식별자에 monospace 만 적용 (background 없음, 표 cell 내부 등)

금지:
- 일반 텍스트 (제목 / 본문 / 라벨) 에 monospace 적용 — 식별자 외 monospace 가독성 ↓.
- 숫자 (vCPU / GB / %) 에 monospace — sans-serif weight 700 으로 정렬·강조 충분.
- inline `style="font-family:monospace"` — base `.identifier` 또는 `<code>` 사용 의무.

## P3 정공 예외 — 1회 fetch vs polling 흐름

P3 (Jinja2 template 단일 진실) 의 1차 정공 = JS HTML 합성 폐기, server fragment endpoint + JS DOM 교체.

다만 2 case 는 본 정공 미적용 — overhead 또는 latency 의무 큼:

| case | 정공 / 예외 | 이유 |
|------|-------------|------|
| 1회 fetch + render (예: task-modal body) | 정공 — fragment endpoint (`/api/tasks/{id}/detail`) + JS `innerHTML = await fetch().text()` | overhead 0, P3 완전 정공 |
| polling 흐름 (예: detail page metrics/latest SSE / storage snapshot / diagnostic-results.js · diagnostic-inline.js result polling) | 예외 — JS template literal 허용 (P4 와 같은 dynamic 인터랙션 도메인) | polling 마다 HTML fragment fetch 시 overhead 큼. JSON polling + JS render 가 정공 |

폴링 흐름 JS render 의무:
- inline `style="color:#xxx"` 금지 — base.html 색 전용 유틸 (중립 톤 `.text-strong`/`.text-label`/`.text-muted`/`.text-meta`/`.text-faint` + 의미색 `.text-danger`/`.text-ok`/`.text-warn`/`.text-attn`, 모두 color-only · size 는 부모 상속) 사용. font-size 는 위계 제목·컴포넌트 클래스(`.stat-*`/`.metric-*`/`.kpi-*`/`.text-narrative`/`.pre-output`) 우선 — size 전용 유틸 미도입(위계·컴포넌트 의미 우선).
- layout 관련 inline style (display:flex / grid / table 등) 허용 — 모듈별 부수 정렬, utility class 화 강제 X.
- 동일 데이터의 SSR template 이 있으면 그쪽이 우선 (server 단일 진실 정공).

신규 dynamic UI 추가 시 흐름 판단:
- 1회 fetch → fragment endpoint
- polling / SSE 무한 → JSON + JS render (예외)

## 시간 표기 — 단일 진실 (예외 0 의무)

표시 시점의 KST 변환은 서버 / 클라이언트 양쪽 모두 같은 포맷 (`YYYY-MM-DD HH:MM:SS`) — 운영자 인지 부담 0.

| 위치 | 함수 | 포맷 |
|------|------|------|
| Server-side (Jinja2 template) | `kst` 필터 (`web/templating/filters.py`) | `%Y-%m-%d %H:%M:%S` |
| Client-side (JS) | `ChartUtils.fmtKst(isoStr)` (`web/static/js/chart-utils.js`) | `YYYY-MM-DD HH:MM:SS` |

규약:
- 표시 단계 시간 = 둘 중 하나 사용 의무 (SSR `{{ ts | kst }}`, JS `ChartUtils.fmtKst(ts)`).
- 초 단위까지 표시 — 운영자가 수집 끊김·발행 시점 진단 시 분/초 정확성 의무.
- 차트 X축 라벨은 별도 — `ChartUtils.fmtLabel(ts, range)` 가 range 별 (24h 미만 `HH:MM`, 7d `MM/DD HH:MM`, 30d `MM/DD HH:00`) 적용.

금지:
- `toLocaleString('ko-KR')` 등 locale-dependent 포맷 (브라우저 locale 분기).
- `fmtKst().slice(0, 16)` 초 단위 잘라내기 (정밀 손실).
- `dt.strftime("%H:%M")` 등 부분 포맷 (일관성 깨짐).
- inline `new Date(... + 9*60*60*1000)` 임의 offset (F2 위반 — KST 변환 단일 경계).

## 네비게이션 규약 — 새창 금지 + 뒤로가기 보존 (예외 0 의무)

원칙: 모든 페이지 (대시보드 외) 가 동일 back chain 규약 적용. 보고서·진단·이력·detail·tab 페이지 사이 이동은 모두 현재 탭. 새 탭 (`target="_blank"`) 금지. 모든 결과 페이지의 "← 이전" link 는 referrer 를 `back` query 로 명시 보존 → 어떤 진입 경로에서든 정확히 직전 페이지로 복귀.

새 페이지·새 link 추가 시 3 위치 동시 적용 의무:
1. router endpoint signature 에 `back: str | None = Query(None)` + `back_url = _safe_back(back, FALLBACK)` 산출 + context 전달.
2. 자식 진입 link (template / JS) 에 `?back={{ self_back }}` (또는 JS `back=pathname+search`) 박음. `self_back = quote(f"{request.url.path}?{request.url.query}", safe="")` 산출.
3. 페이지 `.back` 버튼 = `<a class="back no-print" href="{{ back_url }}">&larr; 이전</a>` (hardcoded 부모 URL 금지).

위 3 위치 중 어느 하나 누락 시 back chain 깨짐 → fallback (대시보드 등) 으로 점프. 새 페이지 추가 PR 의 review 항목.

### 발행·전환 시 현재 탭 이동

JS publish 함수 표준:
```js
const params = new URLSearchParams();
params.set('view', currentView);
params.set('time_range', rangeSel.value);
params.set('back', location.pathname);  // referrer 보존
window.location.href = `/servers/report?${params.toString()}`;
```

라우터의 결과 페이지 표준:
```python
back_url = back if back and back.startswith("/") and not back.startswith("//") else "/servers/"
# open-redirect 방어: '/' 시작 + '//' 제외 (same-origin path 만 허용)
```

템플릿 ← 이전 link:
```html
<a class="back no-print" href="{{ back_url }}">&larr; 이전</a>
```

### 결과 페이지 → 자식 detail link 의 back chain

다중 N대 보고서 (`/servers/report?ids=...`) 의 hostname 클릭 → 단일 detail (`/servers/{id}/report`) 진입 시, 자식 detail 페이지의 ← 이전 link 가 부모 보고서로 복귀해야 함. 부모 라우터에서 `self_back` 합성:
```python
from urllib.parse import quote
self_back = quote(f"{request.url.path}?{request.url.query}", safe="")
```
템플릿에서 자식 link 에 `&back={{ self_back }}` 추가.

### 표 적용 위치 (모든 페이지 — 예외 0)

| 라우터 | back 사용 여부 | back fallback |
|--------|----------------|----------------|
| `/servers/` (대시보드) | X (root 진입점) | — |
| `/servers/{id}` (detail) | O | `/servers/` |
| `/servers/{id}/{cpu,memory,services,performance,storage,network}` (tab) | O | `/servers/{id}` |
| `/servers/report` (다중) | O | `/servers/` |
| `/servers/{id}/report` (단일) | O | `/servers/{id}` |
| `/reports/environment` | O | `/servers/` |
| `/reports/history` | O | `/servers/` |
| `/reports/right-sizing-thresholds` (참고자료) | O | `/servers/` |
| `/diagnostics?ids=...` (결과) | O | `/servers/` |
| `/diagnostics/history` | O | `/servers/` |

대시보드 (`/servers/`) 만 root 진입점이라 back 안 받음 — 다른 페이지에서 그쪽으로 가는 link 도 back 불필요. 다만 대시보드 자체는 `self_back` 산출 의무 (자식 link 에 박기 위해).

### 에러 표시 — toast 단일 진실

발행 실패·API 오류는 페이지 본문 (statusEl 영구 표시) 가 아닌 toast (sub-window) 로 표시:
```js
if (window.ToastUtils) {
  ToastUtils.show(`AI 진단 발행 실패: ${e.message}`, 'err');
}
```
statusEl 은 이전 상태 복원 — 에러 흔적 본문에 잔존 금지.

금지:
- 발행 publish 함수에서 `window.open(url, '_blank')` — 사용자 의도 (현재 탭 일관) 위반.
- ← 이전 link 에 `javascript:history.back()` 단독 사용 (back chain 끊김 시 이상한 곳으로 복귀) — 명시 `back_url` 항상 같이.
- back query 인자 sanitize 누락 (open-redirect — 외부 URL 로 점프 가능).
- `.back` 버튼에 hardcoded URL (`/servers/{id}` / `/servers/` 등) 박음 — back_url context 활용 의무. fallback 은 router 측 `_safe_back` 단일 진실.
- 자식 진입 link 에 `?back=` 박지 않음 — 진입한 자식 페이지가 fallback 으로 점프하는 결과.

## 링크 포맷 — 단일 진실 (예외 0)

모든 link 일관 — base.html 의 `a` 스타일이 단일 진실:

```css
a { color: #2563eb; text-decoration: underline; text-decoration-color: #cbd5e1; text-underline-offset: 2px; }
a:hover { text-decoration-color: #2563eb; }
```

별도 class (예: `.row-link` / `.task-cell` / `.attention-link`) 는 base 스타일 그대로 상속. 부수 affordance (layout flex / font-weight 강조 등) 만 정의. text-decoration 중복 정의 금지.

금지:
- 일부 link 에 `text-decoration: none` 또는 hover-only underline — 일관성 깨짐.
- 별도 class 가 base 스타일 override (예: `.row-link { text-decoration: ...}` 중복) — base 단일 진실.
- inline style `style="text-decoration:none"` — 버튼류 (`btn-action` 등) 에서 의도된 경우 외 금지.
