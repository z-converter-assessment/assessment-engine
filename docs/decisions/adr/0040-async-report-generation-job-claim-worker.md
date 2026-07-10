# ADR 0040 — 비동기 보고서 발행 복원 (web 내 job-claim 워커)

상태: Accepted (2026-06-21) — Amended by ADR 0055 (job-claim 워커 배치 web lifespan -> 전용 워커 프로세스, DB 상태머신 결정은 유지)

## Context

보고서 발행이 동기 HTTP 요청 안에서 완료된다(발행 즉시 succeeded, ADR 0014 정정·CLAUDE.md C1). VM 40대 환경에서 선택 N대 engineer 발행이 매우 느렸고 원인은 두 층이었다.

1. O(N^2) + 상수 폭증 — `get_single_server_report` 가 N대 fan-out 루프마다 `get_attention_signals`(내부에서 전체 서버 대상 `report_aggregate` 무거운 CTE)를 재호출. 40대 발행 시 전역 집계가 40회 반복. 보고서는 그 결과 중 os_eol 만 사용.
2. O(N) 워크로드를 동기 HTTP 에 결박 — 41개 보고서(child N + selection) 생성·저장이 요청 1개 안에서 동기 완료. 쿼리를 줄여도 서버 수 증가 시 응답 시간이 선형 증가.

1번은 attention 주입으로 제거(전역 집계 40->1, 별도 변경). 본 ADR 은 2번 — 발행을 비동기로 분리하는 실행 인프라 결정이다.

### 히스토리
- ADR 0004 가 비동기 작업 정석으로 옵션 B(별도 워커 + RabbitMQ 큐)를 채택하고 옵션 A(web `asyncio.create_task`/BackgroundTasks)를 "web 재시작 시 in-flight 손실, web 점유"로 기각했다.
- 그 워커는 LLM narrative(수십초~수분 외부 호출) 생성용이었고, LLM 폐기(2026-06-14)와 함께 워커·스케줄러가 제거되며 발행이 동기로 단순화됐다. 즉 비동기를 버린 건 "비동기가 나빠서"가 아니라 "비동기가 필요했던 무거운 외부 호출이 사라져서"다.
- 지금은 LLM 과 무관한 새 이유(N대 fan-out O(N) 워크로드)로 다시 비동기가 필요하다.
- `diagnostic_jobs` 는 비동기 상태머신(status pending/running/succeeded/failed, progress_stage, started_at, error_message, finished_at, active partial UNIQUE)을 이미 보유 — 스키마 변경 없이 재사용 가능.

## Options

### A. web 내부 메모리 백그라운드 (`asyncio.create_task` 즉시 처리)
emit 이 task 를 띄워 생성. 상태가 메모리에만 — ADR 0004 가 기각한 형태(재시작 시 in-flight 손실).

### B. 별도 워커 프로세스 + RabbitMQ 큐 (ADR 0004 정석)
emit 이 `report.generate` 발행, consumer 형태 워커가 큐 소비해 생성.
- 장점: web 격리, 큐 자연 분산, consumer 패턴 재활용.
- 단점: 보고서 생성 코드(query_service report 메서드 + mappers 10종 + view_models 7종 + report_serializer + 단위 helper, 약 4900+ LOC)가 web/services 에 강결합. consumer(BaseCollectRepository 만 의존, F4)가 쓰려면 web 표시계층 절반 이상을 web 비의존 패키지로 승격하고 web 이 거꾸로 import 하는 역전 구조 — 대공사이자 새 양방향 의존·회귀 위험. 워크로드가 LLM(수분 외부 호출)이 아니라 DB 집계 I/O(수초)라 큐 분리의 효용도 낮다.

### C. web 내부 job-claim 워커 (DB 상태머신, 채택)
emit 이 parent job 을 pending 으로 enqueue 만 하고 즉시 `?job={id}` 반환. web lifespan 의 백그라운드 task 가 `SELECT ... WHERE status='pending' FOR UPDATE SKIP LOCKED LIMIT 1` 로 claim(running 마킹) -> 기존 query_service 그대로 호출해 생성 -> succeeded.

## Decision

옵션 C 채택. 이는 옵션 A 부활이 아니라, 옵션 A 기각 사유(메모리 상태 손실)를 DB 상태머신으로 무효화한 변형이다.

