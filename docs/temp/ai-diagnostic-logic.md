# AI 진단 로직 (현재 기준) — 학습 정리

> docs/temp 학습용. 작성 2026-05-25. 현재 구현 기준 (RAG 비활성 default).
> self-contained — 흐름·구조를 문서만으로 이해 가능. 코드 위치는 "더 깊이 보기" 포인터로 병기.

## 1. 한눈에 — 전체 흐름

```
web  POST /api/diagnostics  (scope, server_ids?, time_range)
  -> DiagnosticSubmitter : input_hash 계산 + diagnostic_jobs INSERT(pending) + MQ publish
  -> RabbitMQ (routing key: diagnostic.request)
  -> diagnostic-worker (aio-pika consume)
       1. parse + 멱등성 (Redis SET NX)
       2. status=running, 통계 집계 (규칙 기반 USE Method)
       3. RAG retrieve  (RAG_ENABLED=false -> skip, rag_context=[])
       4. LLM narrative (ollama llama3.1:8b)
       5. 환각 검증 (narrative 숫자 vs payload, 불일치 시 재생성 1회)
       6. status=succeeded + result 저장 (diagnostic_jobs) + Redis 캐시
  <- web  GET /api/diagnostics/{job_id}  (polling, Redis 우선 -> DB fallback)
```

핵심 컴포넌트 3개:
- web: 진단 요청 발행(submit) + 결과 조회(polling). LLM 직접 호출 안 함.
- RabbitMQ: 비동기 작업 큐 (요청을 worker로 전달).
- diagnostic-worker: 실제 진단 수행 (통계 -> LLM -> 저장). 독립 프로세스.

## 2. 가장 중요한 원칙 — "규칙이 결정, LLM은 설명"

- 분류(idle/over/under/optimal 등)와 권고는 전부 규칙 기반(`recommendation.py`, USE Method 임계)이 결정한다. 결정론적이고 LLM 과 무관.
- LLM(ollama)은 그 결과를 사람이 읽을 자연어 narrative로 풀어쓰는 역할만 한다.
- 그래서 LLM 이 없거나(RAG off) 틀려도 "진단 결과(분류·권고)"는 흔들리지 않는다. LLM 은 설명 품질만 좌우.
- LLM 이 숫자를 지어내는 것(환각)을 막으려고, narrative 안 숫자가 입력 payload 에 없으면 재생성한다.

## 3. Trigger — web 에서 작업 발행

요청: `POST /api/diagnostics` body `{scope, server_ids?, time_range, anchor_at?}`
- scope: `"server"`(개별 서버) 또는 `"environment"`(전체 fleet).
- server scope: server_ids N개 -> N개 job 발행. environment scope: server_ids 불필요 -> 1개 job.

발행 로직(DiagnosticSubmitter):
1. input_params 구성 (scope별 — server는 server_public_id 포함, environment는 time_range/anchor만).
2. input_hash = SHA256(scope + canonical JSON). 같은 입력 = 같은 hash.
3. `diagnostic_jobs` 에 status=pending INSERT.
4. RabbitMQ 에 PERSISTENT 메시지 publish.

더블클릭/중복 방어: `diagnostic_jobs` 에 active partial UNIQUE `(scope, input_hash, job_type)` WHERE status IN (pending, running). 같은 진단이 진행 중이면 INSERT 충돌 -> 흡수(새 job 안 만듦).

> 코드: `diagnostic/submitter.py`, `web/routers/diagnostics.py`

## 4. Worker 처리 — 단계별

diagnostic-worker 가 큐에서 메시지를 꺼내 순서대로:

1. JSON 파싱, job_id 추출.
2. 멱등성 1단: Redis `SET NX idempotent:{message_id}` — 같은 메시지 재전송 차단(at-most-once). Redis 장애 시 통과(fail-open).
3. DB UPDATE status=running, progress_stage=`extracting_stats`.
4. 통계 집계(aggregator) — scope별:
   - server: 단일 서버 메트릭 1행 (CPU/MEM/iowait p95, 분류, 권고).
   - environment: 전체 N대 집계 (분류 분포 카운트, avg/median/max, top 권장액션, saturation 상위).
5. progress_stage=`applying_rules` — 분류·권고는 4단계 통계에 이미 규칙으로 산출돼 있음.
6. RAG retrieve (progress_stage=`retrieving_context`): RAG_ENABLED=false 면 retriever=None -> skip, `payload["rag_context"]=[]`.
7. progress_stage=`generating_narrative` — LLM 호출 + 환각 검증.
8. DB UPDATE status=succeeded, result(분류/narrative 등), finished_at.
9. Redis `SET diagnostic:job:{job_id}` (polling 캐시, TTL 1h).

실패 분기: LLM timeout / 환각 2회 실패 / 외부 오류 -> status=failed + error_message. 정상 종료=ACK / 예외=NACK + DLQ.

> 코드: `diagnostic/main.py`(워커 entry), `diagnostic/handler.py`(단계), `diagnostic/aggregator.py`(집계)

## 5. scope별 차이 (server vs environment)

