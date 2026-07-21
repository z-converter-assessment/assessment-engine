# 서버 보고서 (Server Report)

본 문서는 서버 단위 (scope=server) 산출물의 존재 의의·구현 의도·근거를 정리한다. 운영자가 선택한 N대 또는 단일 서버에 대해 row 단위 상세·자원 적정성 판단을 받는 산출물.

환경 단위 산출물(scope=environment, 분포 카운트·high-level KPI)은 `docs/explanation/products/environment-report.md` 별도.

## 산출물

서버 보고서 (선택 N대) — `GET /reports/servers?ids=<public_id,...>&period_days=14&view=customer|engineer`, 발행 `POST /reports/servers/emit`. 단일 1대는 `GET /servers/{id}/report`. 선택 N대 row 단위 상세. customer(양식 A)·engineer(양식 B) 세부 서버 목록은 동일 컬럼 세트(`detail_server_list` 단일 진실) — engineer 는 재부팅·에이전트 재시작 2칼럼 추가.

발행 흐름 (T13): 보고서 발행(`POST /reports/servers/emit`)은 parent job 을 pending enqueue 후 즉시 `?job={id}` 반환 — 전용 워커 프로세스가 발행 시점 ViewModel 정적 스냅샷을 생성해 `diagnostic_jobs` `result` JSONB 에 보존하고 succeeded 전이 (customer/engineer 동일). GET `?job={id}` 는 succeeded 면 저장된 스냅샷 정적 렌더(재계산 0), 생성 중이면 진행 화면 + 폴링. 보고서 이력은 `/reports/history`.

## 위치

- UI 진입점: 대시보드 list 페이지에서 N대 선택 → "고객 보고서 (N)" / "엔지니어 보고서 (N)" 버튼. 단건은 server detail 페이지에서.
- 발행 경로: 운영자 즉시 호출 (HTTP GET). 발행 시점 즉시 5 SQL round-trip + render
- 산출물 형태: HTML SSR. 브라우저 인쇄로 PDF/PPT 캡처 (백엔드 PDF export 미도입)

## 존재 의의

운영자가 단일 서버 또는 N대 batch 에 대한 정량 분석·자원 적정성 판단을 받기 위한 산출물. 다음 질문에 답한다.

질문 1: "이 N대, 어떤 부하 특성을 보이는가?"

세부 서버 목록 표로 상태·구동 서비스·OS·OS지원종료·자원(vCPU·MEM·DISK)·운영 이벤트·프로비저닝(자원 적정성 분류)을 행 단위로 한눈에 비교(engineer 는 재부팅·재시작 추가). 정량 근거(CPU/메모리 p95·포화·변동성)는 자원 적정성 평가 표·심화 카드에서. row 단위로 정렬·복사·외부 분석 도구 입력 가능.

질문 2: "이 서버 한 대, 자원 배분이 적절한가?"

단일 서버 보고서(`/servers/{id}/report`)가 USE Method 분류·권장 action 을 노출. 운영자가 보고서 한 장에서 즉시 판단 가능 — 환경 분포 비교 불필요.

질문 3: "환경 분포에서 'under_provisioned 5대' 를 봤다 — 그 5대가 누구인가?"

환경 단위 산출물은 분포 카운트만 — 개별 식별 안 됨. 본 서버 단위 batch 발행으로 어떤 서버가 어떤 분류인지 행 단위 확인. 환경 단위 산출물의 행동 follow-up.

질문 4: "자원 적정성 결정의 근거를 어디서 확인하나?"

engineer view 의 진단·분류 칼럼이 USE Method 임계값 기반 자동 해석 노출. 운영자가 "왜 이 서버가 under_provisioned 인가" 를 자원 적정성 평가 표의 근본원인·CPU p95·메모리 p95·포화 축에서 즉시 검증. 별도 detail 페이지 없이 보고서 한 장에서 자원 적정성 의사결정 시그널 확인.

## 산출 정보

### 서버 보고서 — 두 view 공통 상단

KPI 6개 + 환경 총 자원 + 선택 맥락 (선택 N대의 OS 구성·워크로드 한 줄 요약 — "이 묶음이 무엇인지", P-A 구성 계층. `build_selection_context`). 비교 표는 위험 우선 정렬 (`sort_rows_for_report` — under -> attention -> normal).

### view 분기 — customer (양식 A)

