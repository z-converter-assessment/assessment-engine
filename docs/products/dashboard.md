# 대시보드 (Dashboard)

본 문서는 환경 개요 홈(`/`)·서버 목록(`/servers`)·실시간 현황(`/environment/realtime`) 산출물의 존재 의의·구현 의도·근거를 정리한다. 라우터·서비스·매퍼·정적 자원 deep dive는 `docs/architecture/web/` 별도.

## 위치

- 환경 개요 홈 — `GET /` (집계 위젯: 환경 요약·평균 활용률·자원 적정성 분포·운영 신호)
- 서버 목록 — `GET /servers` (검색·필터 + 선택 N대 액션)
- 실시간 현황 — `GET /environment/realtime` (현재 스냅샷 활용률 도넛 + 네트워크/디스크 I/O + 부하 상위)
- 진입점: 엔진 web 첫 페이지(`/`) — 운영자가 가장 자주 보는 화면
- 산출물 형태: HTML SSR. 집계 위젯은 정적 렌더(페이지 진입 시 1회) — 자동 폴링 없음. 서버 목록의 "최근 작업" cell 만 install task 발행·진행 중 추적 polling
- 다른 산출물의 navigation hub — 보고서·진단·Install·Export 모두 본 화면에서 진입

## 환경 개요 홈 (`/`)

운영자가 환경 전체 상태를 한 화면에서 파악·다음 행동 결정하기 위한 entry point. 집계형 위젯만 본 페이지에 남고, 서버 목록은 `/servers`, 환경 단위 분석은 `/environment/*` 로 분리. 정적 집계라 새로고침 시 1회 렌더.

### 영역 1: 환경 요약

- 총 N대 / 온라인·오프라인
- 자원 합계 (vCPU·메모리·디스크)
- 역할 분포 pill (web·db·cache·mq·monitor 등 카테고리별 카운트)

답: "지금 환경에 몇 대 있고 어떻게 분포돼 있나?"

### 영역 2: 환경 평균 활용률 도넛 (3개)

- CPU 24시간 평균 활용률
- 메모리 24시간 평균
- 디스크 평균
- 임계 색 분기 60·80% (UI badge danger·warn 임계)
- 윈도우는 `DASHBOARD_TIME_RANGE`(24h, query_service) 단일 진실 — 최근 현황 모니터링

답: "환경 전체 자원 활용률은 어느 수준인가?"

### 영역 3: 자원 적정성 분포 도넛

- 24시간 측정값 기반 분류 3 카테고리 (under·정상·over)
- `recommendation` 규칙 분류로 자동 계산한 분포 시각화
- Windows (원칙 P2): swap 축 제외(pagefile baseline)·saturation 축 OS 부재라 utilization 축만으로 분류(부분 평가) — 도넛/카운트가 pagefile 사용으로 under 쪽 왜곡되지 않음. 상세 `docs/architecture/web/services.md` "OS 분기" 절

답: "환경 안 자원 부족·자원 과다 서버 비율은?"

### 영역 4: 운영 신호 카드 (3 카탈로그)

운영신호(`AttentionSignals`)는 USE Method 자원 평가와 분리된 인프라 이상 3개만:

| 신호 | 트리거 |
|------|--------|
| 통신 끊김 | metric 발행 갭 — 5분+ 미수신(한때 살아있다 끊김), `metric_gap_warnings` |
| OS EOL | OS 버전(Linux)·build(Windows)가 EOL 카탈로그 경과, `resolve_os_eol` (ADR 0031) |
| 에이전트 재시작 빈번 | 1h fixed 윈도우 재시작 N회+ (`agent_started_at` DISTINCT) |

자원 부족·디스크 사용률·디스크 잔여일은 운영신호가 아니라 자원 적정성 분류(영역 3 분포 도넛 + 자원 부족 호스트 / 보고서 스토리지 컬럼)에 표시 — 중복 회피.

답: "지금 즉시 손대야 할 위험 신호는?"

## 서버 목록 (`/servers`)

검색·필터 + 선택 N대 액션. page 기반 pagination, 기본 20대 표시 후 전체보기/접기.

### 서버 테이블 (행별)

- 컬럼: 선택 / 상태 (online dot) / Hostname / 서비스 (role-chip 카테고리+개수) / OS / 프로비저닝 (자원 적정성 분류) / 최근 작업 / 상세
- 외부 IP 컬럼 없음 — 식별·분류에 미사용. 자원 인벤토리(vCPU/메모리/디스크)도 홈 "환경 요약" KPI 로 통합 (시각 노이즈 회피)
- 서비스 컬럼 — 워크로드 카테고리별 role-chip 칩(카테고리명 + 인스턴스 개수)
- 행별 프로비저닝 — `recommendation.classify` 결과 (`under_provisioned` / `over_provisioned` / `idle` / `shutdown` / `optimal` / `insufficient_data`)
- "최근 작업" column — install task badge (success/failure/pending) + 클릭 시 modal 로 stdout/stderr/failure_reason 디버깅. modal 본문은 server fragment endpoint (`GET /api/tasks/{id}/detail`) HTML 반환 (P3 정공)
- 기본 표시 20대 후 "전체보기"(CLIP 초과 행 노출)/"접기" 토글 — 필터 비활성 상태에서만 적용
- 필터(별도 행으로 분리): search(hostname) / is_online (전체·온라인·오프라인) / service (web/db/cache/mq/container/monitor) / os_id (distro) / classification (자원 적정성 6 분류) — 검색 버튼 없음, dropdown/checkbox 변경 즉시 client-side filter + URL 갱신
- pagination: page=1 default, limit=20 (max 100)

