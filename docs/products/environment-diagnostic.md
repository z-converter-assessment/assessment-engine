# 환경 진단 (Environment Diagnostic)

본 문서는 환경 진단 산출물의 존재 의의·구현 의도·의사결정 근거를 정리한다. 코드·인프라 구현 세부는 `docs/architecture/diagnostic.md` + ADR 0004 + ADR 0010 별도.

## 위치

- UI 진입점: 대시보드 `list.html` 상단 환경 진단 패널 + 결과 페이지 `/diagnostics?ids=...` + 이력 `/diagnostics/history`
- 발행 경로: 사용자 즉시 발행(웹 모달) 또는 스케줄러 매일 03시 자동(`diagnostic-scheduler` 컨테이너)
- 산출물 형태: 한 줄 자연어 요약 + 분류 분포 카운트(4종) + 평가 커버리지(전체 대비 평가 가능 대수)

## 존재 의의

운영자·고객이 다음 질문에 한눈에 답하기 위한 산출물.

질문 1: "지금 우리 환경, 자원 배분이 적절한가?"

수십·수백 대 서버를 가진 환경에서 개별 서버 상세를 다 확인하지 않고도 환경 전체의 자원 배분 상태(과다·부족·정상)를 분포로 본다. 서버 단위 detail은 너무 많고, 카드 한 장으로는 환경 전반을 못 본다. 그 사이를 메우는 산출물이 환경 진단이다.

질문 2: "다음에 어디부터 손대야 하는가?"

분포에서 가장 시급한 카테고리(보통 under_provisioned 위험 또는 over_provisioned 비용)를 우선 검토 대상으로 명시. 운영자가 "오늘은 over-provisioned 5대 다운사이즈 검토"처럼 다음 단계 행동을 결정.

질문 3: "고객사·내부 보고 시 자원 현황을 어떻게 요약하는가?"

고객 미팅·내부 정기 보고에서 환경 자원 현황을 한 줄로 표현 가능 — "14일 평가 기준 평가 가능 23대 중 over-provisioned 5대·under-provisioned 2대·optimal 16대, 우선 검토는 over-provisioned 다운사이즈". 보고서 양식 A·B와 별개로, 화면에서 즉시 인용 가능한 raw 텍스트.

## 산출 정보

스케줄러 또는 사용자 발행 → 워커가 다음 4 항목 계산 후 narrative 1줄로 합성.

| 항목 | 내용 | source |
|------|------|--------|
| 평가 윈도우 | 14일 default (`recommendation.WINDOW_DAYS`) | AWS Compute Optimizer 표준 |
| 평가 커버리지 | `evaluated_servers / total_servers` — 메트릭 데이터가 분류 가능한 정도로 누적된 서버 수 | DB 시계열 집계 |
| 분류 분포 | over_provisioned / under_provisioned / idle / optimal 각 카운트 | `recommendation.classify` |
| 우선 검토 권장 | 분포 중 가장 시급한 카테고리 1개 (현재: over_provisioned 다운사이즈) | 규칙 |

산출 결과 예시:
```
최근 14일 환경 진단 — 평가 대상 23대 (전체 25대). 분류 분포:
over-provisioned 5대, under-provisioned 2대, idle 0대, optimal 16대.
우선 검토 권장: over-provisioned 5대의 다운사이즈.
```

## 의사결정 근거

분류 임계값 출처:

| 분류 | 트리거 조건 | 출처 |
|------|-----------|------|
| idle | CPU p95 < 3% + 네트워크 미사용 | Azure Advisor "underutilized VM" 기준 |
| over_provisioned | CPU p95 < 30% | AWS Compute Optimizer "over-provisioned" 기준 |
| under_provisioned | CPU p95 > 80% 또는 메모리 압박 (swap 발생·available 부족) | AWS Compute Optimizer + Linux page cache 운영 통념 |
| optimal | 위 어디에도 해당 안 함 | residual |

평가 윈도우 14일:
- AWS Compute Optimizer가 right-sizing 권장에 사용하는 표준 윈도우.
- Azure Advisor도 14일 또는 7일 사용.
- 사용량의 일·주 단위 주기성(주중·주말 차이)을 평탄화하기에 충분한 길이.
- 단위가 너무 짧으면(1~3일) 일시 부하·정기 백업 등을 평상 부하로 오인.
- 단위가 너무 길면(30일+) 최근 도입된 워크로드 부하 반영이 느림.

규칙 기반 한정 (LLM 미사용) 근거:
- 본 시점 정책: 외부 LLM 호출 금지(과금·보안). 로컬 LLM(ollama)은 운영 부담 vs 가치 손익분기 미만.
- 분류·권장은 결정론 임계값으로 충분. 자연어 합성은 결정론 템플릿으로 산출 가능.
- 결정 근거: ADR 0010.

## 평가 커버리지의 의미

`total_servers` vs `evaluated_servers`는 다른 수치다.

- `total_servers` — 인벤토리에 등록된 모든 활성 서버 수 (최근 N시간 안에 에이전트가 살아 있었던 서버).
- `evaluated_servers` — 그중 분류 가능한 서버 수. 시계열 데이터가 평가 윈도우(14일)에 비해 너무 짧은 신규 서버나 메트릭 누적이 부족한 서버는 평가 불가.

운영자에게 보여줘야 하는 이유: 환경 진단이 신뢰성 있게 답한 대상의 범위를 명시. "23대 평가 후 분포가 이렇다"가 "25대 전체에 적용된다"는 오해 회피.

## 한계

규칙 기반의 본질적 한계:

1. 워크로드 특성 무관 — DB·캐시·앱서버 모두 같은 임계로 분류. DB는 메모리 압박이 정상 운영일 수 있는데 그것도 under_provisioned로 잡힐 가능성. 향후 서버 역할 분류(`role` 메타 + 역할별 임계 분리)로 개선 여지.
2. 14일 윈도우 내 일회성 부하 — 단발 부하(월 1회 배치 등)가 그 윈도우 안에 들면 평상 부하로 오인. 외부 윈도우(30일·90일)·요일/시간대 분리는 미적용.
3. 자연어 narrative의 표현 한정 — 결정론 템플릿이라 운영자가 추가 컨텍스트(예: "이 서버는 신규 도입 한 달째"·"비용 절감 우선 vs 안정성 우선")를 narrative에 반영할 수 없음. 외부 LLM 도입 시 가능해질 영역.

## 한계 해결 후보 (재논의 시점)

- 워크로드 역할별 임계 분기 → 별도 ADR.
- 외부 LLM(가격·보안 제약 해소 시) 도입 → ADR 0010 정정.
- 통계 윈도우 옵션(7d·14d·30d) UI 토글 → 현재 14일 default만 노출. 시점·범위 옵션 도입 시 보고서 양식과 정합 필요.

## 관련 문서·코드

- ADR 0004 — 진단 워커 아키텍처 (인프라 결정)
- ADR 0010 — 진단 규칙 기반 한정 (명칭·범위 결정)
- `docs/architecture/diagnostic.md` — 모듈 구조·흐름
- `src/assessment_engine/recommendation.py` — 분류 임계값·`WINDOW_DAYS`
- `src/assessment_engine/diagnostic/scheduler.py` — 스케줄러 흐름 (매일 03시)
- `src/assessment_engine/diagnostic/llm/mock.py::_environment_narrative` — 자연어 합성 템플릿