| 항목 | server | environment |
|------|--------|-------------|
| job 수 | 선택 서버 N대 -> N개 | 전체 -> 1개 |
| 입력 통계 | 단일 서버 지표 1행 | N대 집계 (분포·평균·top) |
| LLM 프롬프트 | "이 서버의 p95/분류/권고" -> 진단 | "fleet 분포(over/under/idle 카운트)" -> 전략 요약 |
| RAG 쿼리(켤 때) | 서버 지표 기반 | fleet 분포 기반 |

같은 골격, scope로 단계마다 분기하는 별개 경로.

## 6. 규칙 기반 분류·권고

분류(`recommendation.classify`): USE Method(Utilization + Saturation) 2축. 10단계 short-circuit(위에서 첫 매칭 확정):
```
insufficient -> idle -> shutdown
-> under(swap) -> under(disk) -> under(iowait) -> under(load)
-> over -> under(cpu/mem) -> optimal
```
임계 출처: AWS Compute Optimizer / Azure Advisor / GCP Recommender / Kleinrock 등 산업 표준.

권고(`_build_recommendation_action`): 분류 -> 권장 조치 문구.
- under: hit 된 trigger 별 증설 권고 결합 ("메모리 증설 (스왑 발생) / CPU 증설" 등).
- over -> "자원 축소 검토", idle -> "용도 재평가 / 종료 검토", shutdown -> "종료 가능 검토", optimal -> "적정 운영", insufficient -> "평가 표본 부족".

> 코드: `recommendation.py`(분류 임계·classify), `web/services/mappers/report.py`(권고·판단 문구)
> 참고: 분류/권고 근거 임계는 보고서 "참고자료" 페이지(`/reports/right-sizing-thresholds`)에 표로 명시돼 있음.

## 7. LLM narrative (ollama)

호출(`OllamaLlmClient.generate_narrative`):
- `POST {OLLAMA_BASE_URL}/api/chat`, model `llama3.1:8b`, temperature 0.2(환각 감소).
- 메시지: system 프롬프트 + user 프롬프트(scope별, 통계 payload 삽입 + RAG context 있으면 추가).
- HTTP 오류 시 3회 retry (exponential backoff). 응답 형태 오류는 재시도 안 함.

환각 검증(`find_hallucinated_numbers`):
- narrative 안 숫자 토큰 집합 - payload 숫자 집합 - whitelist = 환각 숫자.
- 환각 발견 시 재생성(최대 2회). 2회 모두 실패 -> ValueError -> status=failed("llm_hallucination").

> 코드: `diagnostic/llm/ollama.py`(호출·프롬프트), `diagnostic/llm/verify.py`(환각 검증)

## 8. 멱등성·실패 처리

- 멱등성: Redis SET NX (1단, at-most-once). 같은 message_id 재전송 무시.
- 실패: timeout(LLM 60s) / 환각 / 외부 오류 -> mark_failed(status=failed, error_message).
- 미구현(인지): worker 가 진행 중(status=running) 강제 종료되면 stale job 이 DB 에 남음 — 자동 정리 없음, 수동 SQL 필요. prod 운영 전 해결 대상.

## 9. DB 모델 (diagnostic_jobs)

주요 컬럼: scope / input_hash / job_type(ai_diagnostic) / status(pending·running·succeeded·failed) / progress_stage / result(JSON) / started_at / finished_at / error_message.
- active partial UNIQUE `(scope, input_hash, job_type)` WHERE status IN (pending, running) — 중복 진행 차단.

> 코드: `db/models/diagnostic_job.py`

## 10. 결과 표시 (web polling)

- `GET /api/diagnostics/{job_id}`: Redis 캐시 우선 read -> miss 시 DB fallback.
- mapper `to_view` 가 표시 파생(분류 한글 라벨, narrative, 진행 단계 등) 추가.
- 환경·서버 보고서 안에서는 inline 진단(`diagnostic-inline.js`)이 보고서 발행 시 자동 trigger + polling 으로 narrative 갱신.

> 코드: `web/routers/diagnostics.py`·`diagnostic_results.py`, `web/services/mappers/diagnostic.py`, `web/static/js/diagnostic-inline.js`

## 11. RAG (현재 OFF — 참고만)

- `RAG_ENABLED=false`(default)면 6단계 retrieve 가 skip 되고 narrative 는 통계만 반영.
- 켜면: 도메인 지식(USE Method/Compute Optimizer 백서 등)을 pgvector 의미검색으로 retrieve 해 LLM 프롬프트에 주입 -> narrative 가 산업 표준 근거 반영.
- 활성화 절차는 별도 정리(`docs/temp/rag-activation.md`). 이번 학습 범위에선 off 상태만 이해하면 충분.

## 12. 학습 동선 추천

1. 흐름 감 잡기: 본 문서 1·2절 (전체 그림 + "규칙이 결정, LLM은 설명").
2. 발행: `diagnostic/submitter.py` (job 생성 + MQ).
3. 처리 핵심: `diagnostic/handler.py` (단계 순서) -> `diagnostic/aggregator.py` (통계).
4. 의사결정: `recommendation.py` (분류 임계·판정 순서).
5. LLM: `diagnostic/llm/ollama.py` + `verify.py` (호출·환각 검증).
6. 표시: `web/services/mappers/diagnostic.py` (polling 응답 가공).
