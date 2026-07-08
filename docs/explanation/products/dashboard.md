# 대시보드 (Dashboard)

본 문서는 사이드바 "모니터링" 그룹 6 페이지 — 환경 개요 홈(`/`)·서버 목록(`/servers`)·환경 자원 평가(`/environment/assessment`)·네트워크 토폴로지(`/environment/topology`)·실시간 현황(`/environment/realtime`)·환경 성능 추이(`/environment/metrics`) — 산출물의 존재 의의·구현 의도·근거를 정리한다. 라우터·서비스·매퍼·정적 자원 deep dive는 `docs/reference/web/` 별도. 사이드바 네비 정의 단일 진실은 `web/templating/setup.py` `NAV_GROUPS`.

## 위치

사이드바 "모니터링" 그룹 (네비 항목명 = 화면 제목):

- 환경 개요 — `GET /` (집계 위젯: 환경 요약·자원 적정성·평균 활용률·수집 건전성)
- 서버 목록 — `GET /servers` (검색·필터 + 선택 N대 액션)
- 환경 자원 평가 — `GET /environment/assessment` (자원 적정성 평가 분포 막대 + 효율화 검토 대상 + 자원 부족 표, 윈도우 override 가능)
- 네트워크 토폴로지 — `GET /environment/topology` (인터랙티브 Cytoscape L3 subnet 그래프)
- 실시간 현황 — `GET /environment/realtime` (현재 스냅샷 활용률 도넛 + 네트워크/디스크 I/O + 부하 상위)
- 환경 성능 추이 — `GET /environment/metrics` (환경 단위 시계열 차트)

공통:
- 진입점: 엔진 web 첫 페이지(`/`) — 운영자가 가장 자주 보는 화면
- 산출물 형태: HTML SSR. 환경 개요·성능 추이·자원 평가 집계 위젯은 정적 렌더(페이지 진입 시 1회). polling 은 두 곳 — 서버 목록 "최근 작업" cell(install task 진행 추적)·실시간 현황(30초 fragment swap)
- 다른 산출물의 navigation hub — 보고서·Install·Export 모두 본 화면에서 진입

## 환경 개요 홈 (`/`)

운영자가 환경 전체 상태를 한 화면에서 파악·다음 행동 결정하기 위한 entry point. 집계형 위젯만 본 페이지에 남고, 서버 목록은 `/servers`, 환경 자원 평가는 `/environment/assessment`, 그 외 환경 단위 분석은 `/environment/*` 로 분리. 정적 집계라 새로고침 시 1회 렌더. 본문 partial = `servers/_environment_overview.html` (4 카드: 환경 요약·자원 적정성·평균 활용률·수집 건전성).

### 영역 1: 환경 요약

- 총 N대 / 자원 합계 (vCPU·메모리·디스크) KPI
- 서비스 식별 N대 + 역할 분포 badge (web·db·cache·mq·container·monitor·remote·file·mail·infra 카테고리별 카운트) / 서비스 미식별 M대

답: "지금 환경에 몇 대 있고 어떻게 분포돼 있나?"

### 영역 2: 자원 적정성 분포

