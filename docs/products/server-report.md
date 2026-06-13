# 서버 보고서 (Server Report)

본 문서는 서버 단위 (scope=server) 산출물의 존재 의의·구현 의도·근거를 정리한다. 운영자가 선택한 N대 또는 단일 서버에 대해 row 단위 상세·자원 적정성 판단을 받는 산출물. 보고서와 진단이 같은 서버 단위라 한 산출물 카탈로그로 통합.

환경 단위 산출물(scope=environment, 분포 카운트·high-level KPI)은 `docs/products/environment-report.md` 별도.

## 두 산출물

서버 scope 에서 두 산출물이 같은 endpoint·SQL·템플릿 위에 서로 다른 의도 만족.

| 산출물 | 라우터 | 의도 |
|--------|--------|------|
| 서버 보고서 (선택 N대) | `GET /reports/servers?ids=<public_id,...>&period_days=14&view=customer\|engineer`, 발행 `POST /reports/servers/emit`. 단일 1대는 `GET /servers/{id}/report` | 선택 N대 row 단위 상세. customer(양식 A) view=KPI 8 컬럼, engineer(양식 B) view=정량 16 컬럼 |
| 서버 진단 | `POST /api/diagnostics` scope=server + 결과 polling | 개별 서버 분류·action·narrative. detail 페이지 "서버 진단" 카드 또는 list 에서 N대 batch 발행 |

두 산출물의 관계 (T13): 보고서·진단 동일 `diagnostic_jobs` 테이블 record. 서버 보고서 라우터가 합성 직후 `record_report_emission` 으로 succeeded row 즉시 INSERT (best-effort). 보고서 이력은 `/reports/history`, 진단 발행 이력은 `/diagnostics/history`.

## 위치

- UI 진입점:
  - 서버 보고서 — 대시보드 list 페이지에서 N대 선택 → "고객 보고서 (N)" / "엔지니어 보고서 (N)" 버튼
  - 서버 진단 — list 에서 N대 선택 → "서버 진단 (N)" 버튼 (batch 발행), 또는 server detail 페이지 "서버 진단" 카드 (단건 발행)
- 발행 경로:
  - 서버 보고서 — 운영자 즉시 호출 (HTTP GET). 발행 시점 즉시 5 SQL round-trip + render
  - 서버 진단 — 운영자 즉시 발행 (웹 모달) — ADR 0023: scheduler cron 폐기, 사용자 trigger 만
- 산출물 형태: HTML SSR. 브라우저 인쇄로 PDF/PPT 캡처 (백엔드 PDF export 미도입)

## 존재 의의

운영자가 단일 서버 또는 N대 batch 에 대한 정량 분석·자원 적정성 판단을 받기 위한 산출물. 다음 질문에 답한다.

질문 1: "이 N대, 어떤 부하 특성을 보이는가?"

16 컬럼 정량 표(engineer view) 또는 8 컬럼 요약 표(customer view) 로 CPU·메모리·load·I/O wait·디스크 I/O·네트워크 I/O·swap·재부팅·분류를 한눈에 비교. row 단위로 정렬·복사·외부 분석 도구 입력 가능.

질문 2: "이 서버 한 대, 자원 배분이 적절한가?"

server detail 페이지에서 "서버 진단" 카드가 USE Method 분류·권장 action·narrative 노출. 운영자가 detail 화면 안에서 즉시 판단 가능 — 별도 보고서·환경 분포 비교 불필요.

질문 3: "환경 분포에서 'under_provisioned 5대' 를 봤다 — 그 5대가 누구인가?"

환경 단위 산출물은 분포 카운트만 — 개별 식별 안 됨. 본 서버 단위 batch 발행으로 어떤 서버가 어떤 분류인지 행 단위 확인. 환경 단위 산출물의 행동 follow-up.

질문 4: "자원 적정성 결정의 근거를 어디서 확인하나?"

engineer view 의 진단·분류 칼럼이 USE Method 임계값 기반 자동 해석 노출. 운영자가 "왜 이 서버가 under_provisioned 인가" 를 같은 행의 CPU p95·메모리 p95·swap·variance 에서 즉시 검증. 별도 detail 페이지 없이 보고서 한 장에서 자원 적정성 의사결정 시그널 확인.

## 산출 정보

### 서버 보고서 — 두 view 공통 상단

