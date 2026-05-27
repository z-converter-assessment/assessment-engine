# ADR 0029 — OS-aware right-sizing 분류 (Windows swap 제외 + 부분 평가)

상태: Accepted

## Context

ADR 0027 로 Windows agent 가 합류했다. agent 는 raw 값을 canonical(Linux `/proc` 모델)로 변환 발행하고 엔진은 OS 무관 단일 공식으로 계산한다(`docs/architecture/agent.md`). 그러나 right-sizing 분류(`recommendation.classify`)는 USE Method 임계를 OS 무관(OS-blind)으로 적용해 왔고, 이게 Windows 에서 두 가지 왜곡을 낳았다:

1. swap short-circuit — `classify` 는 `swap_used=True` 면 cpu/mem 사용률과 무관하게 즉시 `under_provisioned` 로 판정한다(Linux page-out = 메모리 압박 신호). 그런데 Windows pagefile 은 여유 RAM 에도 상시 사용되는 baseline 이라 swap_used 가 거의 항상 True → 사실상 모든 Windows 호스트가 `under_provisioned` 로 분류되고, idle/over/optimal 신호까지 가려진다. 대시보드 프로비저닝 분포 도넛·환경 swap_pressure 카운트·보고서 판단 컬럼·attention 스왑 배지 모두 같은 왜곡을 공유했다.

2. saturation 축 부재의 "통과" 처리 — Windows 는 loadavg(null)·iowait(의미 부재) 가 OS 부재라 `classify` 의 saturation 축이 skip(=정상 통과)된다. 결과적으로 Windows 는 utilization 축만으로 판정되는데, 결과 라벨은 Linux 풀-축 판정과 동일한 confidence 로 표시돼 "정보 부족"을 "확정 판정"으로 오인시킨다.

원칙을 먼저 합의했다 (대화 기록):
- P1 미측정은 미측정으로 (N/A, 0 으로 날조 금지).
- P2 OS 의미가 다른 신호는 OS 별 해석 (swap = Linux 한정 saturation).
- P3 전체환경 집계는 비교 가능한 축만.
- P4 부분 평가 가시화 (confidence 단서 노출).

## Decision

`classify` 를 OS-aware 로 전환한다. 단, Linux 동작은 비트 단위 보존이 절대 조건이다.

1. `ResourceStats.os_family: str | None = None` 추가. default None(unknown)은 Linux 로 취급 — 기존 호출처·동작 무손상.
2. `swap_saturation(os_family, swap_used) -> bool` 단일 helper = `swap_used and os_family != "windows"`. swap 을 saturation 으로 해석하는 모든 지점이 본 helper 경유: `classify` short-circuit · report mapper(`_build_under_provisioned_reason`·`_build_diagnosis`) · attention 스왑 배지 · 환경 swap_pressure 카운트. Linux/unknown 은 `swap_used` 그대로 반환(회귀 0).
3. `is_partial_evaluation(stats) -> bool` = `os_family == "windows"`. report mapper 가 호출해 `ReportRowItem.is_partial` precompute(P2), 템플릿은 bool 만 분기(P3)해 "부분 평가" 마커 표시.
4. 표시(P1): Windows 의 swap·load·iowait 셀은 N/A (보고서 템플릿 `os_family == 'windows'` 분기 — load/iowait 는 ADR 0027 합류 시 이미 적용, 본 ADR 에서 swap 셀 + 부분 평가 마커 확장).
5. classify 호출처 8곳(aggregator 2 · query_service 3 · server/report/export mapper 3) 모두 `os_family` 전달.

분류 의미 단일 진실은 `recommendation.py` 코드 + UI 참고자료 `right_sizing_thresholds.html` "OS 분기" 절.

## Options Considered

1. OS-aware classify + `swap_saturation`/`is_partial_evaluation` helper — 채택
   - 장점: swap 해석을 단일 helper 로 모아 중복 0. Linux default None 으로 회귀 0. 부분 평가를 명시적 ViewModel 필드로 노출.
   - 단점: classify 호출처 8곳에 os_family 전달 필요(기계적).

2. agent 가 Windows 에서 swap_total/free 를 null 발행 (swap 데이터 자체 제거)
   - 장점: 엔진 무변경.
   - 단점: agent repo 합의 필요 + Windows pagefile 사용량 자체는 유효 데이터(표시 가치 있음)인데 버리게 됨. 의미는 "saturation 신호 아님"이지 "데이터 없음"이 아니다.

3. OS-blind 유지 + 표시만 주의 문구
   - 장점: 코드 최소.
   - 단점: 분류 자체가 틀린 채 남아 대시보드 도넛·카운트 왜곡 지속.

옵션 1 채택 — 왜곡의 근원(분류 로직)을 OS-aware 로 고치되 Linux 회귀 0 보장.

## Consequences

장점
- Windows 호스트가 pagefile 사용만으로 under_provisioned 로 오분류되지 않음. 도넛/카운트/배지/판단 컬럼 일관 정정.
- swap 해석이 `swap_saturation` 단일 진실 — OS 정책 변경 시 한 곳만 수정.
- 부분 평가가 `is_partial` 로 명시 노출 — 운영자가 Windows 판정의 confidence 한계 인지.
- Linux 동작 비트 보존 (테스트 `test_swap_saturation_*`·`test_classify_windows_*` 가 회귀 가드).

단점·한계
- Windows 는 saturation 축(load/iowait)을 못 봐 utilization 축만으로 판정 — saturation 병목을 분류로 못 잡는다 (부분 평가, `docs/tradeoffs.md` T14).
- os_family None(unknown)을 Linux 로 취급 — agent 가 os_family 미발행(옛 minor) 시 Windows 라도 Linux 로 분류될 수 있음. ADR 0027 의 os_family not-null tighten 완료로 수렴.

## 관련 문서·코드

- `src/assessment_engine/recommendation.py` — `swap_saturation`·`is_partial_evaluation`·`classify(ResourceStats.os_family)` 단일 진실
- `docs/architecture/web/services.md` "OS 분기" 절 — 분류 deep-dive
- `src/assessment_engine/web/templates/reports/right_sizing_thresholds.html` — 판정 순서·OS 분기 UI 참고자료
- `docs/tradeoffs.md` T14 — Windows 부분 평가 한계
- ADR 0027 — Windows agent 합류 (본 ADR 의 전제)
