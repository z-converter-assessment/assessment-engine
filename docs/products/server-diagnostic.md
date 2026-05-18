# 서버 진단 (Server Diagnostic)

본 문서는 서버 진단(scope=server) 산출물의 존재 의의·구현 의도·근거를 정리한다. 코드·인프라 세부는 `docs/architecture/diagnostic.md` + ADR 0004 + ADR 0010 별도. 환경 진단(scope=environment)과의 관계는 `docs/products/environment-diagnostic.md` 참조.

## 위치

- UI 진입점: 대시보드 list 페이지에서 N대 선택 → "서버 진단 (N)" 버튼 (batch 발행), 또는 server detail 페이지 "서버 진단" 카드 (단건 발행)
- 발행 경로: 사용자 즉시 발행(웹 모달) 또는 스케줄러 매일 03시 자동(`diagnostic-scheduler` 컨테이너 — 활성 서버 전체에 scope=server 각각 enqueue)
- 산출물 형태: 한 줄 자연어 narrative + 분류(under/over/idle/optimal) + 권장 action

## 존재 의의

운영자가 단일 서버 또는 N대 server batch에 대한 right-sizing 판단을 받기 위한 산출물. 다음 질문에 답한다.

질문 1: "이 서버 한 대, 자원 배분이 적절한가?"

server detail 페이지에서 "서버 진단" 카드가 USE Method 분류·권장 action·narrative를 노출. 운영자가 detail 화면 안에서 즉시 판단 가능 — 별도 보고서·환경 분포 비교 불필요.

질문 2: "이 N대 batch, 어떤 서버부터 손대야 하는가?"

list에서 선택한 N대에 scope=server batch 발행 → 각 서버별 분류 결과를 결과 페이지에서 polling으로 추적. 분류가 under_provisioned·idle·over_provisioned 등으로 나뉘므로 운영자가 우선순위 결정 가능.

질문 3: "환경 진단 분포에서 'under_provisioned 5대'를 봤다 — 그 5대가 누구인가?"

환경 진단은 환경 단위 분포 카운트만 제공 — 개별 서버 식별은 못 함. 서버 진단을 list에서 batch 발행하면 어떤 서버가 어떤 분류인지 표 단위 확인 가능. 환경 진단의 행동 follow-up.

## 산출 정보

서버 진단 job 1건당:

| 항목 | 내용 | source |
|------|------|--------|
| 평가 윈도우 | 사용자 선택 (15m/1h/6h/24h/7d/14d default/30d) | UI 모달 또는 14d default |
| Anchor 시점 | 사용자 선택 (KST datetime) 또는 현재 | UI 모달 또는 default now |
| 분류 | under_provisioned / over_provisioned / idle / shutdown / optimal / insufficient_data | `recommendation.classify` |
| 권장 action | upsize_cpu / upsize_memory / downsize_cpu / downsize_memory / no_action 등 | `recommendation` |
| 자연어 narrative | "서버 {hostname}는 최근 {window} 동안 CPU p95 {%}, 메모리 p95 {%} 사용 ..." | `mock.py::_server_narrative` (결정론 템플릿) |

산출 결과 예시:
```
서버 db-01는 최근 14일 동안 CPU p95 12.3%, 메모리 p95 35.0% 사용.
분류는 과다 프로비저닝.
AWS Compute Optimizer 임계값(CPU p95 30%) 기준으로 CPU 다운사이즈 권장.
```

## 환경 진단과의 분기 의도

| 항목 | 환경 진단 (scope=environment) | 서버 진단 (scope=server) |
|------|-----------------------------|------------------------|
| 발행 단위 | 환경 전체 1건 | 1대 또는 N대 batch (각 1건씩) |
| 산출물 | 분류 분포 카운트 + 우선순위 권장 | 개별 분류·action·narrative |
| 답 | "환경 안 over-provisioned 5대 있음" | "이 서버는 under_provisioned, 업사이즈 검토" |
| 운영 단계 | 1단계 — 환경 전체 현황 한눈 | 2단계 — 개별 서버 판단 |

