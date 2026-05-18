# 엔지니어 보고서 (양식 B)

본 문서는 엔지니어 보고서(`/servers/report?view=engineer`)의 존재 의의·산출 정보·의사결정 근거를 정리한다. 라우터·서비스·템플릿 deep dive는 `docs/architecture/web/routers.md`·`services.md`·`static-assets.md` 별도. 같은 endpoint·SQL·템플릿에서 `view` 파라미터로 분기되며, 양식 A(고객 보고서)와의 관계는 `docs/products/customer-report.md` 참조.

## 위치

- URL: `GET /servers/report?ids=<public_id,...>&period_days=14&view=engineer`
- 진입점: 대시보드 list 페이지에서 N대 선택 → "엔지니어 보고서 (N)" 버튼
- 산출물 형태: HTML SSR. 브라우저 인쇄로 PDF 캡처 가능하지만 16 컬럼이라 가로 폭 한계 — 운영자가 화면에서 분석하는 용도 우선.

## 존재 의의

엔지니어 보고서는 운영자·엔지니어가 환경 안 모든 서버의 정량 지표를 한 표에서 비교 분석하기 위한 산출물. 다음 3개 질문에 답한다.

질문 1: "환경 안 어느 서버가 어떤 부하 특성을 보이는가?"

16 컬럼 정량 표로 CPU·메모리·load·I/O wait·디스크 I/O·네트워크 I/O·swap·재부팅·분류를 한눈에 비교. row 단위로 정렬(브라우저 단)·복사·외부 분석 도구 입력 가능.

질문 2: "Right-sizing 결정의 근거를 어디서 확인하나?"

판단 컬럼 + 분류 컬럼이 USE Method 임계값 기반 자동 판단을 노출. 운영자가 "왜 이 서버가 under_provisioned인가"를 같은 행의 CPU p95·메모리 p95·swap·variance에서 즉시 검증. 별도 detail 페이지를 거치지 않고 보고서 한 장에서 right-sizing 의사결정 시그널 확인.

질문 3: "환경 단위 패턴은 무엇인가?"

표 위 자동 정성 요약(summary_bullets)이 환경 단위 시그널 노출 — 고위험·주의 카운트·디스크 임박·I/O 병목·역할별 평균 CPU 최고치·Saturation 발생·CPU 변동성 큼. 16 컬럼 raw 표 분석 전에 어디부터 볼지 가이드.

## 산출 정보

상단 (양식 A와 동일):
- KPI 6개: 대상 서버 / 온라인 / 주의 필요 / 고위험 / 평균 CPU p95 / 평균 메모리 p95
- 환경 총 자원: vCPU / 메모리 / 디스크
- 역할 분포 badge

자동 정성 요약 (양식 A 시그널 + 엔지니어 시그널):
- 고객 시그널: 고위험·주의·디스크 임박·I/O 병목·재부팅·OS EOL
- 엔지니어 추가: 역할별 평균 CPU 최고치·Saturation 발생·CPU 변동성 큼

메인 표 16 컬럼:

| # | 컬럼 | 표시 | source |
|---|------|------|--------|
| 1 | SERVER / internal IP | hostname + private IP | `server_inventory` |
| 2 | ROLE | service_classifier 카테고리 | `service_classifier` |
| 3 | OS / KERNEL | os_id·version + kernel_version | `server_inventory` |
| 4 | CPU | p95 · peak (%) | `report_aggregate` |
| 5 | MEM | p95 · peak (%) | `report_aggregate` |
| 6 | LOAD | 15m max · saturation (load/cores) | `report_aggregate` |
| 7 | 변동성 | cpu · mem (peak/p95) | `report_aggregate` |
| 8 | I/O wait | p95 · peak (%) | `report_aggregate` |
| 9 | DISK IOPS | avg · p95 · peak | `report_disk_io_baseline` |
| 10 | DISK KB/s | avg · p95 · peak | `report_disk_io_baseline` |
| 11 | NET RX | avg · p95 · peak (kB/s) | `report_net_io_baseline` |
| 12 | NET TX | avg · p95 · peak (kB/s) | `report_net_io_baseline` |
| 13 | SWAP / Mount | swap 사용 여부 + worst mount(경로·사용률·잔여일) | `report_aggregate` + `report_mount_worst` |
| 14 | Uptime / 재부팅 | uptime_days + reboot_count (period 안) | `report_uptime_stats` |
| 15 | 분류 / 판단 | USE Method classification + 자동 판단 텍스트 | `recommendation.classify` + mapper `_build_diagnosis` |
| 16 | 진단 (14일) | 진단 워커 latest succeeded 결과 | `diagnostic_jobs` (ADR 0004 + 0010) |

지표 정의 source note + 판단 컬럼 평가 순서 + 컬럼 15/16 차이 설명.

평가 윈도우: 14일 default (`recommendation.WINDOW_DAYS`). URL `?period_days=N`으로 override 가능.

## 지표 정의·임계값 근거

