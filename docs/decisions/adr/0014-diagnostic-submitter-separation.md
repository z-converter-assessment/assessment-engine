# ADR 0014 — Diagnostic 발행 책임 분리 (scheduler 노드 web 의존 끊기)

상태: Superseded — AI 진단(LLM narrative) 기능 폐기 (2026-06-14). `DiagnosticSubmitter` package·`diagnostic/submitter.py` 제거. 보고서 발행은 `web/services/diagnostic_service.py` 의 `emit_report` 가 broker 미경유 DB enqueue 로 직접 수행(발행 시점 정적 스냅샷 즉시 succeeded). input_hash·anchor helper(`_compute_hash`·`_normalize_anchor`)는 `diagnostic/report_result.py` 단일 진실. 동기 즉시 succeeded 발행은 ADR 0040 으로 비동기(enqueue -> web job-claim 워커 생성)로 전환 — `emit_report` 는 워커의 child 단일 보고서 저장 경로로 유지.

이전 상태: Accepted (2026-05-19), Refined by 0023 (2026-05-23) — scheduler 노드 폐기 후도 `DiagnosticSubmitter` 본질 유지. 본 ADR 본문 안 "scheduler 노드 의존 끊기" 모티브·`submitter` package 분리 정공은 historical record.

## Context

- `web/services/diagnostic_service.py` 의 `DiagnosticService.submit` (·`_publish`·`_build_input_params`·`_compute_hash`·`_normalize_anchor`) 가 진단 발행 단일 로직이었음.
- `diagnostic/scheduler.py` 가 본 클래스를 `from assessment_engine.web.services.diagnostic_service import DiagnosticService` 로 import — scheduler 노드가 web 서비스 계층 코드를 전부 따라 import.
- 멀티노드 배포에서 scheduler 노드는 진단 enqueue + publish 만 수행 — 보고서 합성·조회·이력 같은 web 서비스 책임이 불필요. 같은 wheel 1개 패키지라도 import 경계 흐림.
- 사용자 요청 (2026-05-19): "멀티노드 구조가 됐을 때 스케줄러와 진단 로직이 다른 노드로 갈려야 한다면 모듈 단위로 쪼개라."

## Decision

진단 발행 책임을 `assessment_engine.diagnostic.submitter` 신규 모듈로 추출. 디렉토리 분리는 불필요 (같은 `diagnostic` package 안 + scheduler 가 worker 측 모듈 `handler/aggregator/llm` import 안 함 — 이미 import-level 격리됨).

신규 모듈 (`src/assessment_engine/diagnostic/submitter.py`):
- `DiagnosticSubmitter` 클래스 — `submit`·`_publish` 메서드
- 예외 — `DiagnosticNotFound` / `DiagnosticBadRequest` / `DiagnosticRaceMiss`
- helpers — `_build_input_params` / `_compute_hash` / `_normalize_anchor` (input_hash 결정성 단일 진실)

`web/services/diagnostic_service.py`:
- `DiagnosticService` 는 `DiagnosticSubmitter` composition (`self._submitter = DiagnosticSubmitter(...)`) + `submit` 위임.
- 조회·이력·보고서 발행 기록 (`get_one`·`get_many`·`get_latest`·`get_many_latest_server`·`list_recent`·`list_reports`·`record_report_emission`·`to_panel_payload`·`_deserialize_record`) 는 본 service 단일 진실.
- 호환 re-export — 기존 라우터들이 `from assessment_engine.web.services.diagnostic_service import DiagnosticNotFound` 같은 import 그대로 동작 (exceptions·helpers identity 동일).

`diagnostic/scheduler.py`:
- `from assessment_engine.diagnostic.submitter import DiagnosticSubmitter, DiagnosticNotFound, DiagnosticRaceMiss` 직접 import.
- `web.services` import 0건 — scheduler 노드는 `db/`·`diagnostic/`·`log_config` 만 의존.
- `_build_service(session, broker_channel, redis)` → `_build_submitter(session, broker_channel)` 로 rename + redis 인자 제거 (submitter 가 redis 안 받음).

## Consequences

장점:
- scheduler 노드 import 그래프에서 `web.services` 패키지 전부 끊김 — 멀티노드 배포 시 import 경계 명확.
- `DiagnosticSubmitter` 가 독립 단위 — 단위 테스트 시 web service 의존 없이 검증 가능 (`tests/unit/test_diagnostic_service.py::test_submitter_re_export_identity` 회귀 가드).
- 호환 re-export 로 라우터·기존 호출자 변경 0건. exceptions identity 동일 검증.
- input_hash 결정성 (helpers) 이 진단 package 단일 위치 — web service 변경이 hash 식별성에 영향 없음.

단점:
- module-level wrapper 1단 추가 (`DiagnosticService.submit` → `DiagnosticSubmitter.submit`). 호출 stack 1 frame 늘어남 — 미미.
- helpers 가 `_` prefix 라 module-private 의도였으나 test/web 모두 import 사용 — Python convention 상 strict private 아닌 점 명시 (re-export 도 본 의도).

## 대안 검토

(a) 디렉토리 분리 (`diagnostic/scheduler/` + `diagnostic/worker/`) — 과도. scheduler 가 worker 측 `handler/aggregator/llm` 이미 import 안 함. import-level 격리 충분.

(b) helpers (`_build_input_params`/`_compute_hash`/`_normalize_anchor`) 만 추출, `submit` 자체는 web service 유지 — scheduler 가 여전히 web 의존. 의존 끊기 목표 미달.

(c) `DiagnosticSubmitter` 를 클래스가 아닌 module-level 함수 (`submit_diagnostic(query_repo, session_factory, ...)`) — 5개 의존성 매번 인자 전달 부담. 인스턴스 lifecycle 의도 명확성 떨어짐.

## 향후 작업

- LLM 활성 (ADR 0010 정정 시) 시 worker 측 변경 — submitter 영향 0.
- 본 ADR 의 패턴 (책임별 모듈 분리 + composition root 호환 re-export) 을 다른 서비스 (consumer·web) 분리 시에도 적용 검토.
