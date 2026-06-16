# 환경 보고서 (Environment Report)

본 문서는 환경 단위 (scope=environment) 산출물의 존재 의의·구현 의도·근거를 정리한다. 환경 안 모든 등록 서버를 묶어 KPI·분류 분포를 한 화면에 합성.

서버 단위 산출물(scope=server, 선택 N대 row 단위 상세) 은 `docs/products/server-report.md` 별도.

## 산출물

환경 scope 환경 보고서 — `GET /reports/environment?view=customer|engineer&time_range=14d`. 환경 전체 KPI·자원 합계·분류 분포 high-level 한 장. customer(양식 A) vs engineer(양식 B) view 분기.

발행 흐름:
- 발행(`POST /reports/environment/emit`)을 눌러야 스냅샷 생성 + 본문 표시 + 이력 추가. 발행 전 GET 은 컨트롤(보고서 양식·윈도우·앵커 select + 발행 버튼)만 노출, live preview 본문 없음. 발행된 스냅샷은 `GET /reports/environment?job={id}` 정적 렌더 (서버 scope `/reports/servers` 는 발행 전에도 live preview 본문 유지 — 환경 보고서만 컨트롤-only).
- 발행 시점 SQL 집계 + 스냅샷을 `diagnostic_jobs` 테이블 row 의 `result` JSONB 에 정적 보존 (#C1).
- 이력 표시: 보고서 이력 `/reports/history` (customer + engineer union, view 필터).

## 위치

- UI 진입점: 홈/네비 "환경 보고서" 또는 `/reports/environment?view=customer|engineer` 직접 호출 (컨트롤 노출, 발행 후 본문)
- 발행 경로: 운영자가 양식·윈도우·앵커 선택 후 발행(POST emit) — 발행 시점 SQL 집계 + 스냅샷 INSERT + render. 발행 전 GET 은 컨트롤만
- 산출물 형태: HTML SSR. 브라우저 인쇄로 PDF/PPT 캡처 (백엔드 PDF export 미도입 — `docs/tradeoffs.md` T 참조)

## 존재 의의

운영자·고객이 다음 질문에 한눈에 답하기 위한 산출물.

질문 1: "지금 우리 환경, 자원 배분이 적절한가?"

수십·수백 대 서버를 가진 환경에서 개별 서버 상세를 다 확인하지 않고도 환경 전체의 자원 배분 상태(과다·부족·정상)를 분포로 본다. 서버 단위 detail은 너무 많고, 카드 한 장으로는 환경 전반을 못 본다. 그 사이를 메우는 산출물이 환경 보고서.

질문 2: "다음에 어디부터 손대야 하는가?"

분포에서 가장 시급한 카테고리(보통 under_provisioned 위험 또는 over_provisioned 비용)를 우선 검토 대상으로 명시. 운영자가 "오늘은 over-provisioned 5대 다운사이즈 검토"처럼 다음 단계 행동을 결정. 그 다음 단계는 서버 단위 산출물 (`docs/products/server-report.md`) 로 개별 서버 후보 식별.

질문 3: "고객사·내부 보고 시 자원 현황을 어떻게 요약하는가?"

고객 미팅·내부 정기 보고에서 환경 자원 현황을 한 줄로 표현 가능 — "7일 평가 기준 평가 가능 23대 중 over-provisioned 5대·under-provisioned 2대·optimal 16대, 우선 검토는 over-provisioned 다운사이즈". customer view 보고서는 한 장 KPI·자동 요약, engineer view 는 정량 분석 추가.

## 산출 정보

### 환경 보고서 — 두 view 공통 상단

| 영역 | 내용 | 데이터 source |
|------|------|--------------|
| KPI 6개 | 대상 서버 / 온라인 / 주의 필요 / 고위험 / 평균 CPU p95 / 평균 메모리 p95 | service KPI 집계 (time_range 윈도우) |
| 환경 구성 + 서비스 구성 (한 카드 2열) | 환경 구성(OS family Windows/Linux 분포) + 서비스 구성(카테고리별 칩 "카테고리명 + 서비스명·개수", 전 카테고리 노출 count 0 포함 #E9, "분류 미상 서버 N대" 표기). engineer 호스트 전수 나열 없음 | `overview.os_distribution` / 워크로드 카테고리 칩 |
| 환경 총 자원 | 총 vCPU / 메모리 / 디스크 | inventory 합산 |
| 분류 분포 | 자원 적정성 6분류 카운트 막대 (한국어 분류명 LABEL_KO, 영어 enum 미노출) | `recommendation.assess` |
| 환경 부하 추이 (시계열) | CPU·메모리·디스크 평균 추이 차트. 보고서=발행 윈도우 정적 스냅샷 | `metric_trend` |
| 네트워크 토폴로지 (engineer) | 정적 서브넷 요약 표 (서브넷 대역·호스트 수). 인터랙티브 Cytoscape 그래프는 화면 토폴로지 페이지(`/environment/topology`) 전용. OS(linux/windows)로만 구분 — 멀티홈 색 구분 없음 | `build_network_topology` (subnet 집계) |

### view 분기 — customer (양식 A)

목적: 컨설턴트가 고객 미팅·내부 보고에 들고 가는 한 장짜리 환경 자원 요약.

- 분류 어휘 = 자원 적정성 한국어 분류명(LABEL_KO) 단일 — 요약·분포·조치 표 동일, 영어 enum·평행 어휘 없음.
- 환경 요약: 인벤토리(등록 서버·총 vCPU/메모리/디스크) + 메트릭(CPU/메모리/디스크 평균) + OS 구성(Linux/Windows, 0대 포함 #E9) metric-card 소제목 — 카드는 `.env-stat-card` 너비·높이 통일. 서비스 구성은 별도 카드("서비스 식별 (N대)"·"서비스 미식별 (M대)" 소제목). engineer 환경 현황과 동일 구조.
- 자원 적정성 평가: 분류 분포(조치 방향) + 효율화 검토 대상(과다·유휴·종료 자원 합) + 조치 필요 호스트(자원 부족, high 만). 평가 커버리지(평가 대상/전체) 명시.
- 운영 신호: OS 지원 종료 카드만 (2축 정책, 디스크 capacity 는 자원 적정성 평가가 흡수).
- 정성 요약: 분류 분포 + 우선 조치/효율화 여지 (결정론 템플릿 합성).
- 발화 항목은 제목 + placeholder (데이터 0 이어도 노출, #E9).
- Print 우선 — 참고자료 전문 인쇄 임베드.

### view 분기 — engineer (양식 B)

목적: 운영자·엔지니어가 환경 단위 정량 패턴 분석 + 자원 적정성 근거 검증. customer 와 동일 어휘(LABEL_KO) + 정량 상세.

- 요약: customer 와 동일 (view 무관 단일 `_env_summary_bullets`) — 등록 서버(+vCPU/메모리/디스크) / 온라인·오프라인 / 분류 분포 / 자원 부족(원인별) / OS 지원 종료.
- 환경 현황 카드: 인벤토리(등록 서버·총 vCPU/메모리/디스크) / 메트릭 / OS 구성 소제목. 메트릭 = metric-card 5축(CPU·메모리·디스크·네트워크·디스크 I/O) — 실시간 '현재 자원 현황' 축과 동기, 값은 전부 보고서 윈도우 통계(CPU/메모리/디스크 = capacity-weighted avg+p95, 네트워크/디스크 I/O = per-server 윈도우 baseline 합, 단위 표기 `format_net_rate` 실시간 공용 단일 진실). 디스크 p95 는 시점별 capacity 합이 Windows 디바이스(major/minor) 인식 불완전으로 신뢰 불가라 의도 제외(repo `environment_utilization` SQL 주석 단일 진실). 인벤토리/메트릭/OS 카드 `.env-stat-card` 높이 통일. 에이전트 버전은 보고서 헤더 메타.
- 환경 부하 추이(시계열 CPU/메모리/디스크) + 네트워크 토폴로지(정적 서브넷 요약 표) — 한 카드 2열.
- 자원 적정성 평가: 분류 분포(소제목 "분류 분포 (N대)") + 효율화 검토 대상(over/idle/shutdown 호스트 표 Top 30 — 호스트·분류·진단·신뢰도. 권고 칼럼 폐기 — 분류와 1:1) + 자원 부족(6축 메트릭 + 권고(`recommendation_action`) + 신뢰도). 조치 호스트 노출은 이 두 표가 단일 진실(전수 위험도 종합 표 없음).
- 세부 서버 목록: 환경 보고서는 미표시 (전수 인쇄 폭주 회피 — 조치 대상은 효율화/자원 부족 표가 담음). 선택 N대 보고서(selection)만 표시.
- 운영 신호 = OS 지원 종료만(2축 정책) — 보고서는 전수 표시(절단 없음, 대시보드 카드 한도와 분리). 재부팅·에이전트 재시작은 selection 세부 서버 목록 표에 표시.
- 화면 분석 우선 (인쇄 가능).

분기 메커니즘:
- 같은 endpoint·SQL·템플릿. `view` 파라미터로 `{% if view == "customer" %} ... {% elif view == "engineer" %} ... {% endif %}` 블록 토글.
- service `get_report(view=view)` → mapper `build_report_summary_bullets(view=view)` view 전달.

### 정성 요약 — 발행 시점 합성

발행 시점에 4 항목을 계산해 요약 문장으로 합성 (결정론 템플릿).

| 항목 | 내용 | source |
|------|------|--------|
| 평가 윈도우 | 7일 default (`recommendation.WINDOW_DAYS`) | Azure Advisor 단기 표준 (7일) — 14일·30일은 라우터 override |
| 평가 커버리지 | `evaluated_servers / total_servers` — 메트릭 데이터가 분류 가능한 정도로 누적된 서버 수 | DB 시계열 집계 |
| 분류 분포 | over_provisioned / under_provisioned / idle / optimal 각 카운트 | `recommendation.classify` |
| 우선 검토 권장 | 분포 중 가장 시급한 카테고리 1개 | 규칙 |

산출 결과 예시:
```
최근 7일 환경 평가 — 평가 대상 23대 (전체 25대). 분류 분포:
over-provisioned 5대, under-provisioned 2대, idle 0대, optimal 16대.
우선 검토 권장: over-provisioned 5대의 다운사이즈.
```

## 의사결정 근거

### 분류 임계값 출처

| 분류 | 트리거 조건 | 출처 |
|------|-----------|------|
| under_provisioned | 위험 신호 OR — CPU p95 >= 70 / 메모리 p95 >= 80 / swap 발생 / load >= cores / iowait p95 >= 20 / worst mount >= 85% | USE Method + Kleinrock 큐잉 |
| idle | CPU peak <= 1% + 네트워크 <= 1 kBps | AWS Compute Optimizer |
| shutdown | CPU p95 <= 3% + 네트워크 <= 2 Mbps | Azure Advisor "underutilized VM" |
| over_provisioned | CPU p95 <= 30% + 메모리 p95 <= 50% | AWS Compute Optimizer "over-provisioned" |
| optimal | 위 어디에도 해당 안 함 | residual |

판정 순서 = under -> idle -> shutdown -> insufficient_data -> over -> optimal (`recommendation.assess`). 임계 상수·근거 단일 진실은 `recommendation.py` + `docs/products/server-report.md` 분류 표.

Windows (원칙 P2): swap 트리거는 Linux 한정 — Windows pagefile 상시 사용은 saturation 아니라 분류에서 제외(swap_pressure 카운트·분포 도넛 모두). Windows는 utilization 축만으로 분류(부분 평가). 상세 `right_sizing_thresholds.html`.

분류 표시 (customer·engineer 공통): 자원 적정성 한국어 분류명(LABEL_KO) 단일. 내부 risk_level(high/attention/normal)은 조치 필요 호스트 선정·강조용으로만 쓰고, 화면 라벨로 노출하지 않는다 (영어 enum·평행 어휘 금지).

운영 신호 (2축 분리): 자원 적정성 평가(축1, 디스크 capacity·IO 포함)와 별개로 AttentionSignals 3종(통신 끊김·OS 지원 종료·에이전트 재시작)이 운영 신호 축. 보고서는 그중 OS 지원 종료만 카드로 표시(통신 끊김·에이전트 재시작은 윈도우 의미 불일치로 전역 카드 미표시 — 에이전트 재시작은 engineer 호스트 상세 컬럼).

### 평가 윈도우 7일

- Azure Advisor right-sizing 단기 권장 윈도우 (7일) — AWS Compute Optimizer(14일)는 라우터 override 로 지원
- 사용량 주기성 평탄화에 충분한 단기 구간 (7~14일 범위)
- 사용량의 일·주 단위 주기성(주중·주말) 평탄화에 충분
- 너무 짧으면(1~3일) 일시 부하·정기 백업을 평상 부하로 오인
- 너무 길면(30일+) 최근 도입된 워크로드 부하 반영 늦음

### 규칙 기반 한정

- 분류·권장은 결정론 임계값으로 충분. 자연어 요약은 결정론 템플릿으로 산출.
- 결정 근거: ADR 0010.

## 평가 커버리지의 의미

`total_servers` vs `evaluated_servers` 는 다른 수치다.
- `total_servers` — 인벤토리에 등록된 모든 활성 서버 수 (최근 N 시간 안에 에이전트가 살아 있었던 서버).
- `evaluated_servers` — 그중 분류 가능한 서버 수. 시계열 데이터가 평가 윈도우 (7일) 에 비해 너무 짧은 신규 서버나 메트릭 누적이 부족한 서버는 평가 불가.

운영자에게 보여줘야 하는 이유: 환경 보고서가 신뢰성 있게 답한 대상의 범위 명시. "23대 평가 후 분포가 이렇다"가 "25대 전체에 적용된다"는 오해 회피.

## 서버 단위 산출물과의 분기

| 항목 | 환경 (본 문서) | 서버 (`server-report.md`) |
|------|---------------|--------------------------|
| 발행 단위 | 환경 전체 1건 | 1대 또는 N대 batch (각 1건씩) |
| 보고서 라우터 | `/reports/environment` | `/reports/servers?ids=...` |
| scope | environment | server |
| 산출물 | 분류 분포 카운트 + 우선순위 권장 | 개별 서버 분류·action |
| 답 | "환경 안 over-provisioned 5대 있음" | "이 서버는 under_provisioned, 업사이즈 검토" |
| 운영 단계 | 1단계 — 환경 전체 현황 한눈 | 2단계 — 개별 서버 판단 |

운영자 표준 흐름: 환경 단위로 분포 확인 → 시급한 카테고리의 서버 list 식별 → 서버 단위 batch 로 개별 판단 → detail 화면에서 검증.

## 한계

1. 위험도 3단계 압축 (customer view 한정) — `recommendation` 6분류를 high/attention/normal 3단계로 압축. shutdown·idle·over_provisioned 가 모두 "주의 필요" 로 묶임. 고객에게 더 세분된 행동을 제시하지 못함.
2. 평균 활용률 KPI 는 산술 평균 — 환경 안 서버 부하 분포가 양극화 (절반 고부하·절반 저부하) 되면 평균은 misleading. p50·p95 분포 표시도 검토 후보.
3. 워크로드 역할 무관 임계 — DB·캐시·앱서버 모두 같은 70%/80% 임계. DB 는 메모리 압박이 정상 운영일 수 있는데 "고위험" 으로 잡힐 가능성. 향후 역할별 임계 분기 시 정밀도 증가.
4. 7일 윈도우 내 일회성 부하 — 단발 부하 (월 1회 배치 등) 가 그 윈도우 안에 들면 평상 부하로 오인. 외부 윈도우 (30일·90일)·요일/시간대 분리 미적용.
5. 정성 요약의 표현 한정 — 결정론 템플릿이라 운영자가 추가 컨텍스트 (예: "이 서버는 신규 도입 한 달째"·"비용 절감 우선") 를 요약에 반영 불가.
6. 인쇄 색상 — 브라우저 인쇄 시 색 처리가 브라우저별 다름. 흑백 PDF 에서 위험도 색이 비슷해 보일 수 있음. `print` CSS 에서 별도 처리.

## 한계 해결 후보 (재논의 시점)

- 워크로드 역할별 임계 분기 → 별도 ADR.
- 통계 윈도우 옵션 (7d·14d·30d) UI 토글 → 현재 7일 default 만 노출.

## 관련 문서·코드

- ADR 0010 — 진단 규칙 기반 한정 (명칭·범위 결정)
- `docs/architecture/web/routers.md` — 보고서 라우터·view 분기
- `docs/architecture/web/services.md` "Recommendation 분류" — USE Method 임계값 출처
- `docs/architecture/web/static-assets.md` "report.html print CSS" — 인쇄 색 처리
- `docs/tradeoffs.md` T13 — 보고서 = diagnostic_jobs 스냅샷 보존
- `src/assessment_engine/recommendation.py` — 분류 임계값·`WINDOW_DAYS`
- `src/assessment_engine/web/services/query_service.py::get_report` — KPI 집계 + view 분기
- `src/assessment_engine/web/services/mappers/report.py::build_report_summary_bullets` — view 분기 시그널
- `src/assessment_engine/web/templates/reports/environment.html` — 환경 보고서 템플릿
- `docs/products/server-report.md` — 서버 단위 산출물 (cross-reference)
