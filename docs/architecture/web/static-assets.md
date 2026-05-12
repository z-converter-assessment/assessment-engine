# Web 정적 자원 — JS·차트 UI

CLAUDE.md F5 "Frontend JS 외부화 의무" — 신규 차트 로직은 외부 `.js` 파일에. inline `<script>` 신규 금지.

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
| `fmtKst(iso)` / `fmtLabel(iso, range)` | KST 시각 포맷 (`MM/DD HH:mm`) |
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

`.no-print` 클래스로 navbar/검색폼/버튼 인쇄 시 숨김 (base.html). 컨설턴트가 브라우저 인쇄 → PDF/PPT 캡처. 백엔드 PDF export는 미도입 (deliverables.md 5 결정).

## 의존성

| 도구 | 로드 |
|------|------|
| Chart.js (`chart.umd.min.js`) | 차트 페이지에서 `<script src=...>` |
| ChartUtils | base.html `<head>`에서 단일 로드 |

번들 도구 미도입 — IIFE 노출 패턴 (`docs/tradeoffs.md` T9).