KPI 6개 + 환경 총 자원 + 선택 맥락 (선택 N대의 OS 구성·워크로드 한 줄 요약 — "이 묶음이 무엇인지", P-A 구성 계층. `build_selection_context`). 비교 표는 위험 우선 정렬 (`sort_rows_for_report` — under -> attention -> normal).

### view 분기 — customer (양식 A)

목적: 컨설턴트가 고객 미팅·내부 보고에 들고 가는 N대 자원 요약.

구성 = 환경 보고서 본문 공유(customer 분기 — 요약·환경 구성·서비스 구성·환경 요약·자원 적정성 평가(분류 분포·효율화·조치 필요 호스트)·OS 지원 종료, 단일 진실 `docs/products/environment-report.md`) + 세부 서버 목록 표.

세부 서버 목록 컬럼(customer): 상태 · 서버 · 구동 서비스 · OS · 자원(vCPU·MEM·DISK) · CPU 평균 · MEM 평균 · 프로비저닝 · 개별 보고서 링크 (`_shared.html` `detail_server_list` 단일 진실, 환경·선택 공유).

자동 정성 요약 (행동 시그널): 디스크 임박·I/O 병목·재부팅·OS EOL.

판단 근거(임계값 전문)는 모든 보고서 공통 단일 partial(`reports/_thresholds_reference.html`)이 인쇄본 말미에 임베드 — 인쇄본 단독 검토 가능.

### view 분기 — engineer (양식 B)

목적: 운영자·엔지니어 정량 분석 + 자원 적정성 근거 검증.

구성 = 환경 보고서 본문 공유(engineer 분기 — 환경 현황 5축 메트릭·부하 추이+토폴로지·자원 적정성 분류(분포·효율화 검토 대상·리소스 부족 6축 상세)·OS 지원 종료·OS 버전 분포, 단일 진실 `docs/products/environment-report.md`) + 세부 서버 목록 표.

세부 서버 목록 컬럼(engineer): customer 컬럼 + 재부팅 · 에이전트 재시작 (시스템 안정성 — anchor+window 안 카운트).

효율화 검토 대상 표(본문 공유, over/idle/shutdown Top 30): 분류 · 진단 · 권고 · 신뢰도 — 분류는 `recommendation.assess`, 진단은 가장 시급한 신호 1개(`_build_diagnosis`, 데이터 부족 호스트는 원인 진단·오프라인은 "오프라인" 접두), 권고는 분류+trigger 파생(`_build_recommendation_action`), 신뢰도는 `build_confidence_notes`(is_partial·low_sample). 단일 보고서 자원 적정성 평가 표와 동일 판독 프레임.

자동 정성 요약 (customer 시그널 + engineer 추가): 역할별 평균 CPU 최고치·Saturation 발생·CPU 변동성 큼(peak/p95 1.5배+).

### 개별 서버 보고서 — 구동 서비스 (구성 계층)

단일 서버 보고서(`/servers/{id}/report`)는 "이 서버가 무엇을 하는가"를 구성 계층(P-A)으로 노출 — 자원 적정성 평가(활용·평가 계층) 앞에 배치.

