# 대시보드 (Dashboard)

본 문서는 대시보드(`/servers/`) 산출물의 존재 의의·구현 의도·근거를 정리한다. 라우터·서비스·매퍼·정적 자원 deep dive는 `docs/architecture/web/` 별도.

## 위치

- URL: `GET /servers/`
- 진입점: 엔진 web 첫 페이지 — 운영자가 가장 자주 보는 화면
- 산출물 형태: HTML SSR. JS가 실시간 SSE로 갱신
- 다른 산출물의 navigation hub — 보고서·진단·Install·Export 모두 본 화면에서 진입

## 존재 의의

운영자가 환경 전체 상태를 한 화면에서 파악·다음 행동 결정하기 위한 entry point. 다음 4 영역으로 구성.

### 영역 1: 환경 요약 (page=1·검색·필터 미사용 시만)

- 총 N대 / 온라인·오프라인
- 자원 합계 (vCPU·메모리·디스크)
- 역할 분포 pill (web·db·cache·mq·monitor 등 카테고리별 카운트)

답: "지금 환경에 몇 대 있고 어떻게 분포돼 있나?"

### 영역 2: 환경 평균 활용률 도넛 (3개)

- CPU 14일 평균 활용률
- 메모리 14일 평균
- 디스크 평균
- 임계 색 분기 60·80% (UI badge danger·warn 임계)
- 평가 윈도우는 `recommendation.WINDOW_DAYS` 단일 진실

답: "환경 전체 자원 활용률은 어느 수준인가?"

### 영역 3: 프로비저닝 분포 도넛

- 14일 측정값 기반 분류 3 카테고리 (under·정상·over)
- 진단 워커가 자동 계산한 분포 시각화

답: "환경 안 under-provisioned·over-provisioned 서버 비율은?"

### 영역 4: 주의 신호 카드 (6 카탈로그)

| 신호 | 트리거 |
|------|--------|
| 통신 끊김 | `online:{id}` Redis TTL 만료 |
| 디스크 사용률 임박 | mount 사용률이 임계 초과 |
| 자원 부족 | recommendation.classify under_provisioned |
| 디스크 잔여 30일 | mount 채워 짐 30일 안 예상 |
| OS EOL | OS 버전이 EOL 카탈로그에 포함 |
| 에이전트 재시작 빈번 | 1h 슬라이딩 윈도우 N+회 |

답: "지금 즉시 손대야 할 위험 신호는?"

### 영역 5: 서버 테이블 (행별)

- 컬럼: hostname / role / OS / online / CPU / MEM / disk / 권장 / 최근 작업
- 행별 권장 조치 — recommendation.classify 결과 badge
- "최근 작업" column — install task badge (success/failure/pending) + 클릭 시 modal로 stdout/stderr/failure_reason 디버깅
- pagination: page=1 default, limit=20 (max 100)

답: "어떤 서버가 어떤 상태인가? 어떤 행동을 권장받나?"

### 영역 6: 행동 버튼 (selection-driven)

list에서 N대 선택 → 다음 4 액션 활성화:
- 고객 보고서 (양식 A 발행)
- 엔지니어 보고서 (양식 B 발행)
- JSON Export (자동화 도구 입력)
- 서버 진단 (batch 발행)
- Install (zconverter task 발행)

답: "선택한 N대에 어떤 다음 단계를 진행할 것인가?"

## 의사결정 근거

활용률 임계 60·80%:
- UI badge "warn"(노랑)·"danger"(빨강) 두 단계로 시각 구분
- 60% 미만은 정상 녹색·여유. 60%+ 노랑 주의·80%+ 빨강 위험
- `_USAGE_WARN_PCT=75`·`_USAGE_DANGER_PCT=90`이 코드 단일 진실 (`mappers.py`). 대시보드는 그 표현
- 다만 환경 평균은 60·80% (서버 단위 임계와 다른 도메인 — 환경 평균이 80%면 매우 위험)

평가 윈도우 14일:
- `recommendation.WINDOW_DAYS` 단일 진실 (CLAUDE.md #F10)
- 대시보드는 윈도우 override 안 함 (보고서만 `?period_days=N` 허용) — 산업 표준 윈도우 고정

prov 분포 도넛 3 카테고리:
- 화면 단순성 우선 — under/optimal/over 3분류로 환경 한눈
- recommendation의 5분류(under/over/idle/shutdown/optimal/insufficient_data)는 보고서·진단 ref에서 정밀화

도넛 중앙 강조 1개만:
- 가장 시급한 카테고리 카운트 1개만 강조 (예: "under 5대")
- 합계·ratio 노출 금지 — 운영자가 행동할 단일 시그널만
- 임계 색 단일 진실 — 동일 의미는 동일 hex (활용률·프로비저닝 분포·capacity trigger 일관, CLAUDE.md #E8)

모든 카테고리 항상 노출 (count 0 포함):
- 환경에 under_provisioned가 0이어도 카테고리 카드 노출 (옅은 회색)
- 카드 위치 변동이 운영자 인지에 영향 — 슬롯 고정

## 한계

1. page=1 + 검색·필터 미사용 시만 상단 요약·도넛·신호 노출 — 검색·다음 페이지에선 raw 테이블만. 의도된 단순화이지만 운영자가 "왜 갑자기 사라졌나" 혼란 가능. UI 가이드 보강 후보.
2. SSE 단일 채널 + 서버 측 필터링 (T5) — 동시 운영자 ↑ 시 broker 부하. 본 프로젝트 규모는 OK.
3. 활용률 도넛은 환경 평균만 — 분포(p50·p95)는 미노출. 양극화 환경에서 misleading (`docs/products/customer-report.md` 한계 #2와 동일 패턴).
4. 행별 권장 단일 라벨 — recommendation 분류 1개만 표시. 다중 신호(예: CPU 정상 + 메모리 부족)는 우선순위 평가 후 1개만.
5. 환경 진단 결과 자동 노출 — list 페이지가 매일 03시 cron 실행된 최근 succeeded 진단을 자동 표시. 사용자 명시 발행 안 해도 정보 노출. 다만 진단 워커 중단 시 stale 표시 위험.

## 관련 문서·코드

- `docs/architecture/web/layering.md` — 라우터 흐름·다이어그램
- `docs/architecture/web/services.md` — query_service·diagnostic_service·service_classifier
- `docs/architecture/web/view-models.md` — ViewModel 카탈로그·도넛 SVG 상수
- `docs/architecture/web/static-assets.md` — list.js·차트 P4 규약
- `docs/products/{environment-diagnostic,server-diagnostic}.md` — 진단 결과의 source
- `docs/products/install-task.md` — "최근 작업" column source
- `src/assessment_engine/web/routers/pages.py::list_servers` — 라우터
- `src/assessment_engine/web/templates/servers/list.html` — 메인 템플릿
- `src/assessment_engine/web/static/js/pages/list.js` — selection·polling·toast
- CLAUDE.md #E1·#E2·#E3·#E8 — 표시 계층 원칙·데이터 흐름·임계 색 단일 진실
