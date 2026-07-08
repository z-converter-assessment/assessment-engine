# ADR 0023 — diagnostic scheduler 폐기 (사용자 trigger 모델로 통합)

상태: Superseded — AI 진단(LLM narrative) 기능 폐기 (2026-06-14)

이전 상태: Proposed (2026-05-23)

## Context

ADR 0004 (Refined by 0010) 안 진단 워커 인프라 결정 본문 = scheduler cron 자동 발화 + 워커 별도 프로세스 + LLM 토글. 본 ADR 은 cron 자동 발화 부분 본질 정정 — 워커 별도 프로세스 + LLM 토글 결정은 그대로 유효.

본 시점 0004 의 cron 결정 본문 (정정 대상):

- 환경변수 `DIAGNOSTIC_SCHEDULE_CRON` (기본 `0 3 * * *` — 매일 03시 KST)
- 발화 시 활성 서버 (`last_seen_at > now() - 24h`) catalog -> server scope job N 건 enqueue + environment scope job 1 건 enqueue + retention DELETE
- 별도 entrypoint `python -m assessment_engine.diagnostic.scheduler`

본질 검토 catalog:

1. 평가 윈도우 = 14일 (F10 단일 진실, AWS Compute Optimizer 표준).
2. 평가 결과 변화 빈도 = 14일 윈도우 라 1 시간 안 의미 있는 변화 거의 없음. 매일 cron 자동 발화 = 활성 서버 N 대 + environment 1 건 자동 누적 -> 대부분 redundant (변화 없는 동일 분류 row 반복).
3. retention DELETE 본 시점 코드 비활성 (`scheduler.py` 안 "임시 비활성" 주석) -> 활성화 의무 catalog 누적.
4. LLM 비용 — mock 단계 0, ollama 단계 GPU 부담 N 대/일. cron 자동 발화 = 비용 vs 가치 비율 약.
5. anchor_at 분 단위 truncate (`_normalize_anchor`) -> cron 발화 시점 anchor 와 사용자 진입 시점 anchor 다름 -> 사전 산출 캐시 의의 약 (사용자 진입 시점 = 항상 anchor 재산출).
6. active partial UNIQUE `(scope, input_hash, job_type) WHERE status IN ('pending','running')` -> 사용자 동시 trigger 시 중복 발행 자연 흡수 -> trigger 모델 단독 운영 안정성 보장.

## Options

### A. cron 유지 + retention 활성화

본 시점 catalog 그대로. retention DELETE 활성화 (job row 무한 누적 차단).

- 장점: 자동 누적 이력 풍부 + 환경 추세 감지
- 단점: 본질적 변화 없는 row 다수 누적. ollama 도입 시 GPU 비용 N 대/일

### B. cron 폐기 -> 사용자 trigger 만

scheduler 컴포넌트 제거. 사용자 진입 시점 "진단 실행" 버튼 클릭 시만 발화.

- 장점: 사용자 의도 표현 명시 + LLM 비용 의도만큼 + retention 부담 작음 (사용자 trigger 만 누적)
- 단점: 자동 이력 누적 없음 -> 환경 변화 추세 감지 = 사용자 진입 빈도에 종속

### C. 하이브리드 — environment cron 만 + server scope 사용자 trigger 만

environment scope 1 건/일 cron 유지 + server scope = 사용자 trigger.

- 장점: 환경 추세 누적 + server scope GPU 비용 회피
- 단점: scheduler 컴포넌트 유지 (환경 1 건 발화 위해서). 절반 결정의 본질 모호

## Decision

옵션 B 채택.

근거:

