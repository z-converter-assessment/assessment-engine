# ADR 0034 — 환경 평균 활용률 capacity-weighted 전환 (서버 동등가중 폐기)

상태: Accepted (2026-06-04)

## Context

대시보드·보고서의 "환경 평균 활용률"(CPU/메모리/디스크)은 서버 동등가중으로 집계했다 — 서버별 평균을 낸 뒤 서버 간 단순 평균(서버 1대=1표). `environment_utilization`(전체 환경)과 `_selection_utilization`(선택 N대, base.rows 재합성), `environment_metric_trend`(추이 차트)가 모두 이 방식이었다.

두 가지 문제가 드러났다.

1. 물리 자원 활용률 왜곡. 2코어 90%와 64코어 10%를 같은 1표로 합치면 환경 평균이 50%가 된다. 그러나 환경 전체 컴퓨팅 자원 관점에서 실제 활용률은 `(2*90 + 64*10)/(2+64) ≈ 12.4%`다. 본 제품은 cloud right-sizing assessment(놀고 있는 용량 = 비용 절감)이므로 "환경 자원이 실제로 얼마나 쓰이나"가 핵심 가치인데, 동등가중은 이를 가린다.

2. 일관성 혼란. 평가 윈도우는 14일 고정인데(ADR 0003·`recommendation.WINDOW_DAYS`), 데이터가 1일치만 있으면 윈도우 안 가용 표본만 평균돼 실효 평균 기간이 데이터 양에 종속된다(자동 clamp 아님 — sparse data의 자연 결과). 사용자에겐 "기간이 자동으로 줄어든 것"처럼 보였다. 또한 VM마다 켜진 시간이 달라 환경 단위의 "실효 기간" 스칼라는 정의 자체가 불가능하다.

## Decision

환경 평균 활용률을 capacity-weighted(자원 총량 가중)로 전환한다. 윈도우 안 전 서버·전 시점 통합 비율 `Σused / Σtotal`로 계산한다.

- CPU: `(1 - Σ d_idle / Σ d_total) x 100` — 전 서버·전 인접쌍 jiffies delta 합. jiffies가 코어별 합산값이라 코어 수가 분모에 내재 -> 코어 수 곱셈 없이 capacity-weighted. `report_aggregate`와 동일한 d_idle/d_total 정의 + boot_time 변경 시 reset 제외.
- MEM: `Σ(mem_total_kb - mem_available_kb) / Σ mem_total_kb x 100`.
- DISK: `Σ(total_bytes - avail_bytes) / Σ total_bytes x 100` (전 mount 통합, 가상 mount 제외). 서버별 worst mount 개념은 환경 평균에서 폐기 — 호스트 상세 표엔 유지.
- 빈 구간/미수집 시점은 분자·분모에서 동시에 빠져 "그 시점 살아있는 VM"만 자동 반영. 서버별 측정 기간 편차도 분모에 녹아들어 별도 정규화·자격 게이트 불필요. 빈 구간을 0으로 채우지 않는다(미수집/idle 구분 불가 -> 활용률 과소평가 + right-sizing 오판 회피).

적용 범위 (단일 산식, 단일 의미):
- 전체 환경 카드 + 선택 N대 보고서: `environment_utilization(period_days, end, server_ids=None|list)` 단일 SQL로 통일. `_selection_utilization` 헬퍼(base.rows 재합성) 폐기. `end`(anchor) 파라미터로 selection 발행 시점 정적 스냅샷 존중.
- 환경 부하 추이 차트: `environment_metric_trend`도 버킷별 capacity-weighted(`Σused/Σtotal`)로 전환 — 카드와 추이가 같은 가중. 같은 페이지에서 카드 값과 차트 값의 의미 불일치 제거.
- 평가 윈도우는 14일 고정 약속 유지(ADR 0003). "데이터에 맞춘 윈도우 자동 조정"은 도입하지 않는다 — VM마다 데이터 범위가 달라 환경 단위로 맞출 기준이 없다.

## Consequences

장점
- 환경 평균이 물리 자원 활용률을 정확히 반영 — "64코어 사놓고 12%만 쓴다"가 right-sizing 가치대로 드러난다.
- 카드·선택 보고서·추이 차트가 단일 산식·단일 의미 -> drift 0.
- 빈 구간/서버별 기간 편차가 분모에 자연 반영돼 별도 정규화 로직 불필요.

한계 / 트레이드오프
- 거대 VM 1대가 환경 평균을 지배할 수 있다. "환경 전체 자원이 얼마나 쓰이나" 관점에선 정확(큰 자원이 큰 영향)이나, "전형적인 서버 한 대 상태"가 궁금하면 부적합 — 그 질문은 서버별 right-sizing 분류(`recommendation.assess`, 서버마다 개별)가 답한다.
- 메모리·디스크는 레벨값이라 표본 밀도 가중이 약간 남는다(발행 주기 불균일 시). 완전 time-weighted 적분은 복잡도 대비 실익이 작아 표본 가중으로 둔다.

## 관계

- ADR 0003(평가 윈도우 14일) 유지 — 본 ADR은 윈도우 안 집계 방식만 변경.
- 서버별 right-sizing 분류(ADR 0029)와 무관 — 환경 요약 표시 지표만의 결정.