목적: 컨설턴트가 고객 미팅·내부 보고에 들고 가는 N대 자원 요약.

구성 = 환경 보고서 본문 공유(customer 분기 — 요약·환경 구성·서비스 구성·환경 요약·자원 적정성 평가(분류 분포·효율화·조치 필요 호스트)·OS 지원 종료, 단일 진실 `docs/explanation/products/environment-report.md`) + 세부 서버 목록 표.

세부 서버 목록 컬럼(customer): 상태 · 서버 · 구동 서비스(시그니처 워크로드만, `signature_workload_categories` — 서버 목록 뱃지와 동일 기준) · OS · OS 지원종료(4상태: 지원종료·연장지원·지원중·미상, 보고서 발행 기준 시각 고정) · 자원(vCPU·MEM·DISK) · 운영 이벤트(보고서 window 내 OOM·MCE·메모리손상·net/disk 에러 발생 유무) · 프로비저닝 · 개별 보고서 링크 (`_shared.html` `detail_server_list` 단일 진실, 환경·선택 공유). CPU/MEM 평균·디스크 최대 칼럼은 지엽적 원시 수치라 제외 — 자원 적정성 분류(프로비저닝 칼럼)가 그 판정 결론.

자동 정성 요약 (행동 시그널): 디스크 임박·I/O 병목·재부팅·OS EOL.

판단 근거(임계값 전문)는 인쇄본에 임베드하지 않는다 — 보고서 하단은 화면 전용 경량 링크(`_reference_link.html`)만 두고, 임계값 전문은 사이드바 "참고" 그룹의 별도 페이지(`/reference`)에서 확인한다(보고서 본문 인쇄 분량 절약).

### view 분기 — engineer (양식 B)

목적: 운영자·엔지니어 정량 분석 + 자원 적정성 근거 검증.

구성 = 환경 보고서 본문 공유(engineer 분기 — 환경 현황 5축 메트릭·부하 추이+토폴로지·자원 적정성 평가(분류 분포 + 서버별 자원 적정성 통합 표)·OS 지원 종료·OS 버전 분포, 단일 진실 `docs/explanation/products/environment-report.md`) + 세부 서버 목록 표.

세부 서버 목록 컬럼(engineer): customer 컬럼 + 재부팅 · 에이전트 재시작 (시스템 안정성 — anchor+window 안 카운트).

인쇄 2분할도 이 컬럼 세트에 맞춰 표A(구성: 상태·서버·구동서비스·OS·OS지원종료·자원)/표B(평가: 서버·운영이벤트·프로비저닝{engineer 는 +재부팅·재시작})로 재편.