1. 14일 윈도우 평가 결과 변화 빈도 낮음 -> 매일 cron 자동 발화 = 대부분 redundant. 의미 있는 변화 (분류 전이) = 사용자 진입 시점 진단 으로 충분 포착.
2. 사용자 trigger 명시 의도 표현 = 단일 고객사 내부 포털 본질 정합. 운영자가 진단 의도 명시 + 결과 대기 UX 정공.
3. ollama 단계 GPU 비용 + retention 부담 모두 회피.
4. RAG 도입 (ADR 0024) 와의 본질 정합 — peer learning 자료원 본질이 진단 narrative 가 아닌 통계 시계열 (이미 `server_metrics` 누적). 즉 RAG 도입 시점에도 cron 자동 진단 누적 정당화 약. 도메인 지식 RAG (1순위) 도 cron 무관 (1회 ingest CLI).
5. anchor_at 분 단위 truncate + active hash dedup 정공 -> 사용자 동시 trigger 시 중복 발행 자연 흡수 -> trigger 모델 단독 운영 안정성 보장.

## Consequences

### 긍정

- scheduler 컴포넌트 제거 (entrypoint 4 -> 3 단순화: web + consumer + diagnostic-worker).
- LLM 비용 의도 비례 (cron 자동 발화 N 대/일 부담 0).
- retention 부담 감소 (사용자 trigger 만 누적).
- ADR 0024 (RAG 도입) 와 본질 정합 (cron 무관 RAG 자료 카탈로그).
- ADR 0014 (Diagnostic 발행 책임 분리) 본질 유지 — submitter 가 web/worker 양쪽 import 가능 모듈로 잔존. trigger 채널만 단일화 (web POST -> submitter).

### 부정·한계

- 자동 이력 누적 부재 -> 환경 변화 추세 감지 = 사용자 진입 빈도에 종속. 운영자가 본 시점 진단 안 누르면 자료 누적 0.
- 운영자가 매번 trigger 의무 -> UX 부담 (1 클릭). TTL 조건부 자동 (옵션 C 후보) 도입 = 본 phase 후 사용 패턴 측정 후 의문.

### 전환 경로 (자동 발화 재도입 시점)

- 사용 패턴 측정 결과 운영자 진입 빈도 높음 + TTL N 시간 안 redundant 진단 비율 낮음 시 = TTL 조건부 자동 (lazy refresh) 도입.
- 별도 ADR 정정 의무. cron 자동 발화 (본 ADR 폐기 대상) 와 본질 다름 — TTL = 사용자 진입 trigger + cache 미스 시 자동, cron = 사용자 무관 백그라운드.

### 영향 catalog (코드 + docs)

- 제거: `src/assessment_engine/diagnostic/scheduler.py`
- 제거: `dev/docker-compose.yml` 안 `diagnostic-scheduler` service
- 제거: `Dockerfile` 안 scheduler 관련 stage·CMD (있는 경우)
- 제거: `DiagnosticSettings` 안 cron 관련 필드 (`diagnostic_schedule_cron` · `diagnostic_active_server_window_hours` · `diagnostic_retention_days` 등)
- 제거: `croniter` 의존성 (사용처 0 확인 후 `pyproject.toml`·`uv.lock` 정정)
- 정정: `docs/architecture/diagnostic.md` 안 scheduler 절
- 정정: `docs/guides/deploy.md` 안 scheduler 절
- 정정: `docs/reference/contracts/env.md` 안 cron 관련 키 카탈로그
- 정정: ADR 0004 상태 ("Refined by 0023" 표시 추가, cron 결정 본문 supersede)
- 정정: `.claude/CLAUDE.md` 안 scheduler 언급 (#A0·#F11 등)

## 관련 문서

- ADR 0004: AI 진단 워커 아키텍처 (cron 결정 본문 본 ADR 로 정정. worker + LLM 토글은 유효)
- ADR 0010: 진단을 규칙 기반으로 한정 (LLM 분기 보류)
- ADR 0014: Diagnostic 발행 책임 분리 (submitter 본질 유지, trigger 채널 단일화)
- ADR 0024 (예정): AI 진단 RAG 도입 (cron 무관 RAG 자료 카탈로그)
- `.claude/CLAUDE.md` #F11 Disposability — diagnostic-scheduler 절 정정 의무
