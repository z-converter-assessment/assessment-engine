# Right-sizing 분류 — 명세·판정 순서·임계 근거·OS 분기·한계

> 분류 단일 진실 = `recommendation.assess(stats) -> Assessment(recommendation, triggers, unmeasured)`.
> 본 문서는 그 명세(정의·판정 순서·임계 출처·OS 분기·한계)의 단일 진실이다.
> 코드 상수: `src/assessment_engine/recommendation.py`. 사용자 노출 요약: `reports/_thresholds_reference.html`(보고서 참고자료).
> 결정 기록: ADR 0029 (OS-aware + evidence 재설계). 변경 시 본 문서·코드 상수·`_thresholds_reference.html`·ADR 0029 동시 갱신(#F9).

## 1. 목적·범위

right-sizing = 관측 부하(`WINDOW_DAYS` = 7일 통계) 대비 할당 자원의 적정성 평가. 규칙 기반 결정적 분류 — 자원(CPU/Mem/Disk)별로 "가진 축"을 신호로 모아 단일 분류 하나 + 근거(triggers) + 미관측 축(unmeasured)을 산출한다. 가진 데이터로 항상 결론을 내며("어떤 데이터로 이 분류" 설명 가능), OS 비대칭(Windows saturation 축 부재)은 분류를 막지 않고 confidence 단서로만 노출한다.

UI badge 임계(`mappers._USAGE_DANGER_PCT`/`_USAGE_WARN_PCT`, 90/75)와는 별 도메인이다 — 그쪽은 시점 사용량 시각 신호, 본 모듈은 윈도우 통계 기반 사이징 결정. 혼용 금지.

`classify(stats)` 는 분류 enum 만 돌려주는 호환 wrapper. 표시 파생(권고 문구·배지)은 `assess().triggers` 를 재사용한다(임계 재계산 금지).

## 2. 분류 6종

idle / shutdown / over_provisioned / under_provisioned / optimal / insufficient_data.

## 3. 판정 순서 (단일 진실)

`assess()` 는 아래 순서로 평가하고, 먼저 매칭되는 분류로 확정한다.

| 순위 | 분류 | 조건 |
|------|------|------|
| 1 | under_provisioned | 위험 신호 OR (하나라도): cpu_p95 >= 70 / mem_p95 >= 80 / worst mount >= 85 / (load_15m / cores) >= 1.0 / iowait_p95 >= 20 / swap(Linux) |
| 2 | idle | cpu_peak <= 1% AND net_avg <= 1 KB/s (위험 신호 0) |
| 3 | shutdown | cpu_p95 <= 3% AND net_avg <= 2 Mbps (위험 신호 0) |
| 4 | insufficient_data | cpu_p95·mem_p95 둘 다 부재 AND under 위험 신호 0 |
| 5 | over_provisioned | cpu_p95 <= 30% AND mem_p95 <= 50% (둘 다 관측·낮음, 보수적 AND) |
| 6 | optimal | 위 모두 미해당 |

핵심 원칙 — under 가 idle/shutdown 보다 우선이다. saturation/압박 신호(swap·iowait·load·고이용·용량 초과)가 하나라도 있으면, CPU 가 낮아도 그 호스트는 "미사용(idle/shutdown)"이 아니라 자원 부족이다. 예: CPU 는 idle 인데 page-out(swap)이 발생 = 메모리 부족 -> under_provisioned. idle/shutdown 이 under 를 가로채면 "누락 0" 원칙이 깨지고, 스왑 중인 호스트를 종료 권장으로 오분류한다(과거 버그, 본 순서로 수정).

`net_avg_kbps` 가 없으면 idle/shutdown 판정은 skip(fall-through)된다. idle/shutdown 은 net + cpu 가 있어야 평가된다.

## 4. 임계 카탈로그 (출처)

| 신호(trigger key) | 임계 | USE 축 | 출처 |
|-------------------|------|--------|------|
| cpu_util | cpu_p95 >= 70% | Utilization | Kleinrock — Queueing Systems (1975) · Google SRE |
| mem_util | mem_p95 >= 80% | Utilization | Linux page cache 압박 시작점 |
| disk_capacity | worst mount used >= 85% | Utilization | Cloud Advisor storage capacity |
| cpu_saturation | load_15m / cores >= 1.0 | Saturation | USE Method (Brendan Gregg) — run queue |
| disk_io | iowait_p95 >= 20% | Saturation | USE Method — disk IO 병목 |
| mem_saturation | swap_used (Linux) | Saturation | Linux page-out = 메모리 압박 |
| (idle) | cpu_peak <= 1% AND net <= 1 KB/s | Utilization | AWS Compute Optimizer |
| (shutdown) | cpu_p95 <= 3% AND net <= 2 Mbps | Utilization | Azure Advisor |
| (over) | cpu_p95 <= 30% AND mem_p95 <= 50% | Utilization | AWS Compute Optimizer + GCP (headroom 30%) |

임계 상수는 `recommendation.py` 단일 진실(`CPU_UPSIZE_P95_PCT` 등). 본 표는 그 값의 표시본 — 값 변경 시 동시 갱신.

## 5. 합성 규칙

- under = 위험 신호 OR — 어떤 자원이든 고이용·포화·용량 초과가 하나라도 hit 되면 발화(누락 0). hit 된 신호(triggers)를 근거로 동반한다.
- over = 가용 이용률 AND — cpu·mem p95 가 둘 다 있고 둘 다 다운사이즈 임계 이하일 때만(보수적). 한쪽이라도 None 이거나 높으면 over 로 단정하지 않는다(saturation 못 본 Windows 오판 회피).
- insufficient_data = cpu_p95·mem_p95 가 둘 다 None 일 때만(진짜 평가 불가 = 신규/표본 부재). 후순위 — swap·iowait 등 saturation 신호가 있으면 util 부재여도 위 under 에서 이미 결론낸다. OS 메트릭 부재만으로는 미발화.

## 6. OS 분기 (Windows)

- swap: `swap_saturation(os_family, swap_used) = swap_used and os_family != "windows"` 단일 helper 경유 의무. Windows pagefile 은 여유 RAM 에도 상시 사용되는 baseline 이라 saturation 신호가 아니다(제외). Linux/unknown 은 swap_used 그대로 page-out 신호로 해석. `if raw.swap_used` 직접 해석 금지.
- load/iowait: Windows 는 loadavg·iowait 가 OS 부재라 saturation 축을 못 본다. 값 None 인 축은 `unmeasured` 에 기록 -> `is_partial`(=bool(unmeasured)) -> ViewModel/템플릿이 "이용률 기준 평가(saturation 축 미관측)" confidence 단서로 노출한다. 분류 자체는 utilization/capacity 로 완결되며 "표본 부족"이 아니다(cpu_p95·mem_p95 가 산출되는 한).
- Windows agent 가 등가 카운터(Processor Queue Length 등)를 발행하면 미관측 축이 자동 채워진다(unmeasured 자동 해제).

동일 통계라도 Linux 는 load/swap 으로 under 결론, Windows 는 utilization 기준으로 같은 분류 체계 안에서 결론난다.

## 7. 근거(triggers) 재사용

trigger 6키(`cpu_util`·`mem_util`·`disk_capacity`·`cpu_saturation`·`disk_io`·`mem_saturation`)는 도메인 식별자(머신용). report mapper 권고(`_build_under_provisioned_reason`)와 attention capacity 배지(`to_capacity_warning_item`)가 `assess.triggers` 를 재사용해 한국어 권고로 변환한다(P2 — 임계 재계산 금지). stats 생성은 `build_resource_stats` 공용(report·attention 단일 진실).

## 8. 한계

- Windows saturation 미관측: Windows 는 load/iowait 부재라 saturation 병목을 분류로 못 잡고 utilization 축만으로 판정한다(부분 평가). `docs/tradeoffs.md` T14.
- 디스크 용량 환경 집계: 개별 호스트 `disk_capacity` trigger(worst mount used %)는 단일 마운트 비율이라 OS 무관하게 신뢰 가능하나, 환경 전체 디스크 활용률을 자원 총량으로 합산(Σtotal_bytes)하는 capacity-weighted 집계는 Windows 물리디스크/디바이스(major·minor) 인식이 불완전해 신뢰가 떨어진다 — 환경 p95 등 디스크 합산 지표 도입 시 주의(현재 환경 평균 활용률 disk 바만 유지, 환경 p95 는 CPU·메모리만).
- p95 표본: 윈도우(7일)보다 데이터가 짧으면 p95 표본 신뢰도가 저하된다. cpu_p95·mem_p95 둘 다 부재면 insufficient_data.
