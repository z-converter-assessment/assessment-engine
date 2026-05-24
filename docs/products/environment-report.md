# 환경 보고서 (Environment Report)

본 문서는 환경 단위 (scope=environment) 산출물의 존재 의의·구현 의도·근거를 정리한다. 환경 안 모든 등록 서버를 묶어 KPI·분류 분포·자동 narrative 를 한 화면에 합성. 보고서와 진단이 같은 환경 단위라 한 산출물 카탈로그로 통합.

서버 단위 산출물(scope=server, 선택 N대 row 단위 상세) 은 `docs/products/server-report.md` 별도.

## 두 산출물

환경 scope 에서 두 산출물이 같은 endpoint·SQL·템플릿 위에 서로 다른 의도 만족.

| 산출물 | 라우터 | 의도 |
|--------|--------|------|
| 환경 보고서 | `GET /reports/environment?view=customer\|engineer&time_range=14d` | 환경 전체 KPI·자원 합계·분류 분포 high-level 한 장. customer(양식 A) vs engineer(양식 B) view 분기 |
| 환경 진단 | `POST /api/diagnostics` scope=environment + `GET /diagnostics?ids=<job_id>` | 분류 분포 카운트 + 우선 검토 권장 한 줄 narrative. 사용자 trigger 만 발행 (ADR 0023). 진단 결과 페이지가 환경 보고서 iframe 2개 (customer·engineer view) 미리 렌더 — 운영자가 한 화면에서 두 view + 진단 narrative 모두 확인 |

두 산출물의 관계 (T13):
- 동일 `diagnostic_jobs` 테이블 row 보존. 보고서 / AI 진단 둘 다 본 테이블 record.
- 보고서 발행 record 는 PRG pattern — `POST /reports/environment/emit` 가 `record_report_emission` 호출 (GET 은 read-only, 다시 보기 / 직접 URL 진입 시 중복 row 방지).
- 이력 표시 분리: 보고서 이력 `/reports/history` (customer + engineer union, view 필터), 진단 발행 이력 `/diagnostics/history` (job_type='ai_diagnostic' 자동 필터).

## 위치

- UI 진입점:
  - 환경 보고서 — 대시보드 list 페이지 상단 "환경 보고서" 버튼 또는 `/reports/environment?view=customer|engineer` 직접 호출
  - 환경 진단 — 대시보드 list 상단 환경 진단 패널 + 결과 페이지 `/diagnostics?ids=<job_id>` + 이력 `/diagnostics/history`
- 발행 경로:
  - 환경 보고서 — 운영자 즉시 호출 (HTTP GET). 발행 시점 즉시 SQL 집계 + render
  - 환경 진단 — 운영자 즉시 발행 (웹 모달) — ADR 0023: scheduler cron 폐기, 사용자 trigger 만
- 산출물 형태: HTML SSR. 브라우저 인쇄로 PDF/PPT 캡처 (백엔드 PDF export 미도입 — `docs/tradeoffs.md` T 참조)

## 존재 의의

운영자·고객이 다음 질문에 한눈에 답하기 위한 산출물.

질문 1: "지금 우리 환경, 자원 배분이 적절한가?"

수십·수백 대 서버를 가진 환경에서 개별 서버 상세를 다 확인하지 않고도 환경 전체의 자원 배분 상태(과다·부족·정상)를 분포로 본다. 서버 단위 detail은 너무 많고, 카드 한 장으로는 환경 전반을 못 본다. 그 사이를 메우는 산출물이 환경 보고서·환경 진단.

질문 2: "다음에 어디부터 손대야 하는가?"

분포에서 가장 시급한 카테고리(보통 under_provisioned 위험 또는 over_provisioned 비용)를 우선 검토 대상으로 명시. 운영자가 "오늘은 over-provisioned 5대 다운사이즈 검토"처럼 다음 단계 행동을 결정. 그 다음 단계는 서버 단위 산출물 (`docs/products/server-report.md`) 로 개별 서버 후보 식별.

질문 3: "고객사·내부 보고 시 자원 현황을 어떻게 요약하는가?"

고객 미팅·내부 정기 보고에서 환경 자원 현황을 한 줄로 표현 가능 — "14일 평가 기준 평가 가능 23대 중 over-provisioned 5대·under-provisioned 2대·optimal 16대, 우선 검토는 over-provisioned 다운사이즈". customer view 보고서는 한 장 KPI·자동 요약, engineer view 는 정량 분석 추가.

## 산출 정보

### 환경 보고서 — 두 view 공통 상단

