# Web 정적 자원 — JS·차트 UI·표준 컴포넌트

정책: CLAUDE.md #E6 (JS 외부화 의무) · #E8 (차트·도넛 UI, P4) · #F5 (외부화 강제 채널). 본 문서는 JS 디렉토리 배치·`ChartUtils` API·P4 5 의무 규약·차트 UI·인쇄 CSS·base.html 표준 컴포넌트 카탈로그·네비게이션 규약 단일 진실.

`static/js/` 배치 규칙 (파일 목록은 `ls` 단일 진실):
- 루트 = base.html 이 전역 로드하는 공용 유틸. 전역 객체 노출(`ChartUtils`·`ToastUtils`·`EmitUtils`·`TableUtils`·`SignalUtils`·`TaskModal`) 또는 전 페이지 공통 위젯 바인딩(사이드바·상단바·네비 진행바).
- `pages/` = 페이지 템플릿이 `defer` 로 개별 로드하는 페이지 전용 스크립트.
- `vendor/` = UMD 라이브러리 (아래 "의존성" 절).
- `generated/api.ts` · `globals.d.ts` = 타입 계약 (`type-contract.md` 단일 진실).

공용 유틸(base.html 전역 로드) — 페이지 간 공통 로직을 단일화해 재발 버그를 차단:
- `EmitUtils.submitNavigate(btn, urlFn, opts)` — 발행/제출 버튼 -> POST -> 응답 `view_url` navigate. 버튼 비활성·pending 토스트·에러 재활성 + bfcache 복원(뒤로가기) 시 버튼 재활성을 내장. 리포트 emit(환경/서버상세/선택 N대) 3곳 공유.
- `SignalUtils.{renderSaturation,renderErrors,renderActivity}` — 서버가 os-aware 판정(값·임계·saturated·4상태)을 끝낸 구조화 신호(`SaturationSignal`·`ErrorSignal`·디스크/네트워크 I/O 스냅샷)를 컨테이너에 렌더만(P4). 임계 재계산·os 분기·양 OS 설명 인라인 금지 — 이 호스트 OS 값만 오고 근거는 hover. 서버 상세 개요·자원 탭 스냅샷 카드 공유(포화=값/임계+상태 배지, 에러=카운트형 정상0 발화, 활동=디스크 어태치별 R/W·인터페이스별 RX/TX 라인).
- `TableUtils.{restripe,sortByColumn,makeSortable}` — `sortable-table` 칼럼 클릭 정렬. 정렬·필터·clip 로 행이 재배열·숨김되면 CSS `:nth-child` zebra 가 숨은 행까지 세어 줄무늬가 어긋나므로(흰색 뒤섞임), sortable-table 은 :nth-child 를 무력화하고 JS 가 '보이는 행'만 `.zebra` 로 재줄무늬. `sortable-table` 클래스를 단 모든 표 공유(서버목록·자원적정성·발행이력·실시간 부하).

## ChartUtils API (chart-utils.js)

`base.html` `<head>`에서 단일 로드 → 전역 `ChartUtils`. 페이지 .js가 destructure.