- 자원 적정성 6분류 카운트 가로 막대 (`overview.risk_donut`) — 환경 자원 평가 페이지와 `provisioning_dist_bar` 매크로 공유(단일 소스)
- 윈도우 = `recommendation.WINDOW_DAYS`(14일) — 서버 목록·보고서 분류와 정합(#E3). 평균 활용률(24h)과 구분해 "최근 14일" 라벨 명시
- 홈에는 분포 요약만 — 효율화 검토 대상·자원 부족 상세 표는 환경 자원 평가 페이지(`/environment/assessment`)

답: "환경이 자원 관점에서 적정한가? 부족·과다 서버 분포는?"

### 영역 3: 환경 평균 활용률 도넛 (3개)

- CPU 24시간 평균 활용률
- 메모리 24시간 평균
- 디스크 평균
- 임계 색 분기 60·80% (UI badge danger·warn 임계)
- 윈도우는 `DASHBOARD_TIME_RANGE`(24h, query_service) 단일 진실 — 최근 현황 모니터링

답: "환경 전체 자원 활용률은 어느 수준인가?"

### 영역 4: 수집 건전성

- 온라인 / 오프라인 (Redis `online:{id}` TTL 기준) + 총 N대

답: "지금 몇 대가 살아 있나?"

효율화·자원 부족 상세 표는 본 홈이 아니라 환경 자원 평가 페이지(`/environment/assessment`)로 분리(홈은 분포 요약만). 운영 신호(통신 끊김·OS EOL·에이전트 재시작)는 보고서 OS 지원 종료 카드·서버 상세 등 발화 지점에서 노출 — 홈 집계 위젯에서는 미표시.

## 서버 목록 (`/servers`)

검색·필터 + 선택 N대 액션. page 기반 pagination, 기본 20대 표시 후 전체보기/접기.

### 서버 테이블 (행별)

- 컬럼: 선택 / 상태 (online dot) / Hostname / 서비스 (role-chip 카테고리+개수) / OS / 프로비저닝 (자원 적정성 분류) / 최근 작업 / 상세
- 외부 IP 컬럼 없음 — 식별·분류에 미사용. 자원 인벤토리(vCPU/메모리/디스크)도 홈 "환경 요약" KPI 로 통합 (시각 노이즈 회피)
- 서비스 컬럼 — 워크로드 카테고리별 role-chip 칩(카테고리명 + 인스턴스 개수)
- 행별 프로비저닝 — `recommendation.classify` 결과 (`under_provisioned` / `over_provisioned` / `idle` / `shutdown` / `optimal` / `insufficient_data`)
- "최근 작업" column — install task badge (success/failure/pending) + 클릭 시 modal 로 stdout/stderr/failure_reason 디버깅. modal 본문은 server fragment endpoint (`GET /api/tasks/{id}/detail`) HTML 반환 (P3 정공)
- 기본 표시 20대 후 "전체보기"(CLIP 초과 행 노출)/"접기" 토글 — 필터 비활성 상태에서만 적용
- 필터(별도 행으로 분리): search(hostname) / is_online (전체·온라인·오프라인) / service (web/db/cache/mq/container/monitor/remote/file/mail/infra) / os_id (distro) / classification (자원 적정성 6 분류) — 검색 버튼 없음, dropdown/checkbox 변경 즉시 client-side filter + URL 갱신
- pagination: page=1 default, limit=20 (max 100)

답: "어떤 서버가 어떤 상태인가? 어떤 행동을 권장받나?"

### 행동 버튼 (selection-driven)

list에서 N대 선택 → 다음 액션 활성화:
- 고객 보고서 (양식 A 발행)
- 엔지니어 보고서 (양식 B 발행)
- JSON Export (자동화 도구 입력)
- Install (zconverter task 발행)

답: "선택한 N대에 어떤 다음 단계를 진행할 것인가?"

## 환경 자원 평가 (`/environment/assessment`)

환경 전체 자원 적정성 평가 — 분류 분포 + 효율화/자원 부족 호스트 표. 엔지니어 보고서 본문과 동일 표(`reports/_resource_tables.html` 공유)를 화면용으로 노출. 본문 partial = `servers/_assessment_result.html`.

- 자원 적정성 평가 막대: 자원 적정성 6분류 카운트 막대 (`overview.risk_donut`). 평가 대상 N대 표기.
- 효율화 검토 대상 표: over/idle/shutdown 호스트 — 합산 vCPU/메모리 절감 여지.
- 자원 부족 표: under_provisioned 호스트 6축 메트릭 + 권고. 초과 행은 더보기/접기 토글.
- 구간·앵커 선택: `?time_range=`(15분~30일) + 기준 시각 override. 변경 즉시 `assessment.js` 가 `?fragment=result` 로 본문 swap. 기본 윈도우는 `DASHBOARD_TIME_RANGE`(24h). 보고서·서버 목록 분류 표준 평가(`recommendation.WINDOW_DAYS`=14d)와 의도 분리 — 본 평가 페이지만 대시보드 중 윈도우 override 허용(#F10).
- Windows (원칙 P2): swap 축 제외(pagefile baseline)·saturation 축 OS 부재라 utilization 축만으로 분류(부분 평가). 상세 `docs/reference/web/services.md` "OS 분기" 절.

답: "환경 안 자원 부족·자원 과다 서버는 누구이고, 무엇부터 손대야 하나?"

## 네트워크 토폴로지 (`/environment/topology`)

L3 subnet 공동소속 추론 그래프 — 인터랙티브 Cytoscape.js (vendored) 렌더. 화면 전용 (보고서는 정적 서브넷 요약 표만, `docs/explanation/products/environment-report.md`).

- 서브넷 연결도: 노드=subnet/host, 가상망·IPv6·단독 subnet 제외. OS(linux/windows)로만 구분.
- 서브넷별 서버 표: net_key·host_count 집계.
- 추론이라 실측 reachability 아님 — caveat 노출(#E9). 매퍼 `mappers/topology.build_network_topology` 단일 진실.

답: "어떤 서버가 같은 서브넷에 묶여 있나?"

## 실시간 현황 (`/environment/realtime`)

최신 스냅샷 기준 현재 자원 현황 — `realtime.js` 가 30초 주기로 `?fragment=realtime` fetch 후 `#rt-mount` swap + 갱신 시각 표시 (P3 정공 — 1회 fetch 아니라 polling).

- 현재 활용률 도넛 (CPU·메모리·디스크) — 환경 평균 도넛과 동일 컴포넌트·단색 게이지, 단 24h 통계 아닌 현재 스냅샷
- 네트워크·디스크 I/O 도넛 — 게이지가 아니라 절대 rate KPI (활용률 도넛과 동일 빈 트랙·모양·폰트)
- 현재 부하 상위 — 최신 스냅샷 호스트 순위

답: "지금 이 순간 환경 부하는 어떤가?"

## 환경 성능 추이 (`/environment/metrics`)

환경 단위 시계열 차트 — 전 서버 capacity-weighted 평균 추이. 본문 = `servers/environment_metrics.html`.

- CPU·메모리·파일시스템·네트워크·디스크 I/O 추이 + CPU 분류·실행 큐(os-aware Linux/Windows 2선)·디스크 I/O 포화(Linux await ms / Windows 큐 깊이 이중 축)·스왑(OS별)·TCP 재전송율.
- 구간(globalRange)·앵커 토글 — `?time_range=` + 기준 시각, 차트 P4 동적 fetch (`AUTO_BUCKET[range]` 동적 bucket, #F10).
- 선택 N대 진입(`?ids=`) 시 "선택 N대 성능 추이" 로 제목·집계 범위 한정.

답: "환경 전체 자원 추이가 시간에 따라 어떻게 변했나?"

## 의사결정 근거

활용률 임계 신호:
- UI badge "warn"(노랑)·"danger"(빨강) 두 단계로 시각 구분
- 서버 badge 임계(`_USAGE_WARN_PCT`·`_USAGE_DANGER_PCT`)와 환경 평균 임계(`_UTIL_LOW_PCT`·`_UTIL_HIGH_PCT`)는 별 도메인 — 값은 `web/services/mappers/shared.py` 단일 진실, 대시보드는 표현만

대시보드 현황 윈도우 24시간:
- `DASHBOARD_TIME_RANGE`(query_service) 단일 진실 (CLAUDE.md #F10) — 최근 현황 모니터링, bucket 은 `AUTO_BUCKET[24h]`=30m
- 자원 적정성 표준 평가(`recommendation.WINDOW_DAYS`=14d — 보고서 기본·서버 목록 분류)와 의도 분리
- 환경 개요 홈·실시간·성능 추이는 표준 윈도우 고정. 환경 자원 평가 페이지(`/environment/assessment`)만 `?time_range=` override 허용 (평가 페이지 기본값도 `DASHBOARD_TIME_RANGE`)

자원 적정성 평가 분류 막대 (환경 자원 평가 페이지):
- 6분류(under/over/idle/shutdown/optimal/insufficient_data) 카운트 막대 — `recommendation.assess` 규칙 분류, `build_risk_donut_segments`
- 분류명은 한국어(LABEL_KO) 단일 진실 — 영어 enum 노출 금지, 보고서·화면 통일
- 막대 색은 게이지 테마 단색 통일 (라벨이 의미 전달) — `UTIL_GAUGE_COLOR`
- 임계 색 단일 진실 — 동일 의미는 동일 hex (활용률·자원 적정성·capacity trigger 일관, CLAUDE.md #E8)

모든 카테고리 항상 노출 (count 0 포함):
- 환경에 자원 부족이 0이어도 카테고리 카드 노출 (옅은 회색)
- 카드 위치 변동이 운영자 인지에 영향 — 슬롯 고정

## 한계

1. 활용률 도넛은 환경 평균만 — 분포(p50·p95)는 미노출. 양극화 환경에서 misleading (`docs/explanation/products/environment-report.md` 한계 #2와 동일 패턴).
2. 행별 권장 단일 라벨 — recommendation 분류 1개만 표시. 다중 신호(예: CPU 정상 + 메모리 부족)는 우선순위 평가 후 1개만.
3. 실시간 현황은 30초 polling 갱신 — server push(SSE/WebSocket) 미도입. 주기 사이 변화는 다음 fetch까지 미반영.

## 관련 문서·코드

- `docs/reference/web/layering.md` — 라우터 흐름·다이어그램
- `docs/reference/web/services.md` — query_service·service_classifier
- `docs/reference/web/view-models.md` — ViewModel 카탈로그·도넛 SVG 상수
- `docs/reference/web/static-assets.md` — list-table.js·차트 P4 규약
- `docs/explanation/products/{environment-report,server-report}.md` — 보고서 산출물 (scope별)
- `docs/explanation/products/install-task.md` — "최근 작업" column source
- `src/assessment_engine/web/routers/pages/list_page.py` — overview·서버 목록·자원 평가·토폴로지·실시간·성능 추이 라우터
- `src/assessment_engine/web/templating/setup.py` `NAV_GROUPS` — 사이드바 네비 정의 단일 진실
- `src/assessment_engine/web/templates/servers/overview.html` (+ `_environment_overview.html`) — 환경 개요 홈
- `src/assessment_engine/web/templates/servers/assessment.html` (+ `_assessment_result.html`) — 환경 자원 평가
- `src/assessment_engine/web/templates/servers/topology.html` — 네트워크 토폴로지
- `src/assessment_engine/web/templates/servers/realtime.html` (+ `_environment_realtime.html`) — 실시간 현황
- `src/assessment_engine/web/templates/servers/environment_metrics.html` — 환경 성능 추이
- `src/assessment_engine/web/templates/servers/list_table.html` — 서버 목록 테이블
- `src/assessment_engine/web/static/js/pages/{list-table,assessment,realtime}.js` — selection·필터·task cell polling / 자원 평가 swap / 실시간 30초 polling
- CLAUDE.md #E1·#E2·#E3·#E8 — 표시 계층 원칙·데이터 흐름·임계 색 단일 진실