| 지표 | 정의 | 임계값 의미 | 출처 |
|------|------|-----------|------|
| p95 | `percentile_cont(0.95)` over period | 정상 부하의 상한선 — 일시 spike 제외 | 운영 통념 + AWS Compute Optimizer |
| peak | 시점별 최댓값 | sizing 시 considered worst case | 운영 통념 |
| CPU% | jiffies delta. boot_time 변경 시 reset 제외 | counter reset 정밀 식별 (`docs/architecture/db/timescaledb.md`) | /proc/stat 표준 |
| MEM% | (1 - available/total) * 100 | available 우선 (cgroup·page cache 보정) | Linux `/proc/meminfo` MemAvailable 권장 |
| Saturation | load_15m_max / vCPU | >= 1.0이면 큐 대기 발생 (큐잉 이론) | Kleinrock - Queueing Systems (1975) |
| 변동성 (variance) | peak / p95 | >= 1.5이면 burst 큼 — 평균보다 peak 기준 sizing 권장 | 본 프로젝트 휴리스틱 |
| DISK I/O | (서버, 시점) device 합산 rate | iops·throughput baseline | `/proc/diskstats` |
| NET I/O | interface 합산 rate | rx·tx baseline | `/proc/net/dev` |

판단 컬럼 평가 순서:
1. swap 사용 — 메모리 부족 신호 (스왑 발생 자체가 위험)
2. 디스크 I/O 병목 (iowait p95 >= 20%)
3. CPU saturation (load > cores)
4. 자원 압박 (cpu/mem p95 임계 초과)
5. 변동성 큼 (peak/p95 >= 1.5)
6. 미사용 (거의 0%)
7. 여유 (다운사이즈 검토)

최상위 신호 1개만 노출 — 엔지니어가 가장 시급한 문제를 즉시 식별.

분류 컬럼 vs 진단 컬럼 차이:
- "분류 / 판단" — 본 보고서 윈도우(period_days) raw 데이터 기반 즉시 분류. URL 파라미터 따라 윈도우 가변.
- "진단 (14일)" — 별도 진단 job 결과 (스케줄러 매일 03시 또는 사용자 발행). 14일 고정. 다른 시점에 발행된 job의 결과라 stale 가능.

같은 분류 이름(under_provisioned 등)을 쓸 수 있지만 source·시점 다름 — 두 컬럼이 다르게 보이면 윈도우 차이·진단 job 갱신 지연이 원인.

## 양식 A vs 양식 B 분기 의도

| 항목 | 양식 A (고객) | 양식 B (엔지니어) |
|------|-------------|-----------------|
| 목적 | 고객 의사결정 한 장 요약 | 환경 정량 분석 + Right-sizing 근거 |
| 컬럼 수 | 8 (SERVER·ROLE·OS·CPU p95·MEM p95·위험도·상태·진단) | 16 (위 + LOAD·변동성·I/O wait·DISK·NET·SWAP/Mount·Uptime/재부팅·판단) |
| 정성 요약 | 행동 시그널 (고위험·주의·디스크·I/O·재부팅·OS EOL) | 위 + 엔지니어 시그널 (역할별 평균·Saturation·CPU 변동성) |
| 위험도 표시 | 3단계 압축 (high/attention/normal) | 5분류 그대로 + 판단 텍스트 |
| Print 우선 | 인쇄 PDF 대응 | 화면 분석 우선 (인쇄 가능하지만 가로 폭 한계) |

분기 메커니즘:
- 같은 endpoint·SQL·템플릿. `view` 파라미터로 `{% if view == "customer" %} ... {% elif view == "engineer" %} ... {% endif %}` 블록 토글.
- service `get_report(view=view)` → mapper `build_report_summary_bullets(view=view)` view 전달 (engineer 추가 시그널 활성).

## 한계

1. 인쇄 폭 한계 — 16 컬럼이라 A4 가로도 빠듯. 폰트 작아짐. PDF 대응이 안 됨. 화면 분석 또는 가로 모드 인쇄 권장.
2. 표 정렬·필터 미지원 — 브라우저 row 단위 정렬 안 됨. 운영자가 brower DevTools 또는 CSV export 같이 별도 도구 필요. 추후 client-side sort 도입 검토 후보.
3. 시점 동기화 없음 — 표 row의 "분류 / 판단"(이번 윈도우 raw)과 "진단 (14일)"(별도 job)이 다른 시점·다른 윈도우. 운영자가 두 컬럼 차이를 source 차이로 해석해야 함 (source note에 명시).
4. 워크로드 역할 무관 임계 — DB·캐시·앱서버 모두 같은 70%/80% 임계. 역할별 임계 분기는 향후 별도 ADR.
5. 변동성·Saturation 등 휴리스틱 임계 — 1.5·1.0 같은 cutoff는 본 프로젝트 휴리스틱. 학술 검증된 절대 기준이 아님. 운영 경험 따라 조정 가능.

## 관련 문서·코드

- `docs/architecture/web/routers.md` — `pages.py` 보고서 라우터·view 분기
- `docs/architecture/web/services.md` "Recommendation 분류" — USE Method 임계값 출처
- `docs/architecture/db/timescaledb.md` `_chart_*` 패턴 — counter reset 정밀 식별
- `src/assessment_engine/recommendation.py` — 분류 임계값 상수 카탈로그
- `src/assessment_engine/web/services/query_service.py::get_report` — 5 SQL round-trip + view 분기
- `src/assessment_engine/web/services/mappers.py::build_report_summary_bullets` — view 분기 시그널
- `src/assessment_engine/web/services/mappers.py::_build_diagnosis` — 판단 컬럼 우선순위 평가
- `src/assessment_engine/web/templates/servers/report.html` — 양식 A·B 분기 템플릿
- ADR 0004 + 0010 — 진단 워커 인프라 + 규칙 기반 한정 (진단 컬럼 source)
- `docs/products/customer-report.md` — 양식 A 보고서 ref
- `docs/products/environment-diagnostic.md` — 진단 컬럼의 data source