운영자 표준 흐름: 환경 진단으로 분포 확인 → 시급한 카테고리의 서버 list 식별 → server 진단 batch로 개별 판단 → detail 화면에서 검증.

## 의사결정 근거

서버 진단 분류 임계값 출처:

| 분류 | 트리거 조건 | 출처 |
|------|-----------|------|
| idle | CPU p95 < 3% + 네트워크 미사용 (`SHUTDOWN_CPU_P95_PCT=3`) | Azure Advisor "underutilized VM" 기준 |
| over_provisioned | CPU p95 ≤ 30% + 메모리 p95 ≤ 50% (`CPU_DOWNSIZE_P95_PCT=30`·`MEM_DOWNSIZE_P95_PCT=50`) | AWS Compute Optimizer "over-provisioned" 기준 |
| under_provisioned | CPU p95 ≥ 70% 또는 메모리 p95 ≥ 80% 또는 swap 발생 (`CPU_UPSIZE_P95_PCT=70`·`MEM_UPSIZE_P95_PCT=80`) | Kleinrock 큐잉 + Linux page cache 운영 통념 |
| optimal | 위 어디에도 해당 안 함 | residual |

권장 action (예시):
- under_provisioned → upsize_cpu / upsize_memory
- over_provisioned → downsize_cpu / downsize_memory
- idle → shutdown_idle (Azure Advisor 기준)

평가 윈도우 7개 옵션 (15m·1h·6h·24h·7d·14d·30d):
- 14d default — AWS Compute Optimizer right-sizing 표준 윈도우
- 짧은 윈도우(15m·1h·6h) — 단발 부하·실시간 시연 검증
- 긴 윈도우(30d) — 신뢰성 ↑하지만 최근 변동 반영 늦음

## 한계

1. 분류 라벨 어휘가 운영자에게 항상 직관적이지 않음 — "over_provisioned"·"under_provisioned"의 의미는 명시적 가이드(`recommendation.py` 상수)에 의존. 한국어 라벨(`과다 프로비저닝`·`리소스 부족` 등)이 한국어 사용자에게 더 명확하지만 영어 분류 식별자는 그대로 코드·메시지에 박힘.
2. 워크로드 역할 무관 임계 — DB·캐시·앱서버 모두 같은 70%/80% 임계. DB는 메모리 압박이 정상 운영일 수 있는데도 under_provisioned로 잡힐 가능성. 역할별 정밀 분기는 향후 별도 결정.
3. anchor 임의 선택 가능 — 운영자가 특정 시점(부하 spike 발생 직후 등) anchor로 잡으면 분류가 그 윈도우 한정. 표준 14d default 외 사용 시 운영자가 의도 인지 의무.
4. 진단 job stale 정리 미구현 — 워커 강제 종료 시 `status='running'` job 수동 정리 필요 (CLAUDE.md #F11).
5. 단일 narrative 합성 — 결정론 템플릿이라 운영자가 추가 컨텍스트("이 서버는 신규 도입 한 달째"·"비용 절감 우선") 반영 불가. 외부 LLM 도입 시 가능해질 영역 (ADR 0010·0003).

## 관련 문서·코드

- ADR 0004 — 진단 워커 아키텍처 (인프라 결정)
- ADR 0010 — 진단 규칙 기반 한정
- `docs/architecture/diagnostic.md` — 모듈 구조·흐름
- `docs/products/environment-diagnostic.md` — 환경 단위 진단 (분포 카운트)
- `src/assessment_engine/recommendation.py` — 분류 임계값
- `src/assessment_engine/diagnostic/scheduler.py` — 스케줄러 흐름 (매일 03시·활성 서버 전체)
- `src/assessment_engine/diagnostic/llm/mock.py::_server_narrative` — 자연어 합성 템플릿
- `src/assessment_engine/web/services/diagnostic_service.py` — job 발행·polling
- `src/assessment_engine/web/templates/servers/detail.html` — server detail 페이지 진단 카드