- customer: 워크로드 카테고리별 제품명 묶음 (예: "web: nginx, gunicorn" — 포트·unit 숨김, 의미 중심).
- engineer: 등록 서비스(systemd unit) 표 (unit·카테고리·귀속 listen 포트) + listen 포트 전체 표 (process 포함, 사실 중심·최대 상세).
- 데이터: `ReportRowRaw.listen_ports` (보고서 집계 SQL 유입) -> mapper `_build_workload_display` (service_classifier 단일 진실, listen-only 카테고리 `detect_listen_categories` 보강). customer/engineer 차등은 같은 데이터의 노출 깊이 차이 (#E7 카테고리 -> 제품명 -> 포트 3단).

### 개별 서버 보고서 — engineer 심화 계층 (단일 deep-dive)

N대 selection 은 서버 간 비교를 위해 행 단위 정량 표(양식 B)로 압축하지만, 단일 1대(`view=engineer`)는 비교 대상이 없어 그 1대를 카드 계층으로 펼친다 — 구성 -> 사용률(평균 + 심화) -> 추이 -> USE 신호 -> 스토리지 -> 종합 진단 -> 운영 신호 순. CPU 분류(user/system/iowait)·메모리 구성(used/available/cached/buffers)·마운트별 스토리지(worst 1개 아닌 전체)는 N대 표엔 없는 단일 전용 — repo `report_cpu_breakdown`·`report_memory_breakdown`·`report_mount_usage` (개별 server_id 단위). customer 단일은 이 심화를 생략하고 구성·평균 사용률·권고만 (현황 파악 범위). 양식 통일상 단일·selection·환경 모두 `EnvironmentReportSummary`(kind=`env_report`) 공유 — 단일 전용 필드(`server_inventory`·`volumes`·`memory_breakdown`·`cpu_breakdown`)는 selection·환경에서 None/빈 list (#C1).

### 서버 진단 — job 1건당 산출

| 항목 | 내용 | source |
|------|------|--------|
| 평가 윈도우 | 사용자 선택 (15m/1h/6h/24h/7d default/14d/30d) | UI 모달 또는 7d default (`DIAGNOSTIC_DEFAULT_TIME_RANGE`) |
| Anchor 시점 | 사용자 선택 (KST datetime) 또는 현재 | UI 모달 또는 default now |
| 분류 | under/over/idle/shutdown/optimal/insufficient_data | `recommendation.classify` |
| 권장 action | upsize_cpu / upsize_memory / downsize_cpu / downsize_memory / shutdown_idle / no_action | `recommendation` |
| 자연어 narrative | "서버 {hostname}는 최근 {window} 동안 CPU p95 {%}, 메모리 p95 {%} 사용 ..." | `llm/ollama.py` `OllamaLlmClient` (단일 provider, ADR 0025) + 수치 환각 검증 |

산출 결과 예시:
```
서버 db-01는 최근 7일 동안 CPU p95 12.3%, 메모리 p95 35.0% 사용.
분류는 과다 프로비저닝.
AWS Compute Optimizer 임계값(CPU p95 30%) 기준으로 CPU 다운사이즈 권장.
```

## 의사결정 근거

### 분류 임계값 출처

| 분류 | 트리거 조건 | 출처 |
|------|-----------|------|
| under_provisioned | 위험 신호 OR — CPU p95 >= 70 / 메모리 p95 >= 80 / swap 발생 / load >= cores / iowait p95 >= 20 / worst mount >= 85% (`CPU_UPSIZE_P95_PCT`·`MEM_UPSIZE_P95_PCT`·`CPU_SATURATION_LOAD_RATIO`·`IOWAIT_UPSIZE_PCT`·`DISK_CAPACITY_UPSIZE_PCT`) | USE Method + Kleinrock 큐잉 |
| idle | CPU peak <= 1% + 네트워크 <= 1 kBps (`IDLE_CPU_PEAK_PCT`·`IDLE_NET_KBPS`) | AWS Compute Optimizer |
| shutdown | CPU p95 <= 3% + 네트워크 <= 2 Mbps (`SHUTDOWN_CPU_P95_PCT`·`SHUTDOWN_NET_MBPS`) | Azure Advisor "underutilized VM" |
| insufficient_data | CPU·메모리 p95 둘 다 부재 + under 신호 없음 | 평가 불가 (관측 부재) |
| over_provisioned | CPU p95 <= 30 + 메모리 p95 <= 50 둘 다 (`CPU_DOWNSIZE_P95_PCT`·`MEM_DOWNSIZE_P95_PCT`) | AWS Compute Optimizer "over-provisioned" |
| optimal | 위 어디에도 해당 안 함 | residual |

판정 순서 = under -> idle -> shutdown -> insufficient_data -> over -> optimal (`recommendation.assess`, CLAUDE.md #E3).

Windows (원칙 P2/P4): swap 트리거는 Linux 한정 — Windows pagefile 상시 사용은 saturation 아니라 제외. load/iowait도 OS 부재라 Windows는 cpu/mem utilization 축만으로 분류되고 swap·load·iowait 셀은 N/A, 분류 옆에 "부분 평가" 마커 표시. 상세 `right_sizing_thresholds.html`.

### 지표 정의·임계값 (engineer view)

| 지표 | 정의 | 임계값 의미 | 출처 |
|------|------|-----------|------|
| p95 | `percentile_cont(0.95)` over period | 정상 부하의 상한선 — 일시 spike 제외 | AWS Compute Optimizer |
| peak | 시점별 최댓값 | sizing 시 worst case | 운영 통념 |
| CPU% | jiffies delta. boot_time 변경 시 reset 제외 | counter reset 정밀 식별 | /proc/stat 표준 |
| MEM% | (1 - available/total) * 100 | available 우선 (cgroup·page cache 보정) | Linux `/proc/meminfo` MemAvailable 권장 |
| Saturation | load_15m_max / vCPU | >= 1.0 이면 큐 대기 발생 | Kleinrock - Queueing Systems (1975) |
| 변동성 (variance) | peak / p95 | >= 1.5 이면 burst 큼 — peak 기준 sizing 권장 | 본 프로젝트 휴리스틱 |
| DISK I/O | (서버, 시점) device 합산 rate | iops·throughput baseline | `/proc/diskstats` |
| NET I/O | interface 합산 rate | rx·tx baseline | `/proc/net/dev` |

### 진단 칼럼 평가 순서 (engineer view)

1. swap 사용 — "메모리 부족 (스왑 발생)" (paging 발생 자체가 1차 강신호)
2. 디스크 I/O 병목 (iowait p95 >= 20%)
3. CPU saturation (load >= cores)
4. 메모리 압박 (mem p95 >= 80%)
5. CPU 압박 (cpu p95 >= 70%)
6. 변동성 큼 (peak/p95 >= 1.5 — burst)
7. 거의 미사용 (cpu p95 <= 3%)
8. 여유 있음 (cpu <= 30 + mem <= 50 — 축소 검토)
9. 정상

최상위 신호 1개만 노출 — 엔지니어가 가장 시급한 문제를 즉시 식별. 예외 2개: 데이터 부족 분류 호스트는 신호 대신 원인 진단(오프라인—에이전트 미가동 / 누락 메트릭 명시 / 윈도우 내 표본 부족), 오프라인 호스트는 진단 앞에 "오프라인" 접두 (분류는 윈도우 측정 기반 유지).

### 평가 윈도우

- 서버 보고서 default 7일 (`recommendation.WINDOW_DAYS`). URL `?time_range=`(15m~30d) override 가능.
- 서버 진단 7개 옵션 (15m·1h·6h·24h·7d·14d default·30d) — 즉시 발행 모달에서 선택. 짧은 윈도우는 단발 부하·실시간 시연 검증, 긴 윈도우는 신뢰성 증가 최근 변동 반영 늦음.

### view 분기 의도

| 항목 | customer (양식 A) | engineer (양식 B) |
|------|-------------------|-------------------|
| 목적 | 고객 의사결정 한 장 요약 | 정량 분석 + 자원 적정성 근거 |
| 컬럼 수 | 8 (SERVER·ROLE·OS·CPU p95·MEM p95·위험도·상태·진단) | 16 (위 + LOAD·변동성·I/O wait·DISK·NET·SWAP/Mount·Uptime/재부팅·판단) |
| 정성 요약 | 행동 시그널 (고위험·주의·디스크·I/O·재부팅·OS EOL) | 위 + 엔지니어 시그널 (역할별 평균·Saturation·CPU 변동성) |
| 위험도 표시 | 3단계 압축 (high/attention/normal) | 5분류 그대로 + 판단 텍스트 |
| Print 우선 | 인쇄 PDF 대응 | 화면 분석 우선 |

분기 메커니즘: 같은 endpoint·SQL·템플릿. `view` 파라미터로 Jinja2 if 블록 토글. service `get_report(view=view)` → mapper `build_report_summary_bullets(view=view)`.

### 분류 컬럼 vs 진단 컬럼 차이 (engineer view)

- "분류 / 판단" — 본 보고서 윈도우 (period_days) raw 데이터 기반 즉시 분류. URL 파라미터 따라 윈도우 가변.
- "진단 (7일)" — 별도 진단 job 결과 (사용자 발행 — ADR 0023). 7일 고정. 다른 시점에 발행된 job 의 결과라 stale 가능.

같은 분류 이름 (under_provisioned 등) 을 쓸 수 있지만 source·시점 다름 — 두 컬럼이 다르게 보이면 윈도우 차이·진단 job 갱신 지연이 원인.

## 환경 단위 산출물과의 분기

| 항목 | 환경 (`environment-report.md`) | 서버 (본 문서) |
|------|-------------------------------|----------------|
| 발행 단위 | 환경 전체 1건 | 1대 또는 N대 batch (각 1건씩) |
| 보고서 라우터 | `/reports/environment` | `/reports/servers?ids=...` (단일 1대 `/servers/{id}/report`) |
| 진단 scope | environment | server |
| 산출물 | 분류 분포 카운트 + 우선순위 권장 | 개별 서버 분류·action·narrative |
| 답 | "환경 안 over-provisioned 5대 있음" | "이 서버는 under_provisioned, 업사이즈 검토" |
| 운영 단계 | 1단계 — 환경 전체 현황 한눈 | 2단계 — 개별 서버 판단 |

운영자 표준 흐름: 환경 단위로 분포 확인 → 시급한 카테고리의 서버 list 식별 → 서버 단위 batch 로 개별 판단 → detail 화면에서 검증.

## 한계

1. 분류 라벨 어휘가 운영자에게 항상 직관적이지 않음 — "over_provisioned"·"under_provisioned" 의미는 명시적 가이드 (`recommendation.py` 상수) 에 의존. 한국어 라벨이 한국어 사용자에게 더 명확하지만 영어 분류 식별자는 코드·메시지에 박힘.
2. 워크로드 역할 무관 임계 — DB·캐시·앱서버 모두 같은 70%/80% 임계. DB 는 메모리 압박이 정상 운영일 수 있는데도 under_provisioned 로 잡힐 가능성. 역할별 정밀 분기는 향후 별도 결정.
3. anchor 임의 선택 가능 — 운영자가 특정 시점 (부하 spike 발생 직후 등) anchor 로 잡으면 분류가 그 윈도우 한정. 표준 14d default 외 사용 시 운영자가 의도 인지 의무.
4. 진단 job stale 정리 미구현 — 워커 강제 종료 시 `status='running'` job 수동 정리 필요 (CLAUDE.md #F11).
5. 단일 narrative 합성 — 결정론 템플릿이라 운영자가 추가 컨텍스트 반영 불가. 외부 LLM 도입 시 가능해질 영역 (ADR 0010).
6. engineer view 인쇄 폭 한계 — 16 컬럼이라 A4 가로도 빠듯. PDF 대응 안 됨. 화면 분석 또는 가로 모드 인쇄 권장.
7. 표는 위험 우선 기본 정렬 (발행 시점 under -> attention -> normal, 동순위 cpu_p95 DESC). 사용자 임의 재정렬·필터는 미지원 — 추후 client-side sort 도입 검토 후보.
8. 시점 동기화 없음 — engineer view 의 "분류 / 판단" (이번 윈도우 raw) 과 "진단 (7일)" (별도 job) 이 다른 시점·다른 윈도우. 운영자가 두 컬럼 차이를 source 차이로 해석해야 함.
9. URL 길이 한계 — `ids` query string 에 N개 public_id 넣음. N 이 매우 크면 URL 한계. 추후 POST + session 도입 검토.

## 관련 문서·코드

- ADR 0004 — 진단 워커 아키텍처
- ADR 0010 — 진단 규칙 기반 한정
- `docs/architecture/diagnostic.md` — 모듈 구조·흐름
- `docs/architecture/web/routers.md` — `pages.py` 보고서 라우터·view 분기
- `docs/architecture/web/services.md` "Recommendation 분류" — USE Method 임계값 출처
- `docs/architecture/db/timescaledb.md` `_chart_*` 패턴 — counter reset 정밀 식별
- `docs/architecture/web/static-assets.md` "report.html print CSS" — 인쇄 색 처리
- `docs/tradeoffs.md` T13 — 보고서 = diagnostic_jobs 통합
- `src/assessment_engine/recommendation.py` — 분류 임계값 상수 카탈로그
- `src/assessment_engine/diagnostic/submitter.py` — 진단 발행 (ADR 0014). trigger 채널 = web POST 만 (ADR 0023)
- `src/assessment_engine/diagnostic/llm/ollama.py::OllamaLlmClient` — LLM narrative 합성 (ollama HTTP)
- `src/assessment_engine/web/services/diagnostic_service.py` — job 발행·polling
- `src/assessment_engine/web/services/query_service.py::get_report` — 5 SQL round-trip + view 분기
- `src/assessment_engine/web/services/mappers/report.py::build_report_summary_bullets` — view 분기 시그널
- `src/assessment_engine/web/services/mappers/report.py::_build_diagnosis` — 진단 칼럼 우선순위 평가
- `src/assessment_engine/web/templates/servers/report.html` — 양식 A·B 분기 템플릿
- `src/assessment_engine/web/templates/servers/detail.html` — server detail 페이지 진단 카드
- `docs/products/environment-report.md` — 환경 단위 산출물 (cross-reference)