근거:
1. in-flight 손실 0 — job 상태가 `diagnostic_jobs`(DB)에 있다. SIGTERM 시 running 으로 DB 에 남고, 기동 시 `recover_stale_running` 가 stale_seconds 초과 running 을 pending 으로 되돌려 다른 노드/재기동 노드가 재집는다.
2. 멀티노드 분산 — `FOR UPDATE SKIP LOCKED` 가 row-lock 으로 노드 경쟁을 안전 분산. 큐 없이 DB 가 조정자(F4 module-level instance 0 와 정합).
3. 추출 0 — 워커가 web 안에 있어 기존 query_service report 메서드를 그대로 호출. 옵션 B 의 대공사를 피한다.
4. 워크로드가 DB I/O 바운드(수초)라 web 프로세스 내 비동기 task 로 충분 — GIL 점유 경미.

## Architecture

발행 흐름:
- emit 라우터(`/reports/{environment,servers}/emit`) — `DiagnosticService.enqueue_report` 로 parent job pending INSERT(active UNIQUE 더블클릭 dedup) 후 즉시 `?job={id}` 반환.
- 워커(`web/report_worker.py`, lifespan `lifespan_worker`) — `claim_pending`(running 마킹) -> `report_generator.build_report_result_for_job`(query_service 호출, N대는 child 단일 보고서 N건 `emit_report` + selection 본문) -> `finish_succeeded` / 생성 불가 `finish_failed`.
- GET `?job={id}` — succeeded 면 정적 스냅샷, pending/running 이면 진행 화면 + `report-poll.js`(2초 폴링 -> 완료 시 reload), failed 면 안내. 폴링용 `GET /reports/{job_id}/status` JSON.

정합성:
- child 단일 단위 — parent task body 가 child N건 + selection 을 한 단위로 생성. child 전부 성공 후에만 parent succeeded, 중간 예외는 전파 -> 워커가 parent failed(부분 succeeded parent 차단).
- 멱등(D2 2단) — active partial UNIQUE(scope,input_hash,job_type) WHERE status IN(pending,running) 가 더블클릭을 차단(재발행 = 기존 job 합류). claim 의 SKIP LOCKED 가 동일 job 이중 처리 차단.
- graceful(F11) — lifespan 이탈 시 stop_event 로 새 claim 중단, 진행 중 1건은 shutdown_timeout 안 drain, 미완은 running 잔류 -> 다음 기동 recover_stale 회수.

상태/설정:
- `WebSettings.report_worker_poll_interval_sec`(2.0) / `report_worker_stale_seconds`(600) / `report_worker_shutdown_timeout_sec`(10.0).
- repo 신규 `claim_next_pending` / `mark_failed` / `recover_stale_running`(`BaseDiagnosticRepository` 추상 포함).

## Consequences

### 긍정
- 발행 응답이 즉시(job_id 반환) — N 증가에도 사용자 응답 시간 일정. 생성은 백그라운드.
- 추출 0 으로 옵션 B 대공사·양방향 의존 위험 회피.
- in-flight 손실 0 / 멀티노드 분산 / graceful — 구조적 허점 차단을 DB 상태머신으로 달성.

### 부정·한계
- web 프로세스가 생성 부하를 짊어진다(요청과 완전 격리 아님). DB I/O 바운드라 경미하나, 생성 폭주 시 web 자원 경합 가능 — 그 시점에 옵션 B(큐 분리)로 재전환 트리거.
- 크래시 후 stale 재처리 시 이미 생성된 orphan child(succeeded)가 이력에 중복될 수 있다(데이터 정합성 허점 아님 — parent succeeded 시 child_jobs 는 최신 유효분, orphan 은 retention 정리). 한계는 tradeoffs T16.
- 쿼리 최적화 A2(aggregate/net 중복 제거)·A3(breakdown 배치)·A5(fan-out prefetch 배치)는 적용 — child fan-out 의 raws·breakdown·details 를 배치화해 백그라운드 생성 쿼리 절감. A4(trend)는 cpu/mem/disk 다른 테이블이라 단일 SQL 불가·서버별 시계열이라 배치 불가·gather 부작용으로 보류(trend·online redis 서버별 잔존). 상세 tradeoffs T16.

## 정정 대상 ADR (historical record 보존)

| ADR | 정정 내용 |
|-----|----------|
| ADR 0004 | 비동기 발행이 본 ADR 로 복원됨 — 단 LLM 큐 워커(옵션 B)가 아니라 web job-claim 워커(옵션 C, DB 상태머신). 옵션 A 기각 사유는 본 ADR 에서 DB 상태로 무효화. |
| ADR 0014 | 동기 즉시 succeeded 발행이 본 ADR 로 비동기(enqueue -> 워커 생성)로 전환. emit_report 는 워커의 child 단일 보고서 저장 경로로 유지. |