| 영역 | 내용 | 데이터 source |
|------|------|--------------|
| KPI 6개 | 대상 서버 / 온라인 / 주의 필요 / 고위험 / 평균 CPU p95 / 평균 메모리 p95 | service KPI 집계 (time_range 윈도우) |
| 환경 총 자원 | 총 vCPU / 메모리 / 디스크 | inventory 합산 |
| 역할 분포 | service_classifier 카테고리별 카운트 | inventory + 분류 |
| 분류 분포 도넛 | over/under/idle/optimal 카운트 | `recommendation.classify` |

### view 분기 — customer (양식 A)

목적: 컨설턴트가 고객 미팅·내부 보고에 들고 가는 한 장짜리 환경 자원 요약.

- 위험도 3단계 압축 (high/attention/normal)
- 자동 정성 요약 (행동 시그널만): 고위험·주의·디스크 임박·I/O 병목·재부팅·OS EOL
- Print 우선 — 인쇄 PDF 대응

### view 분기 — engineer (양식 B)

목적: 운영자·엔지니어가 환경 단위 정량 패턴 분석 + Right-sizing 근거 검증.

- 5분류 그대로 (under/over/idle/optimal/insufficient_data) + 판단 텍스트
- 자동 정성 요약 (customer 시그널 + engineer 시그널): 역할별 평균 CPU 최고치·Saturation 발생·CPU 변동성 큼
- 화면 분석 우선 (인쇄 가능하지만 가로 폭 한계)
- 정량 표 추가: LOAD · 변동성 · I/O wait · DISK · NET · SWAP/Mount · Uptime/재부팅 · 판단

분기 메커니즘:
- 같은 endpoint·SQL·템플릿. `view` 파라미터로 `{% if view == "customer" %} ... {% elif view == "engineer" %} ... {% endif %}` 블록 토글.
- service `get_report(view=view)` → mapper `build_report_summary_bullets(view=view)` view 전달.

### 환경 진단 — 한 줄 narrative

스케줄러 또는 사용자 발행 → 워커가 4 항목 계산 후 narrative 1줄로 합성.

| 항목 | 내용 | source |
|------|------|--------|
| 평가 윈도우 | 14일 default (`recommendation.WINDOW_DAYS`) | AWS Compute Optimizer 표준 |
| 평가 커버리지 | `evaluated_servers / total_servers` — 메트릭 데이터가 분류 가능한 정도로 누적된 서버 수 | DB 시계열 집계 |
| 분류 분포 | over_provisioned / under_provisioned / idle / optimal 각 카운트 | `recommendation.classify` |
| 우선 검토 권장 | 분포 중 가장 시급한 카테고리 1개 | 규칙 |

산출 결과 예시:
```
최근 14일 환경 진단 — 평가 대상 23대 (전체 25대). 분류 분포:
over-provisioned 5대, under-provisioned 2대, idle 0대, optimal 16대.
우선 검토 권장: over-provisioned 5대의 다운사이즈.
```

## 의사결정 근거

### 분류 임계값 출처

| 분류 | 트리거 조건 | 출처 |
|------|-----------|------|
| idle | CPU p95 < 3% + 네트워크 미사용 | Azure Advisor "underutilized VM" 기준 |
| over_provisioned | CPU p95 <= 30% + 메모리 p95 <= 50% | AWS Compute Optimizer "over-provisioned" 기준 |
| under_provisioned | CPU p95 >= 70% 또는 메모리 p95 >= 80% 또는 swap 발생 | Kleinrock 큐잉 + Linux page cache 운영 통념 |
| optimal | 위 어디에도 해당 안 함 | residual |

위험도 3단계 압축 (customer view 한정):

| 위험도 | 트리거 조건 |
|--------|-----------|
| 고위험 | CPU p95 70% 이상 또는 메모리 p95 80% 이상 또는 swap 발생 |
| 주의 필요 | over-provisioned 또는 거의 미사용(idle) |
| 정상 | 그 외 (optimal) |

### 평가 윈도우 14일

- AWS Compute Optimizer right-sizing 권장의 표준 윈도우
- Azure Advisor 도 7~14일 사용
- 사용량의 일·주 단위 주기성(주중·주말) 평탄화에 충분
- 너무 짧으면(1~3일) 일시 부하·정기 백업을 평상 부하로 오인
- 너무 길면(30일+) 최근 도입된 워크로드 부하 반영 늦음

### 규칙 기반 한정 (LLM 미사용)

- 본 시점 정책: 외부 LLM 호출 금지 (과금·보안). 로컬 LLM (ollama) 은 운영 부담 vs 가치 손익분기 미만.
- 분류·권장은 결정론 임계값으로 충분. 자연어 합성은 결정론 템플릿으로 산출.
- 결정 근거: ADR 0010.

## 평가 커버리지의 의미