| 함수 | 용도 |
|------|------|
| `RANGE_LABEL` / `AUTO_BUCKET` / `BUCKET_LABEL` / `RANGE_MS` / `BUCKET_MS` / `COLORS` | 매핑·상수 |
| `themeColor()` | 차트 시리즈 주색 — 아래 "색 테마" 절 단일 진실 |
| `fmtKst(iso)` | KST 시각 포맷 (`YYYY-MM-DD HH:MM:SS`) — server `kst` 필터와 동일, "시간 표기" 절 단일 진실 |
| `fmtLabel(iso, range)` | 차트 X축 라벨 (range 별 `MM/DD HH:MM` / `HH:MM`) — 표시 시각과 별개 컨텍스트 |
| `fmtKbChart(v)` | bytes/sec → kB/MB/s |
| `getAnchorEnd(inputId)` / `initAnchor(inputId)` | datetime input 처리 |
| `makeBucketGrid(range, bucket, anchor)` / `joinToGrid(grid, rows, bMs)` | 버킷 그리드 + 응답 join |
| `bindToggle(groupId, onChange)` | range/agg 컨트롤 바인딩 — element 가 `<select>`면 change, `.toggle` 버튼이면 click 자동 분기 (호출처 동일) |
| `pageTimeControl(rangeBtnsId, anchorId, default, onChange)` | 페이지 단일 시간축 컨트롤러(#F10) — range 토글 + anchor 하나가 페이지 전 차트를 구동. `{getRange, getAnchor}` 반환. 변경 시 onChange 로 전체 reload. anchor 미입력=live now, 입력=고정(과거 조사). 서버 상세 자원 탭 공용 |
| `initAutoRefresh(onRefresh, intervalMs)` | 호출자가 준 주기로 자동 갱신 (setInterval + pagehide 정리) |
| `safeArray(arr)` | `Array.isArray` 방어 (P4 c) |
| `buildDimDatasets(rows, bMs, grid, metaMap, opts)` | dimension별(device/iface/mount) 멀티라인 dataset 조립 — grid join + 색·라벨 매핑 |
| `fmtThroughput(kb)` | kB/s -> B/s·kB/s·MB/s 표시 단위 자동 결정 (P4) |
| `naWindows(osFamily, key, formatted)` / `setValText(el, text)` / `setNaText(el, osFamily, key, formatted)` | os-aware N/A 텍스트 — Windows 미측정 축은 대체 표기, 그 외 formatted 값 (서버 상세 실시간 카드) |
| `renderChipLegend(container, chart)` | 색점+라벨 칩(pill) 토글 범례 — dataset 1개당 1칩, 클릭 시 show/hide. comp/load 계열 (cpu·memory) |
| `buildAvgMaxDatasets` / `buildAvgMaxLegend(id, chart, opts)` | avg+max ghost dataset·범례. `withToggle`=칩(avg/max 쌍 1칩 함께 토글 — storage io·network·metrics 통일), `codeLabel`=정적 선+code 라벨(현재 미사용) |

## 페이지별 .js 패턴

| 페이지 | 차트 인스턴스 패턴 |
|--------|-------------------|
| `metrics.js` | `chartInstances` 객체 + 통합 `loadAllCharts()` — 페이지 전역 range/anchor 를 한 번 캡처해 모든 loader 에 같은 인자로 전달(#F10 단일 시간축) |
| `environment-metrics.js` | `metrics.js` 와 동일 패턴 — 환경 스코프 차트 묶음을 `loadAllCharts()` 가 구동 |
| `cpu/memory/network/storage.js` | 차트 인스턴스 개별 변수 + 페이지 단일 시간축(`pageTimeControl`) — 한 range/anchor 가 페이지 전 차트 구동, `reloadAllCharts()` 로 일괄 갱신(#F10). 스냅샷 카드 포화 신호는 `SignalUtils.renderSaturation` |
| `list-table.js` | 모달 (Install / JSON Export / 보고서) + 검색·필터·더보기/접기. 차트 없음 — 체크박스 + bulk action |

## P4 차트 JS 5 의무 규약 (loader 표준)

| 규약 | 내용 |
|------|------|
| (a) sequence counter | `let xxxSeq=0; const seq=++xxxSeq; ... if (seq !== xxxSeq) return;` — stale 응답 폐기 |
| (b) capture-before-await | range·anchor를 `await` 직전 로컬 캡처 → 인자로 전달 |
| (c) Array.isArray | 5xx가 JSON object 반환 가능 — `safeArray()` 사용 |
| (d) 404 분기 | `/metrics/latest` 등 데이터 부재는 `res.status === 404` 분기 |
| (e) suggestedMax 명명 상수 | `PERF_DISK_KBPS_SUGGESTED_MAX = 10 * 1024` 형식 — Y축 상한 매직넘버 금지 |

모든 차트 페이지가 (a)~(e) 적용. `metrics.js`(서버 상세 성능 탭)는 9개 차트 loader 를 `loadAllCharts` 가 `(range, anchor)` 시그니처로 호출 — seq(sequence counter)는 각 loader 내부에서 생성(capture-before-await).

## 차트 UI 디테일

### Y축 정책
- 추이 차트 (자원별 상세 탭): 분해력 우선 — idle 환경의 작은 값도 보이게 `suggestedMax` 낮게 (`STORAGE_KBPS_SUGGESTED_MAX`)
- 성능 추이 (metrics 페이지): 절대 기준 — 디스크 처리량 `PERF_DISK_KBPS_SUGGESTED_MAX`(10MB/s)
- 네트워크 I/O 는 양쪽 모두 고정 상한 없음 (`beginAtZero` 만) — 이기종 fleet 트래픽 규모 차이로 고정 상한을 두면 저트래픽 구간 추이선이 바닥에 눌림

### X축 정책 (예외 0)
- 모든 차트(대시보드·서버 상세·환경 부하 추이·보고서)는 윈도우 전체 고정 그리드. `makeBucketGrid(range, AUTO_BUCKET[range], anchor)` + `joinToGrid` — 빈 구간 null(gap, `spanGaps:false`), 최신이 오른쪽 끝 고정.
- anchor = 마지막 데이터 시각(보고서 정적 스냅샷) 또는 사용자 선택·now(라이브).
- 부분 범위만 그리지 않는다(왼쪽부터 채우기 금지) — 차트 간 x축 비교 위해 전 차트 고정 그리드 단일 정책.

### 차트 컨트롤 (제목줄 통합)
- 차트 헤더 = `.chart-head` 단일 행: 제목(h2 좌측) + bucket-label·구간·앵커·집계 컨트롤(우측, bucket-label 부터 `margin-left:auto`). 좁아지면 그룹 단위 wrap.
- 버킷 라벨 = `<span class="bucket-label">` 배지 (현재 버킷=분해력 표시 — cpu/memory/storage/network 차트 페이지 공용 클래스, base.html 단일 진실). 성능 추이(metrics)는 전역 단일 컨트롤이라 높이·정렬이 달라 별도 스타일.
- 구간/집계 = `<select class="chart-select">` 드롭다운 (너비 절약). `bindToggle` 이 select/button 자동 분기라 JS 호출 동일.
- 앵커 = `<input type="datetime-local" class="chart-anchor">`. select·anchor 높이 통일(`box-sizing`).
- 서버 상세 자원 탭(cpu/memory/network/storage)은 페이지 단일 시간축 컨트롤(`pageTimeControl`, 카드 밖 상단 range+anchor 하나)이 그 페이지 전 차트를 같은 창·시점으로 구동(#F10, 신호 간 시점 상관). 차트별 개별 range/anchor 없음.
- 성능 추이(metrics — 서버 상세 `/{id}/metrics` + 환경 `/environment/metrics`)는 예외 — 차트별 `.chart-head` 대신 페이지 전역 단일 컨트롤(카드 밖 좌상단, 버킷 라벨 + 구간 select + 앵커 input — 구간·앵커 둘 다 change 즉시 반영, 적용 버튼 없음)이 모든 차트 동기. 두 페이지 모두 자원별(CPU/메모리/스토리지/네트워크) 카드를 `.perf-stack`(세로 flex)으로 나열하고, 카드 내부도 동일하게 `.perf-grid`/`.perf-item`(화면 2열 auto-fit, 인쇄는 페이지 로컬 스코프가 2열 portrait 로 재정의, 아래 인쇄 CSS 절) — 서버 상세·환경 두 페이지 레이아웃 단일 진실 공유. 서버 상세는 CPU(2)·메모리(3)·스토리지(2)·네트워크(2) 9차트, 환경은 자원당 2개 8차트 — 디스크 read+write·네트워크 RX+TX 각각 통합 1 차트.

### 범례 (칩 토글)
- `.legend-chip` (pill 버튼 + `.legend-dot` 색점): 클릭 시 dataset show/hide, 숨김은 `aria-pressed=false`로 흐려짐. `button`+`aria-pressed`라 키보드 토글 지원.
- comp/load 계열(고정 dimension) = `renderChipLegend` (dataset 1칩 — cpu·memory comp, metrics CPU 분류·메모리 구성). avg+max ghost = `buildAvgMaxLegend({withToggle})` (avg/max 쌍 1칩 함께 토글 — storage io·network·metrics 물리 I/O·파일시스템·네트워크). 전 차트 칩 토글로 통일 (인터페이스별 그룹 범례·정적 범례 없음).

### avg + max ghost 패턴
1차 dataset = avg (visible). 2차 dataset = max (`borderColor:'transparent'`, `realData` 보유) — tooltip에서 `realData`로 max 표시. legend는 짝수 인덱스만.

### 추세선 · 면적 음영 정책 (예외 0)
- 추이 차트의 면적 음영은 avg+max ghost(`buildAvgMaxDatasets`, avg dataset `fill:'+1'`)만 — avg~max 사이를 채워 burst(순간 최대-평균 차)를 시각화. 이것이 "음영"의 유일한 의미.
- 선 아래 zero 까지 채우는 area fill(`fill:true`) 금지 — 추이 차트는 추세선만(`fill:false`). area fill 은 burst 음영과 혼동되고 값 밀집 시 가독성을 떨어뜨림.
- 15분 구간(1분 버킷)은 버킷당 데이터 1포인트라 max=avg → ghost 음영 0. `buildAvgMaxDatasets` 가 `bMs <= BUCKET_MS['1m']` 일 때 maxRows 를 비워 전 차트 일괄 자동 비활성.
- 실행 큐 차트(`cpu.run_queue`, os-aware — Linux procs_running / Windows Processor Queue Length)는 backend 가 이미 실행큐 합/코어 합(코어당 값, 포화선은 OS 별)으로 반환 — 클라는 값 그대로 표시. 서버 상세는 연속값 단일선(`cpu.js`), 환경 성능 추이는 같은 임계 판정을 SQL 로 이식한 crossing 서버 수(`cpu.saturation_hosts`, count) 단일선 — 스코프별 표현 단위 차이는 `services.md` "서버 상세 성능 추이" 절.

## 색 테마 — `:root` 변수 + 주색 단일 진실 (예외 0)
- 테마 변수 = `base.html :root` 단일 선언. `--color-title`(#2563eb) = 주색 — `.btn-primary` 채움·`.toggle.active`·정렬 칼럼 강조·`.list-filter` 테두리·네비 진행바·스토리지 막대. 사이드바 계열은 `--sidebar-*` 변수군(바탕·글씨·hover·active). `--color-table-head`(#e5e7eb) = 전 테이블 제목행 하단 경계선. 색 변경 시 `:root` 만 수정.
- JS 차트 시리즈 주색도 본 변수 추종 — `chart-utils.js` `ChartUtils.themeColor()`(getComputedStyle 로 `--color-title` 읽기, 실패 시 #2563eb fallback). 페이지 차트 JS(cpu/metrics/memory/environment-metrics/environment-trend/detail/network-topology)가 hex 직박 대신 본 helper 참조. SVG 게이지는 presentation attribute 가 var 미지원이라 inline `style="stroke: ..."` 로 적용.
- 데이터 시각화 주색도 동일 변수 — 환경 평균 활용률 도넛 게이지(`_UTIL_COLOR_GAUGE`, mappers/attention.py) · right-sizing 과다프로비저닝(`_DONUT_SEGMENT_DEFS` over, mappers/shared.py) · 파일시스템 usage 막대(`_MOUNT_BAR_COLOR`, mappers/server.py) 모두 hex 대신 `var(--color-title)` 을 담아 테마 변경에 자동 추종.
- under_provisioned = `#ef4444` (red-500) 대비 유지. 과다프로비저닝(여유)과 활용률 게이지가 같은 파랑 — 같은 화면 두 의미지만 테마 단색화를 위한 의식적 통일.
- 네비게이션 = 좌측 사이드바(`--sidebar-*` 다크 슬레이트 바탕 + 밝은 글씨, `_sidebar.html` + `nav_groups` 글로벌). active 항목은 `--sidebar-active` 채움 + 우측 강조 테두리. 본문 링크(`a`)는 무채 #666666(밑줄 #b0b0b0).
- 버튼 3종(base.html): `.btn-primary`(주색 채움, 모달 발행 등) / `.btn-select`(흰 바탕·표준 크기, 서버 선택 발행 버튼 — 회색 글씨·테두리(600), 비활성(`:disabled`)은 옅은 회색) / `.btn-action`(흰 톤·표준 크기·회색 글씨, 보조 액션 — 환경보고서 발행·실시간 메트릭·성능 추이·전체보기). 선택 N대 실시간 메트릭·성능 추이 버튼은 `.btn-select`(미선택 시 `:disabled`) — public_id navigate(`?ids=`).
- 상태(is_online) 표시 = 폰트색 (`.status-on` #16a34a / `.status-off` #94a3b8, 10px) — 목록·보고서 표·상세 헤더. 개별 보고서 인벤토리 상태는 일반 폰트(보고서 본문 톤 통일).
- 운영 신호 경고 = 호박색(amber) 도메인 (`.attn-active`, mappers/attention.py `_ATTN_ACTIVE_BADGE` 가 생산). 발화는 호박 채움 — 경고 의미를 색으로. 테마 파랑(브랜드)과 영역 분리.
- 네트워크 토폴로지 노드 색(`network-topology.js`·범례 동기화): subnet #475569 / 라우터(게이트웨이) #d97706 / Windows #8b5cf6(파랑과 구분되는 보라) / Linux 는 `ChartUtils.themeColor()`·`var(--color-title)` 추종. host 는 OS 로만 구분(멀티홈 별도 색 없음).
- 테이블 제목행 = `thead` 옅은 배경(#fbfcfd) + 진한 글씨(#171a20), 하단 경계선만 `--color-table-head`. `base.html` `thead`/`th` 전 테이블 단일 진실 (폰트 크기·위계 표준 유지, 색만).
- 파일시스템 usage 게이지 막대는 주색 단색(위 `_MOUNT_BAR_COLOR`). 사용률 위험도는 게이지 색이 아니라 `badge_class`(`badge-warn`/`badge-danger`)로 — 게이지는 톤 통일, 경고는 배지로 분리.
- 색 상수 단일 진실 = `view-models.md` "신호 임계값 단일 정의".

## 반응형·정렬 레이아웃 (예외 0)
- 작은 창에서 카드 무파손. 다열 영역은 `grid-template-columns:repeat(auto-fit, minmax(min(100%, Npx), 1fr))` — 폭 부족 시 자동 1열, 한쪽 칼럼 찌그러짐 0.
- 고정 다열(`kpi-grid-2/3/4` · `metric-grid-2/3`)은 `@media (max-width:640px)` 에서 1열 (base.html).
- 2칼럼 카드(`env-dual` · `env-pair`)는 `align-items:start`로 칼럼 독립, 같은 행 항목은 grid 정렬로 높이 일치.
- 언더 프로비저닝 상세(`action_targets_table`, 환경 자원 평가·환경 보고서(engineer) 공유 — 두 화면 칼럼 동일) = 호스트·CPU·메모리·디스크(`spec_display`, 서버 목록과 동일 정적 배정 사양 — 권고 칼럼의 사이징 목표와 나란히 비교)·분류(근본원인 병합)·권고·네트워크 상태·디스크 I/O 상태·신뢰도 sortable-table — host_status 를 구동한 원시 수치(5축)는 표시 안 함(서버 상세 자원별 탭에서 확인). 심각도 상위 정렬(`severity_score` = swap(paging) > 위반 자원 수 > max(CPU/메모리/디스크 util)).
- 환경 개요(`/`) 영역 = 환경 요약 / 주요 워크로드 / 자원 적정성 / 자원 이용·포화 / 시스템 에러 — 집계 위젯 카드만(자동 갱신 없음, 등록 서버 0이면 안내 카드 단일). 환경 부하 추이 + 네트워크 토폴로지 2열(`env-dual`)은 환경 보고서 본문.

## 인쇄 CSS

인쇄 규칙 단일 진실 = base.html `@media print` — `.no-print` 숨김(사이드바·상단바·조작 컨트롤·버튼)·`.print-only` 노출·카드 page-break·표 격자와 배경색 복원. 용지 방향은 페이지 로컬 `<style>@media print{ @page{ size:...; margin:... } }</style>` 담당 — 환경 보고서 A4 landscape, 성능 추이 두 페이지 A4 portrait(named-page 방향은 브라우저 지원이 불안정해 문서 전체 `@page` 로 확실히). 페이지별 레이아웃 override 는 아래 성능 추이 문단.

컨설턴트가 브라우저 인쇄 -> PDF/PPT 캡처. 백엔드 PDF export는 미도입 (`docs/explanation/tradeoffs.md` 참조).

성능 추이 인쇄(서버 상세 `servers/metrics.html` + 환경 `environment_metrics.html` 공용 패턴, 둘 다 A4 portrait 1페이지): base.html `@media print` 가 두 페이지 공통 기본값(낱개 `.perf-item` page-break, `.perf-grid canvas` 폭·높이 보정, `.perf-stack` 간격·카드 패딩·폰트 축소, `.perf-chart` 높이)을 정하고, 페이지 로컬 스코프(`.metrics-print`/`.env-metrics-print`)가 열 수·차트 높이 등 페이지별 값만 override 한다. 낱개 단위인 이유 — CSS Grid auto-flow 는 "행" DOM 요소가 없어 개별 아이템에 걸어야 한다. canvas 보정 이유 — Chart.js 가 캔버스에 박은 화면 폭(px)이 인쇄 컨테이너를 넘어 꺾은선이 plot 경계를 넘는다. `beforeprint`/`afterprint` 차트 `resize()` 보강(metrics.js·environment-metrics.js).

## 의존성

| 도구 | 로드 |
|------|------|
| Chart.js (`vendor/chart.umd.min.js`) | 차트 페이지·보고서 템플릿이 defer 로드 |
| cytoscape (`vendor/cytoscape.min.js`) | 네트워크 토폴로지 페이지·선택 N대 보고서(engineer)가 defer 로드 |
| ChartUtils 등 전역 유틸 | base.html `<head>`에서 단일 로드 |

번들 도구 미도입 — IIFE 노출 패턴 (`docs/explanation/tradeoffs.md` T9).

## 표준 컴포넌트 카탈로그 (base.html)

원칙: 새 페이지 추가 시 아래 카탈로그의 클래스 먼저 적용. 같은 패턴을 inline 으로 재구현 금지 — 표준에 없으면 base.html 에 새 표준 추가 후 사용. P2 (mapper 단일 진실)·P3 (템플릿 순수 렌더링) 와 동급의 표시 계층 규약.

### 보조 컴포넌트 폰트 (h1/h2/h3 외)

본 표는 표시 컴포넌트 (KPI / metric / badge / 버튼) 만 — h1/h2/h3 + .page-meta / .section-meta 위계 제목은 별도 "폰트 위계 — 단일 진실" 절 참조.

| 슬롯 | 값 | 용도 |
|------|----|------|
| .stat-value | 20px / 700 | 실시간 메트릭 dashboard 값 |
| .metric-value | 24px / 700 / #1e293b | 보고서·KPI 큰 값 |
| .metric-value-md | 18px / 700 / #1e293b | 보고서 보조 값 (긴 텍스트 위주) |
| body | 14px / #383d43 | 일반 본문 |
| .btn / .btn-action / .btn-print | 13px / 500 | 버튼 |
| .stat-label | 11px / uppercase / #94a3b8 | stat-box 라벨 |
| .metric-label | 12px / #64748b | metric-card 라벨 |
| .metric-sub | 11px / #94a3b8 | metric-card 부가 문구 |
| .badge | 12px / 600 | 분류·카테고리 표시 |
| td (표 데이터) | 13px / #334155 | 모든 표 셀 — 전역 단일(목록·상세·보고서 통일). inline font-size 로 override 금지 |
| .chart-caption | 11px / #94a3b8 | 차트 하단 설명 캡션 (음영 영역 등) — 전 차트 페이지 공용 |
| .chart-desc | 11px / #94a3b8 | 차트 상단 설명 (성능탭 차트별 한 줄 설명) |
| .chart-empty | 13px / #94a3b8 / center | 차트 빈상태 오버레이 |
| code | 12px / monospace | inline code (식별자) |
| .text-md / .text-sm / .text-xs | 13 / 12 / 11px (size-only) | 보조 텍스트 size 유틸 — 색 유틸(`.text-*`)과 조합. 위계 제목·값 컴포넌트(`.stat-*`/`.metric-*`/`.kpi-*`)는 각자 size 보유라 미적용 |
| .btn-action-sm | 12px / padding 4px 11px | `.btn-action` 축소 변형 — 상세 페이지 그룹헤더 보조 '상세 ->' 링크 (함께 사용: `class="btn-action btn-action-sm"`) |

금지: 9px·10px·32px 등 카탈로그 외 값, 그리고 inline `font-size`/`font-weight`/`color` 를 박지 않음 — 크기는 위계/컴포넌트 클래스 또는 size 유틸(`.text-md`/`.text-sm`/`.text-xs`, 색 유틸과 조합), 굵기는 위계/컴포넌트, 색은 색 유틸(`.text-meta` 등)로. 새 위계가 필요하면 base.html 에 명명 클래스 추가 후 사용. 8/9/10px 은 `.status-on/off` 배지와 `@media print` 에만 허용. SVG `<text>`·modal 내부는 별도(인라인 허용).

### 박스 컴포넌트 (대형부터)

| 클래스 | 용도 | bg / border |
|--------|------|-------------|
| `.card` | 카드 컨테이너 (페이지 최상위 단위) | #fff / shadow |
| `.kpi` + `.kpi-grid` + `.kpi-grid-{2,3,4}` | KPI 카운트 카드 (12px label + 24px strong) | #fff / 1px #e2e8f0 |
| `.metric-card` + `.metric-grid` + `.metric-grid-{2,3}` | 보고서 메트릭 카드 (12px label + 24/18px value + 11px sub) | transparent / 1px #e2e8f0 |
| `.stat-box` + `.stat-grid` | 실시간 메트릭 dashboard (작은 grid, soft bg) | #f8fafc |
| `.alert-warn` + `.alert-list` | 운영 신호 발화 박스 (warn 톤) | #fef3c7 / 1px #fde68a |
| `.card-section` | 카드 내부 서브섹션 구분 (환경요약·운영신호 공통 위계 — h3 + 구분선) | 1px #e2e8f0 top border |
| `.empty-state` | 발화 가능하나 비어있는 슬롯 placeholder (#E9 discoverability) | 박스 없음 / 회색 텍스트 #94a3b8 |
| `.stree` (+ `.stree-row`/`.stree-kind`/`.stree-usage` 등) | 스토리지 레이아웃 트리 (중첩 ul, `servers/_storage_tree.html` `storage_tree()` 매크로) — 스토리지 상세·서버 보고서 공용 | 좌측 가이드선 #e2e8f0 |

금지: `<div style="border:1px solid #e2e8f0; border-radius:6px; padding:14px;">` 같은 inline 박스 재구현. 위 클래스로 치환. (P3 직접 위반 — 박스 모양을 한 곳에서 바꿀 수 있어야 한다.)

### 공통 매크로 (`_shared.html`)

base.html 컴포넌트와 동급의 표시 계층 단일 진실 — 페이지 간 재사용 매크로. 라우터·페이지 템플릿은 `{% from "_shared.html" import empty_state, window_meta %}` 로 가져와 사용.

| 매크로 | 용도 | 정책 |
|--------|------|------|
| `empty_state(message)` | 발화/조건부 섹션이 비었을 때 placeholder (제목은 유지, 내용 없음 명시) | dumb — 분기·계산 0, 정적 message만 렌더 (P3). discoverability 원칙 #E9 단일 진실. |
| `window_meta(window_label)` | 표제 메타 "최근 {라벨}" — 활용률·자원 적정성 표제 공통 | 라벨은 라우터가 `recommendation.WINDOW_DAYS`(#F10) 로 합성해 컨텍스트로 전달. 일수 하드코딩 반복 제거. |

### Badge 카탈로그

| 클래스 | 용도 |
|--------|------|
| `.badge` | base (변형 클래스와 함께) |
| `.badge-ok` / `.badge-warn` / `.badge-danger` | semantic 상태 |
| `.badge-cat-{web,db,cache,mq,container,monitor,remote,file,mail,infra,unknown}` | 서비스 카테고리 |
| `.rec-{under_provisioned,over_provisioned,optimal,idle,insufficient_data,swap}` (분류·rec-swap 메모리부족 1차신호) + `.rec-{success,failure,pending,unknown}` (Task status) | 분류 결과 · Task status |
| `.attn-active` | 운영 신호 발화 |

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
| `h2` | 16px / 700 / #1e293b | 카드 섹션 제목 (요약 / 환경 메트릭 / 자원 적정성 평가 등) |
| `h3` | 13px / 600 / #475569 | 소제목 (환경 현황 / 분포 / 조치 필요 호스트 등) — h2 보다 영향력 약함 |
| `.page-meta` | 14px / 400 / #64748b / margin-left 8px | h1 옆 sub-text (예: view_title 양식 라벨) |
| `.section-meta` | 12px / 400 / #94a3b8 / margin-left 8px | h2 옆 sub-text (예: 윈도우 / 부가 설명) |

금지:
- `<h2 style="font-size:14px; color:#475569;">` 같은 inline style override — base 단일 진실 위반.
- `<h3 style="margin:0 0 10px; font-size:14px;">` — h3 가 h2 size 와 같으면 위계 깨짐.
- h1/h2/h3 외 임의 `<div style="font-size:18px; font-weight:700;">` 같은 의사-제목 — h1/h2/h3 사용 의무.
- modal 안 제목은 별개 (`#title-id style="..."` 인라인 허용 — modal 컴포넌트 별도 위계).

## 폰트 체 — sans-serif 단일 + monospace 선택 적용

base.html `<body>` 가 system-ui sans-serif 기본. 모든 텍스트 동일 family. 별도 폰트 (serif / display) 안 씀.

monospace 는 "식별자라는 이유"로 자동 적용하지 않는다. 표시 설계상 등폭 표기가 의미 전달에 기여하거나
일반 텍스트와의 구분이 유용하다고 판단되는 경우에만 선택 적용 (의무 아님 — UI 판단):
- 실제 코드·명령·설정 스니펫 (shell command / config) — 등폭 정렬·"코드임" 표시가 도움
- 그 외 등폭 구분이 가독성·식별에 실익이 있다고 판단되는 특정 케이스

class:
- `<code>...</code>` — 위 용도의 inline code (background + padding + monospace, base 단일 진실)
- `.identifier` — code element 아닌 곳에 monospace 만 (background 없음). 동일 판단 기준 적용

금지:
- 식별자(hostname / UUID / IP / path / unit name)에 "식별자라는 이유만으로" monospace 자동 적용 — 기본 sans.
  (등폭 구분이 유용하다 판단되면 위 선택 기준으로 case-by-case 결정)
- 일반 텍스트 (제목 / 본문 / 라벨) 에 monospace — 가독성 저하.
- 숫자 (vCPU / GB / %) 에 monospace — sans-serif weight 700 으로 정렬·강조 충분.
- inline `style="font-family:monospace"` — base `.identifier` 또는 `<code>` 사용 의무.

## P3 정공 예외 — 1회 fetch vs polling 흐름

P3 (Jinja2 template 단일 진실) 의 1차 정공 = JS HTML 합성 폐기, server fragment endpoint + JS DOM 교체.

흐름 유형 3종 — 앞 2종은 fragment endpoint 정공, 고빈도 polling 만 예외(fragment fetch overhead 큼):

| case | 정공 / 예외 | 이유 |
|------|-------------|------|
| 1회 fetch + render (예: task-modal body, 자원 평가 `?fragment=result`, 발행 이력 `?fragment=1`) | 정공 — fragment endpoint (`/api/tasks/{id}/detail` 등) + JS `innerHTML = await fetch().text()` | overhead 0, P3 완전 정공 |
| 저빈도 polling + 파생 많은 SSR 영역 (예: 실시간 현황 자동갱신) | 정공 — fragment endpoint (`/environment/realtime?fragment=realtime`) HTML 반환 + JS `#rt-mount` innerHTML 교체 | 저빈도라 HTML fragment fetch overhead 무시 가능. mapper 파생 많아 JSON+JS render 시 P2 복제 — fragment 가 단일 진실 유지 |
| polling 흐름 (예: detail page metrics/latest polling / storage snapshot) | 예외 — JS template literal 허용 (P4 와 같은 dynamic 인터랙션 도메인) | polling 마다 HTML fragment fetch 시 overhead 큼. JSON polling + JS render 가 정공 |

폴링 흐름 JS render 의무:
- inline `style="color:#xxx"` 금지 — base.html 색 전용 유틸 (중립 톤 `.text-strong`/`.text-label`/`.text-muted`/`.text-meta`/`.text-faint` + 의미색 `.text-danger`/`.text-ok`/`.text-warn`/`.text-attn`/`.text-unknown`(판정 불가 전용 보라 — 경고 `.text-warn`과 다른 범주), 모두 color-only · size 는 부모 상속) 사용. font-size 는 위계 제목·값 컴포넌트(`.stat-*`/`.metric-*`/`.kpi-*`/`.pre-output`) 우선, 그 외 보조 텍스트는 size 유틸(`.text-md`/`.text-sm`/`.text-xs`)을 색 유틸과 조합.
- layout 관련 inline style (display:flex / grid / table 등) 허용 — 모듈별 부수 정렬, utility class 화 강제 X.
- 동일 데이터의 SSR template 이 있으면 그쪽이 우선 (server 단일 진실 정공).

신규 dynamic UI 추가 시 흐름 판단:
- 1회 fetch → fragment endpoint
- 저빈도 polling(수십초) + 파생 많은 SSR 영역 → fragment endpoint(HTML) 교체 (P2 단일 진실 유지). 체크박스 선택·client 필터·직접 바인딩 이벤트가 있는 영역은 행만 교체 후 체크 상태·필터·이벤트를 복원 (정적 요소는 교체 대상에서 보존). task-cell 모달처럼 event delegation 핸들러는 복원 불필요
- 고빈도 polling → JSON + JS render (예외)

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
1. router endpoint signature 에 `back: BackUrl = None` + sanitize 후 `back_url` 산출(아래 "라우터의 결과 페이지 표준") + context 전달.
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
window.location.href = `/reports/servers?${params.toString()}`;
```

라우터의 결과 페이지 표준:
```python
back: BackUrl = None  # 시그니처에서 검증 강제 (routers/_back.py)
back_url = safe_back(back, "/")  # 걸러진 값에 기본 목적지만 씌운다
```

템플릿 ← 이전 link:
```html
<a class="back no-print" href="{{ back_url }}">&larr; 이전</a>
```

### 결과 페이지 → 자식 detail link 의 back chain

다중 N대 보고서 (`/reports/servers?ids=...`) 의 hostname 클릭 → 단일 detail (`/servers/{id}/report`) 진입 시, 자식 detail 페이지의 ← 이전 link 가 부모 보고서로 복귀해야 함. 부모 라우터에서 `self_back` 합성:
```python
from urllib.parse import quote

self_back = quote(f"{request.url.path}?{request.url.query}", safe="")
```
템플릿에서 자식 link 에 `&back={{ self_back }}` 추가.

### 표 적용 위치 (모든 페이지 — 예외 0)

| 라우터 | back 사용 여부 | back fallback |
|--------|----------------|----------------|
| `/` (환경 개요) | X (root 진입점) | — |
| `/servers` (목록) | X (root 진입점) | — |
| `/servers/{id}` (detail) | O | `/` |
| `/servers/{id}/{cpu,memory,services,metrics,storage,network}` (tab) | O | `/servers/{id}` |
| `/environment/{assessment,realtime,metrics,topology}` | O | `/` |
| `/reports/servers` (선택 N대) | O | `/` |
| `/servers/{id}/report` (단일) | O | `/servers/{id}` |
| `/reports/environment` | O | `/` |
| `/reports/history` | O | `/` |
| `/reference` (참고) | O | `/` |

환경 개요(`/`)만 root 진입점이라 back 안 받음 — 다른 페이지에서 그쪽으로 가는 link 도 back 불필요. 다만 개요 자체는 `self_back` 산출 의무 (자식 link 에 박기 위해).

### 에러 표시 — toast 단일 진실

발행 실패·API 오류는 페이지 본문 (statusEl 영구 표시) 가 아닌 toast (sub-window) 로 표시:
```js
if (window.ToastUtils) {
  ToastUtils.show(`보고서 발행 실패: ${e.message}`, 'err');
}
```
statusEl 은 이전 상태 복원 — 에러 흔적 본문에 잔존 금지.

금지:
- 발행 publish 함수에서 `window.open(url, '_blank')` — 사용자 의도 (현재 탭 일관) 위반.
- ← 이전 link 에 `javascript:history.back()` 단독 사용 (back chain 끊김 시 이상한 곳으로 복귀) — 명시 `back_url` 항상 같이.
- back query 인자 sanitize 누락 (open-redirect — 외부 URL 로 점프 가능).
- `.back` 버튼에 hardcoded URL (`/servers/{id}` / `/` 등) 박음 — back_url context 활용 의무. fallback 산출은 라우터 책임(`safe_back`). sanitize 는 `BackUrl` 타입이 시그니처에서 강제한다.
- 자식 진입 link 에 `?back=` 박지 않음 — 진입한 자식 페이지가 fallback 으로 점프하는 결과.

## 링크 포맷 — 단일 진실 (예외 0)

모든 link 일관 — base.html 의 `a`/`a:hover` 가 단일 진실 (색·밑줄 값은 위 "색 테마" 절).

별도 class (예: `.row-link` / `.task-cell` / `.attention-link`) 는 base 스타일 그대로 상속. 부수 affordance (layout flex / font-weight 강조 등) 만 정의. text-decoration 중복 정의 금지.

금지:
- 일부 link 에 `text-decoration: none` 또는 hover-only underline — 일관성 깨짐.
- 별도 class 가 base 스타일 override (예: `.row-link { text-decoration: ...}` 중복) — base 단일 진실.
- inline style `style="text-decoration:none"` — 버튼류 (`btn-action` 등) 에서 의도된 경우 외 금지.