답: "어떤 서버가 어떤 상태인가? 어떤 행동을 권장받나?"

### 행동 버튼 (selection-driven)

list에서 N대 선택 → 다음 액션 활성화:
- 고객 보고서 (양식 A 발행)
- 엔지니어 보고서 (양식 B 발행)
- JSON Export (자동화 도구 입력)
- Install (zconverter task 발행)

답: "선택한 N대에 어떤 다음 단계를 진행할 것인가?"

## 실시간 현황 (`/environment/realtime`)

최신 스냅샷 기준 현재 자원 현황 — 정적 렌더(페이지 진입 시 1회, 자동 폴링 없음).

- 현재 활용률 도넛 (CPU·메모리·디스크) — 환경 평균 도넛과 동일 컴포넌트·단색 게이지, 단 24h 통계 아닌 현재 스냅샷
- 네트워크·디스크 I/O 도넛 — 게이지가 아니라 절대 rate KPI (활용률 도넛과 동일 빈 트랙·모양·폰트)
- 현재 부하 상위 — 최신 스냅샷 호스트 순위

답: "지금 이 순간 환경 부하는 어떤가?"

## 의사결정 근거

활용률 임계 신호:
- UI badge "warn"(노랑)·"danger"(빨강) 두 단계로 시각 구분
- 서버 badge 임계(`_USAGE_WARN_PCT`·`_USAGE_DANGER_PCT`)와 환경 평균 임계(`_UTIL_LOW_PCT`·`_UTIL_HIGH_PCT`)는 별 도메인 — 값은 `web/services/mappers/shared.py` 단일 진실, 대시보드는 표현만

대시보드 현황 윈도우 24시간:
- `DASHBOARD_TIME_RANGE`(query_service) 단일 진실 (CLAUDE.md #F10) — 최근 현황 모니터링, bucket 은 `AUTO_BUCKET[24h]`=30m
- 자원 적정성 표준 평가(`recommendation.WINDOW_DAYS`=7d — 보고서 기본·서버 목록 분류)와 의도 분리
- 대시보드는 윈도우 override 안 함 (보고서만 `?time_range=` 허용)

자원 적정성 분포 도넛 3 카테고리:
- 화면 단순성 우선 — under/optimal/over 3분류로 환경 한눈
- recommendation의 6분류(under/over/idle/shutdown/optimal/insufficient_data)는 보고서·진단 ref에서 정밀화

도넛 중앙 강조 1개만:
- 가장 시급한 카테고리 카운트 1개만 강조 (예: "under 5대")
- 합계·ratio 노출 금지 — 운영자가 행동할 단일 시그널만
- 임계 색 단일 진실 — 동일 의미는 동일 hex (활용률·자원 적정성 분포·capacity trigger 일관, CLAUDE.md #E8)

모든 카테고리 항상 노출 (count 0 포함):
- 환경에 자원 부족이 0이어도 카테고리 카드 노출 (옅은 회색)
- 카드 위치 변동이 운영자 인지에 영향 — 슬롯 고정

## 한계

1. 활용률 도넛은 환경 평균만 — 분포(p50·p95)는 미노출. 양극화 환경에서 misleading (`docs/products/environment-report.md` 한계 #2와 동일 패턴).
2. 행별 권장 단일 라벨 — recommendation 분류 1개만 표시. 다중 신호(예: CPU 정상 + 메모리 부족)는 우선순위 평가 후 1개만.
3. 실시간 현황은 정적 스냅샷 — 진입 시점 1회 렌더라 갱신은 새로고침 의존. 자동 push(SSE/WebSocket) 미도입.

## 관련 문서·코드

- `docs/architecture/web/layering.md` — 라우터 흐름·다이어그램
- `docs/architecture/web/services.md` — query_service·service_classifier
- `docs/architecture/web/view-models.md` — ViewModel 카탈로그·도넛 SVG 상수
- `docs/architecture/web/static-assets.md` — list-table.js·차트 P4 규약
- `docs/products/{environment-report,server-report}.md` — 보고서 산출물 (scope별)
- `docs/products/install-task.md` — "최근 작업" column source
- `src/assessment_engine/web/routers/pages/list_page.py` — overview·서버 목록·실시간 라우터
- `src/assessment_engine/web/templates/servers/overview.html` — 환경 개요 홈
- `src/assessment_engine/web/templates/servers/list_table.html` — 서버 목록 테이블
- `src/assessment_engine/web/static/js/pages/list-table.js` — selection·필터·task cell polling·toast
- CLAUDE.md #E1·#E2·#E3·#E8 — 표시 계층 원칙·데이터 흐름·임계 색 단일 진실
