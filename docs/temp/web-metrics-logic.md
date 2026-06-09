# 웹 지표 계산 로직 메모

웹 화면에 드러나는 지표들이 내부적으로 어떤 계산을 거치는지, 중요한 부분만 짧게 정리하는 메모. 정식 명세가 아니라 개념 정리용 — 지표가 늘어날 때마다 아래에 절을 추가한다.

용어 한 가지부터. 여러 서버를 하나의 환경 숫자로 합칠 때 두 가지 방식이 있다.
- 산술평균: 서버 1대 = 1표. 작은 서버와 거대 서버가 똑같은 비중.
- capacity-weighted: 자원 총량(코어·메모리·디스크 용량) 기준 가중. 거대 서버가 더 큰 비중. `SUM(used) / SUM(total)` 형태.

이 메모에 나오는 "환경" 단위 활용률은 전부 capacity-weighted다.

---

## 1. 환경 자원 평가 (환경 평균 활용률)

대시보드의 환경 전체 평균 활용률 도넛/바.

- 방식: capacity-weighted. 서버 1대=1표 산술평균이 아니다.
- CPU: 전 서버·전 시점 jiffies delta 통합 `SUM(d_idle) / SUM(d_total)`. jiffies가 코어 수를 내재하므로 코어 많은 서버가 자동으로 큰 비중.
- 메모리: `SUM(used_kb) / SUM(total_kb)`. 메모리 큰 서버가 분모에서 큰 비중.
- 디스크: 전 마운트 통합 `SUM(used_bytes) / SUM(total_bytes)`.
- 시간 결합: 윈도우(기본 7일) 전체를 한 번에 누적해서 단일 스칼라 1개로 환산.

의미: "환경 전체가 보유 자원 대비 평균 몇 %를 쓰고 있나"라는 단일 KPI.

---

## 2. 라이트사이징 분류 (right-sizing)

서버별 idle / shutdown / over / under / optimal / insufficient_data 분류 배지.

- 환경 평균과 다른 경로다. capacity 가중 아님. 서버 1대씩 독립 평가.
- 판정 순서: under(위험 신호 OR) -> idle -> shutdown -> insufficient_data -> over(보수적, cpu·mem 둘 다 낮을 때만) -> optimal.

사용 지표 (USE Method — Utilization 축 + Saturation 축):

| 자원 | 축 | 지표 | 산출 | 임계 |
|------|----|------|------|------|
| CPU | utilization | cpu_p95 | 시점별 `(1-idle/total)*100` 의 서버별 p95 | under >= 70, over <= 30 |
| CPU | (idle 판정) | cpu_peak | 위 값의 MAX | idle: peak <= 1 |
| CPU | saturation | load_15m / cores | load15m MAX / 코어 수 | under: ratio >= 1.0 |
| MEM | utilization | mem_p95 | 시점별 `(1-available/total)*100` 의 서버별 p95 | under >= 80, over <= 50 |
| MEM | saturation | swap_used | 윈도우 안 swap 사용 발생 flag (Linux 한정, Windows pagefile 제외) | under: 사용 시 |
| DISK | capacity | worst mount used% | 서버 worst 마운트 `(1-avail/total)*100` MAX (가상 mount 제외) | under >= 85 |
| DISK | saturation | iowait_p95 | iowait 사용률 p95 | under >= 20 |
| NET | (idle/shutdown) | net_avg_kbps | net rate 평균 | idle/shutdown 판정 보조 |

- under는 위 위험 신호 중 하나라도 hit하면 발화(OR). over는 cpu·mem p95가 둘 다 다운사이즈 임계 이하일 때만(보수적, 한쪽이라도 None/높으면 단정 안 함).
- OS 비대칭: Windows는 load 등 일부 saturation 축이 없다. 분류를 막지 않고 "미관측 축"으로만 표시(confidence 단서).

insufficient_data(데이터 부족) 발현 조건:
- cpu_p95, mem_p95 가 둘 다 None AND under 위험 신호(swap·iowait 등)도 0개일 때만.
- 핵심: "오프라인 서버"라고 발현되는 게 아니다. 평가 윈도우(7일) 안에 메트릭이 한 번이라도 있으면 p95가 산출되어 정상 분류로 간다. 지금 꺼져 있어도 며칠 전 데이터로 평가된다.
- 실제 발현 케이스: 신규 등록 후 미수신, 또는 7일 내내 수집 0(장기 오프라인).

---

## 3. 환경 부하 추이 (시계열 그래프)

대시보드의 환경 전체 CPU·메모리 등 시간축 추이 차트.

- 서버 횡단 결합: capacity-weighted. 1번과 동일 원칙(sum/sum).
- 환경 전체·선택 N대·개별 1대 추이가 모두 동일 경로(metric_trend)를 타고 server_ids 집합만 다르다 — 개별 1대는 N=1 동치라 가중이 무의미하고 그 서버 값 그대로(로직 분기 없음).
- 각 collected_at 시점마다 환경값 1개를 가중으로 만든다. 활용률 `SUM(num)/SUM(den)`, 처리량 `SUM(rate)`, 로드 `SUM(load_15m)/SUM(cpu_cores)`.
- 시간 결합: 시점값들을 time_bucket으로 묶어 버킷별 avg/max/p95.

1번 환경 평균 활용률과의 관계:
- 서버 횡단 가중은 둘 다 capacity-weighted로 동일.
- 차이는 시간 결합뿐이다. 1번은 전 기간을 한 번에 누적한 단일 비율, 3번은 시점값을 먼저 만든 뒤 버킷에서 집계.
- 그래서 추이 그래프 선의 평균과 KPI 카드의 7일 평균이 미세하게 어긋날 수 있다. 이건 오차가 아니라 두 산출물의 목적 차이(단일 스칼라 vs 시간 분해)에서 나오는 구조적 성질 — 어떤 시계열 대시보드에서도 동일. 같은 숫자로 직접 비교만 안 하면 용인 가능.

---

## 4. 운영 신호 (attention)

목록 화면 상단 운영 신호 카드. right-sizing(USE Method)과 별개로, 시스템 운영 이상 3종을 잡는다.

| 신호 | 발화 조건 | 산출 |
|------|----------|------|
| gap_warnings (수집 끊김) | 직전 수집 간격이 5분 이상 벌어졌고, 최근 24시간 안에 발생 | collected_at 간격 |
| os_eol_warnings (OS 지원종료) | OS 지원종료일이 임박/경과 (기준 시각 대비) | 정적 EOL 매핑 (Windows build / Linux os_version) |
| agent_unstable (에이전트 불안정) | 최근 1시간 안 agent 재시작 횟수가 임계(기본 3회, env override) 이상 | agent_started_at DISTINCT 카운트 |

- 표시 범위 차이: 목록 카드는 3종 다 표시. 보고서(window-scoped)는 os_eol만 표시한다. gap/agent_unstable은 보고서의 고정 평가 윈도우와 의미가 안 맞아 보고서에선 뺀다(재부팅·재시작 횟수는 보고서 안에서 별도 카운트로 호스트 상세 표에만 노출).