서버별 자원 적정성 표(본문 공유, `action_targets_table`): 호스트·사양·분류(근본원인 병합)·권고(자원별 독립 처방)·네트워크 상태·디스크 I/O 상태·신뢰도. 환경 자원 평가 페이지와 칼럼 동일(#F9 정합). 단일 보고서 자원 적정성 평가 표와 동일 판독 프레임.

자동 정성 요약 (customer 시그널 + engineer 추가): 역할별 평균 CPU 최고치·Saturation 발생·CPU 변동성 큼(peak/p95 1.5배+).

### 개별 서버 보고서 — 서버 인벤토리 (구성 계층)

단일 서버 보고서(`/servers/{id}/report`)는 "이 서버가 무엇인가"를 좌우 2열 카드로 노출 — 자원 적정성 평가 앞에 배치.

- 좌열: vCPU·메모리·디스크 요약 카드 + `<dl>` 식별·구성 정보(OS·Kernel·CPU·Swap·내부/외부 IP·Boot Time·Agent Started·Last Inventory, engineer 는 +Agent ID·Composite ID). `ServerDetail`(`build_server_inventory`) 뿐 아니라 보고서 자체가 조회한 `ReportRowRaw`(재현 필드 — CPU arch/bits·boot firmware·Secure Boot·OS edition·timezone)도 결합해 서버 세부·자원 세부 각 페이지가 따로 보여주는 인벤토리 정보를 한 카드에 종합.
- 우열: 서비스 요약 — 워크로드 카테고리별 제품명 묶음(뱃지, 예: "web: nginx, gunicorn") + (customer 전용) 주요 메트릭(CPU/메모리 평균·디스크) 컴팩트 표.
- Listen 포트 카드(engineer 전용, 자원 적정성 카드 다음) — listen 소켓 원시 표(proto·addr·port·uid·pid·process). 카테고리 분류는 서비스 요약이 이미 담당이라 중복 없이 원시 사실만.
- 데이터: `ReportRowRaw.listen_ports` (보고서 집계 SQL 유입) -> mapper `_build_workload_display` (service_classifier 단일 진실, listen-only 카테고리 `detect_listen_categories` 보강).

### 개별 서버 보고서 — engineer 심화 계층 (단일 deep-dive)

N대 selection 은 서버 간 비교를 위해 행 단위 정량 표(양식 B)로 압축하지만, 단일 1대(`view=engineer`)는 비교 대상이 없어 그 1대를 카드 계층으로 펼친다 — 서버 인벤토리 -> 자원 적정성·운영 평가(통합 1표) -> Listen 포트 -> CPU/메모리/스토리지/네트워크 상세(이용률+포화축+마운트·인터페이스 세부) -> 에러 신호 -> 이용률 추이 + 포화 여부 추이(2열, engineer 전용 — 이용률은 CPU·메모리·디스크 사용률 연속선, 포화 여부는 CPU 실행 큐·메모리 페이징·디스크 I/O 3축 이진 0/1 상태를 lane 오프셋으로 나란히) 순(customer 는 서버 인벤토리 뒤 곧장 사용률 심화 카드들, 자원 적정성·운영 평가 표는 맨 뒤 — 순서만 다르고 "자원 적정성·운영 평가" 표 자체는 동일 패턴 공유, 컬럼 수만 차등). CPU 분류(user/system/iowait)·메모리 구성(used/available/cached/buffers)은 N대 표엔 없는 단일 전용 — repo `report_cpu_breakdown`·`report_memory_breakdown`(개별 server_id 단위). customer 단일은 이 심화를 생략하고 구성·평균 사용률·권고만(현황 파악 범위). 양식 통일상 단일·selection·환경 모두 `EnvironmentReportSummary`(kind=`env_report`) 공유 — 단일 전용 필드(`server_inventory`·`memory_breakdown`·`cpu_breakdown`·`period_assessment`·`storage_tree`·`network_interfaces`)는 selection·환경에서 None/빈 list (#C1).

자원 적정성·운영 평가 표(customer·engineer 공용 패턴, 컬럼 수만 차등) — 분류·진단(engineer)/근본원인(customer)·권고·신뢰도 + 시스템 에러(윈도우 내 OOM·MCE·메모리손상·net/disk 에러 발생 유무)·네트워크 상태(사이징과 별개 품질 판정)·OS 지원종료(4상태). engineer 만 재부팅·에이전트 재시작(윈도우 카운트) 2칼럼 추가. 세부 서버 목록(N대)의 동명 신호와 같은 산식 공유 — 화면 간 정합.

CPU/메모리/스토리지/네트워크 상세 카드(engineer 전용) — 윈도우 평균·p95·peak 정량 표 아래 이용률·포화 축 2열(`period_assessment.resources[cpu|mem|disk|net]`, 서버 세부·자원 세부 탭과 동일 신호·임계·판정 단일 진실 `build_period_assessment`, 네트워크는 포화 열만). 스토리지 카드는 마운트별 표 대신 스토리지 레이아웃 트리(`storage_tree`, `_storage_tree.html` 단일 진실)로 RAID·LVM·파티션 계층과 마운트별 사용률·inode율을 한 번에 노출 — 트리가 마운트 표를 상위호환하므로 마운트 표는 별도로 두지 않는다. 네트워크 카드는 정적 인터페이스 구성(MAC·Speed·MTU·Gateway·주소, `network_interfaces`)도 함께 노출.

에러 신호 카드(engineer 전용) — 서버 세부 페이지와 동일 배지(`period_assessment.error_rows`, 전 자원 통합 MCE·OOM·EDAC·디스크·네트워크 에러 배지).

### 자원 적정성 평가 — 서버 1대당 산출

| 항목 | 내용 | source |
|------|------|--------|
| 평가 윈도우 | 서버 보고서 default 14일, URL `?time_range=`(15m~30d) override | `recommendation.WINDOW_DAYS` 또는 `DIAGNOSTIC_DEFAULT_TIME_RANGE` |
| Anchor 시점 | 현재 또는 발행 시점 | default now |
| 분류(배지) | under_provisioned / over_provisioned / idle / optimal / insufficient_data | `classify_host`(배지) + `rollup_host`(근본원인) |
| 권장 action | 자원별 독립 한국어 처방 (증설 검토·축소 검토·종료·통합 검토·적정 유지·표본 부족) | `under_prescription`/`recommend_action` -> `RECOMMENDATION_ACTION_KO` |
| 정성 요약 | "서버 {hostname}는 최근 {window} 동안 CPU p95 {%}, 메모리 p95 {%} 사용 ..." | 결정론 템플릿 합성 |

산출 결과 예시:
```
서버 db-01는 최근 7일 동안 CPU p95 12.3%, 메모리 p95 35.0% 사용.
분류는 과다 할당.
AWS Balanced 사이징 목표(이용률 70% 착지) 기준 현재보다 적은 코어로 충분 — CPU 축소 검토 권장.
```

## 의사결정 근거

### 분류 임계값·판정

5분류·트리거 조건·임계 상수·벤더 출처 상세는 `docs/reference/right-sizing.md` 4절, 운영자 카탈로그는 `right_sizing_thresholds.html`. host_status 판정 순서 = under -> insufficient -> idle -> over -> optimal (`rollup_host`/`classify_host`, 상세 right-sizing.md 3절, 임계 상수는 `recommendation.py`).

Windows (원칙 P2/P4): 포화 3축 모두 perflib 실측 — CPU=Processor Queue Length(`cpu_saturated` os-aware run queue), 메모리=Pages Input/sec p95 >= 20(하드 read 폴트, Linux swap page-out 대응 — 정적 pagefile 점유는 신호 아님), 디스크 I/O=await(IOCTL ReadTime/WriteTime, 구세대 viostor 미부착 시 큐 깊이 폴백). perflib 미부착 축만 coverage_gap -> "포화 수치 미관측" 마커. 상세 `docs/reference/right-sizing.md` 5절.

### 지표 정의 (engineer view)

engineer view 는 p95·peak·CPU%·MEM%·Saturation·변동성(peak/p95)·DISK/NET I/O baseline 로 근거를 노출한다. 각 지표 정의·임계·출처는 운영자 카탈로그 `_metric_definitions.html`·`_thresholds_reference.html`("엔지니어 보조 지표") 단일 진실.

### 진단 칼럼 (engineer view)

진단 라벨은 `report.py::_build_diagnosis` 단일 진실 — 최상위 신호 1개만 노출(엔지니어가 가장 시급한 문제 즉시 식별). 우선순위(메모리 포화(스왑/페이징) -> disk I/O -> CPU 포화 -> mem -> cpu -> 디스크 용량 -> 네트워크 혼잡 -> burst -> 미사용 -> 여유 -> 정상)와 임계는 `right_sizing_thresholds.html` "진단 칼럼 해석" 단일 진실. 예외: 표본 부족 호스트는 원인 진단(오프라인 / 누락 메트릭 / 윈도우 내 표본 부족), 오프라인 호스트는 "오프라인" 접두(분류는 윈도우 측정 기반 유지).

### 평가 윈도우

- 서버 보고서 default 14일 (`recommendation.WINDOW_DAYS`). URL `?time_range=`(15m·1h·6h·24h·7d·14d·30d) override 가능. 짧은 윈도우는 단발 부하·실시간 시연 검증, 긴 윈도우는 신뢰성 증가 최근 변동 반영 늦음.

### view 분기 의도

| 항목 | customer (양식 A) | engineer (양식 B) |
|------|-------------------|-------------------|
| 목적 | 고객 의사결정 한 장 요약 | 정량 분석 + 자원 적정성 근거 |
| 세부 목록 컬럼 | 상태·서버·구동서비스·OS·OS지원종료·자원·운영이벤트·프로비저닝·링크 | 위 + 재부팅·에이전트 재시작 |
| 정성 요약 | 행동 시그널 (고위험·주의·디스크·I/O·재부팅·OS EOL) | 위 + 엔지니어 시그널 (역할별 평균·Saturation·CPU 변동성) |
| 위험도 표시 | 3단계 압축 (high/attention/normal) | 5분류 그대로 + 판단 텍스트 |
| Print 우선 | 인쇄 PDF 대응 | 화면 분석 우선 |

분기 메커니즘: 같은 endpoint·SQL·템플릿. `view` 파라미터로 Jinja2 if 블록 토글. service `get_report(view=view)` → mapper `build_report_summary_bullets(view=view)`.

### 분류 / 판단 컬럼 (engineer view)

"분류 / 판단" — 본 보고서 윈도우 (period_days) raw 데이터 기반 즉시 분류. URL 파라미터 따라 윈도우 가변. 발행 시점 스냅샷이라 발행된 보고서는 그 시점 분류를 정적 보존.

## 환경 단위 산출물과의 분기

| 항목 | 환경 (`environment-report.md`) | 서버 (본 문서) |
|------|-------------------------------|----------------|
| 발행 단위 | 환경 전체 1건 | 1대 또는 N대 batch (각 1건씩) |
| 보고서 라우터 | `/reports/environment` | `/reports/servers?ids=...` (단일 1대 `/servers/{id}/report`) |
| scope | environment | server |
| 산출물 | 분류 분포 카운트 + 우선순위 권장 | 개별 서버 분류·action |
| 답 | "환경 안 over-provisioned 5대 있음" | "이 서버는 under_provisioned, 업사이즈 검토" |
| 운영 단계 | 1단계 — 환경 전체 현황 한눈 | 2단계 — 개별 서버 판단 |

운영자 표준 흐름: 환경 단위로 분포 확인 → 시급한 카테고리의 서버 list 식별 → 서버 단위 batch 로 개별 판단 → detail 화면에서 검증.

## 한계

1. 분류 라벨 어휘가 운영자에게 항상 직관적이지 않음 — "over_provisioned"·"under_provisioned" 의미는 명시적 가이드 (`recommendation.py` 상수) 에 의존. 한국어 라벨이 한국어 사용자에게 더 명확하지만 영어 분류 식별자는 코드·메시지에 박힘.
2. 워크로드 역할 무관 임계 — DB·캐시·앱서버 모두 같은 70%/80% 임계. DB 는 메모리 압박이 정상 운영일 수 있는데도 under_provisioned 로 잡힐 가능성. 역할별 정밀 분기는 향후 별도 결정.
3. anchor 임의 선택 가능 — 운영자가 특정 시점 (부하 spike 발생 직후 등) anchor 로 잡으면 분류가 그 윈도우 한정. 표준 14d default 외 사용 시 운영자가 의도 인지 의무.
4. 정성 요약의 표현 한정 — 결정론 템플릿이라 운영자가 추가 컨텍스트 반영 불가.
5. engineer view 인쇄 폭 한계 — 16 컬럼이라 A4 가로도 빠듯. PDF 대응 안 됨. 화면 분석 또는 가로 모드 인쇄 권장.
6. 표는 위험 우선 기본 정렬 (발행 시점 under -> attention -> normal, 동순위 cpu_p95 DESC). 사용자 임의 재정렬·필터는 미지원 — 추후 client-side sort 도입 검토 후보.
7. URL 길이 한계 — `ids` query string 에 N개 public_id 넣음. N 이 매우 크면 URL 한계. 추후 POST + session 도입 검토.

## 관련 문서·코드

- 진단 규칙 기반 한정 결정 기록: `docs/decisions/adr/`
- `docs/reference/web/routers.md` — `routers/pages/` 패키지(`report_page.py`) 보고서 라우터·view 분기
- `docs/reference/web/services.md` "Recommendation 분류" — USE Method 임계값 출처
- `docs/reference/db/timescaledb.md` `_chart_*` 패턴 — counter reset 정밀 식별
- `docs/reference/web/static-assets.md` "report.html print CSS" — 인쇄 색 처리
- `docs/explanation/tradeoffs.md` T13 — 보고서 = diagnostic_jobs 스냅샷 보존
- `src/assessment_engine/recommendation.py` — 분류 임계값 상수 카탈로그
- `src/assessment_engine/web/services/query_service.py::get_report` — 5 SQL round-trip + view 분기
- `src/assessment_engine/web/services/mappers/report.py::build_report_summary_bullets` — view 분기 시그널
- `src/assessment_engine/web/services/mappers/report.py::_build_diagnosis` — 진단 칼럼 우선순위 평가
- `src/assessment_engine/web/templates/servers/report.html` — 양식 A·B 분기 템플릿
- `docs/explanation/products/environment-report.md` — 환경 단위 산출물 (cross-reference)