`total_servers` vs `evaluated_servers` 는 다른 수치다.
- `total_servers` — 인벤토리에 등록된 모든 활성 서버 수 (최근 N 시간 안에 에이전트가 살아 있었던 서버).
- `evaluated_servers` — 그중 분류 가능한 서버 수. 시계열 데이터가 평가 윈도우 (14일) 에 비해 너무 짧은 신규 서버나 메트릭 누적이 부족한 서버는 평가 불가.

운영자에게 보여줘야 하는 이유: 환경 진단이 신뢰성 있게 답한 대상의 범위 명시. "23대 평가 후 분포가 이렇다"가 "25대 전체에 적용된다"는 오해 회피.

## 서버 단위 산출물과의 분기

| 항목 | 환경 (본 문서) | 서버 (`server-report.md`) |
|------|---------------|--------------------------|
| 발행 단위 | 환경 전체 1건 | 1대 또는 N대 batch (각 1건씩) |
| 보고서 라우터 | `/reports/environment` | `/servers/report?ids=...` |
| 진단 scope | environment | server |
| 산출물 | 분류 분포 카운트 + 우선순위 권장 | 개별 서버 분류·action·narrative |
| 답 | "환경 안 over-provisioned 5대 있음" | "이 서버는 under_provisioned, 업사이즈 검토" |
| 운영 단계 | 1단계 — 환경 전체 현황 한눈 | 2단계 — 개별 서버 판단 |

운영자 표준 흐름: 환경 단위로 분포 확인 → 시급한 카테고리의 서버 list 식별 → 서버 단위 batch 로 개별 판단 → detail 화면에서 검증.

## 한계

1. 위험도 3단계 압축 (customer view 한정) — `recommendation.classify` 5분류를 high/attention/normal 3단계로 압축. shutdown·idle·over_provisioned 가 모두 "주의 필요" 로 묶임. 고객에게 더 세분된 행동을 제시하지 못함.
2. 평균 활용률 KPI 는 산술 평균 — 환경 안 서버 부하 분포가 양극화 (절반 고부하·절반 저부하) 되면 평균은 misleading. p50·p95 분포 표시도 검토 후보.
3. 워크로드 역할 무관 임계 — DB·캐시·앱서버 모두 같은 70%/80% 임계. DB 는 메모리 압박이 정상 운영일 수 있는데 "고위험" 으로 잡힐 가능성. 향후 역할별 임계 분기 시 정밀도 ↑.
4. 14일 윈도우 내 일회성 부하 — 단발 부하 (월 1회 배치 등) 가 그 윈도우 안에 들면 평상 부하로 오인. 외부 윈도우 (30일·90일)·요일/시간대 분리 미적용.
5. 자연어 narrative 의 표현 한정 — 결정론 템플릿이라 운영자가 추가 컨텍스트 (예: "이 서버는 신규 도입 한 달째"·"비용 절감 우선") 를 narrative 에 반영 불가. 외부 LLM 도입 시 가능해질 영역.
6. 인쇄 색상 — 브라우저 인쇄 시 색 처리가 브라우저별 다름. 흑백 PDF 에서 위험도 색이 비슷해 보일 수 있음. `print` CSS 에서 별도 처리.

## 한계 해결 후보 (재논의 시점)

- 워크로드 역할별 임계 분기 → 별도 ADR.
- 외부 LLM (가격·보안 제약 해소 시) 도입 → ADR 0010 정정.
- 통계 윈도우 옵션 (7d·14d·30d) UI 토글 → 현재 14일 default 만 노출.

## 관련 문서·코드

- ADR 0004 — 진단 워커 아키텍처 (인프라 결정)
- ADR 0010 — 진단 규칙 기반 한정 (명칭·범위 결정)
- `docs/architecture/diagnostic.md` — 모듈 구조·흐름
- `docs/architecture/web/routers.md` — 보고서 라우터·view 분기
- `docs/architecture/web/services.md` "Recommendation 분류" — USE Method 임계값 출처
- `docs/architecture/web/static-assets.md` "report.html print CSS" — 인쇄 색 처리
- `docs/tradeoffs.md` T13 — 보고서 = diagnostic_jobs 통합 + 환경 진단 결과 iframe view toggle
- `src/assessment_engine/recommendation.py` — 분류 임계값·`WINDOW_DAYS`
- `src/assessment_engine/diagnostic/submitter.py` — 진단 발행 (ADR 0014). trigger 채널 = web POST 만 (ADR 0023)
- `src/assessment_engine/diagnostic/llm/ollama.py::OllamaLlmClient` — LLM narrative 합성 (ollama HTTP)
- `src/assessment_engine/web/services/query_service.py::get_report` — KPI 집계 + view 분기
- `src/assessment_engine/web/services/mappers/report.py::build_report_summary_bullets` — view 분기 시그널
- `src/assessment_engine/web/templates/reports/environment.html` — 환경 보고서 템플릿
- `docs/products/server-report.md` — 서버 단위 산출물 (cross-reference)
