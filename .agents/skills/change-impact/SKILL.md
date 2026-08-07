---
name: change-impact
description: 시계열/inventory 컬럼, routing key, 메시지 schema, right-sizing, 환경변수, ViewModel, JSON API, 보고서, 외부 의존성, 차트, install task 변경의 동시 갱신 위치를 확인한다.
---

# 변경 영향도 체크리스트

원칙/적용 시점은 루트 `AGENTS.md` #F9 단일 진실. 본 스킬은 변경 유형별 동시 갱신 위치 표만 보유한다.

| 변경 유형 | 동시 갱신 위치 |
|-----------|----------------|
| 시계열 컬럼 추가 | (1) ORM 모델 (2) Alembic revision (3) Inbound DTO/mapper (4) Outbound DTO/mapper (5) `cache_serializer._DETAIL_DISPLAY_FIELDS` (6) ViewModel (7) 템플릿/외부 .js |
| inventory 컬럼 추가 | 시계열 (1)~(7) + agent payload 합의 + `docs/reference/contracts/agent-data.md` "데이터 형식" 절 (엔진 측 inbound DTO/핸들링 단일 진실) |
| 신규 routing key | (1) 발행 측 (agent 또는 engine web) 상수 (2) consumer 핸들러 팩토리 + dispatch (3) `docs/reference/rabbitmq.md` 토폴로지 표 (4) `docs/reference/contracts/agent-data.md` 메시지 타입 절 |
| `EXCHANGE`/`ROUTING_KEY_*` 값 변경 | (1) 발행 측 상수 (2) consumer subscriber dispatch (3) `docs/reference/rabbitmq.md` 토폴로지 표 |
| 메시지 페이로드 schema 변경 (필드 추가/삭제/rename/Literal 값 변경) | (1) `consumer/schemas.py` 또는 발행 측 payload 빌드 (2) Inbound DTO (3) handler 매핑 (4) DB 모델/Alembic revision (필요 시) (5) `docs/reference/contracts/agent-data.md` 데이터 형식 절 (6) 운영자 가시성 ViewModel/템플릿/API (필요 시) |
| `right_sizing.py` 분류 임계 변경 | (1) `right_sizing.py` 임계 상수 (2) #F10 평가 윈도우 정합 (3) `docs/reference/right-sizing-thresholds.md` 임계 수치/근거 (4) 판정 순서나 합성 규칙도 바뀌면 `docs/reference/right-sizing.md` |
| 분류 신호/OS 분기 (USE Method 축/임계/trigger) | (1) `right_sizing.py` `assess_cpu`/`assess_memory`/`assess_disk_capacity`/`assess_disk_io`/`assess_network`/`rollup_host`(근본원인)/임계 상수/`cpu_saturated`/`mem_saturated`/`disk_io_saturated`(os-aware) helper/`ResourceStats` 필드(`cpu_run_queue_p95`/`mem_pages_input_rate_p95`/`disk_inode_used_pct`/`conntrack_ratio` 등) (2) saturation 원자료면 `get_report_aggregate` SQL(cagg 집계 컬럼)/`ReportRowRaw`/`build_resource_stats` 배선 동시 (3) trigger 키 추가 시 report 진단(`_build_diagnosis`, host.resources 파생)/권고(`under_prescription`/`resource_prescription`)/attention 원인 라벨(`_CAUSE_LABEL_BY_TRIGGER`)/`saturation_axis_displays` 동시 갱신 (4) stats 생성은 `build_resource_stats` 공용(report/attention/서버목록/도넛 단일 진실) — 직접 해석/임계 재계산 금지 (5) 표시 N/A/confidence(`host_saturation_unmeasured` 포화 축 한정/`build_host_confidence_notes`) 마커 (6) `docs/reference/right-sizing.md`(명세/근거 단일 진실) + `docs/reference/web/services.md` "OS 분기" + `_thresholds_reference.html` |
| 환경변수 추가 | (1) `Settings` 필드 (2) `docs/reference/contracts/env.md` 카탈로그 (3) 루트 `docker-compose.yml` `environment:` (필요 시) (4) secret 분류면 `SecretStr` 타입 + `_validate_*_secrets` 검증 추가 + `docs/reference/contracts/env.md` 2절/7절 |
| ViewModel 파생 필드 추가 | (1) mapper 계산 (2) `cache_serializer._DETAIL_DISPLAY_FIELDS` (3) 템플릿 표시 (4) 동일 데이터 JSON API 응답이면 dataclass(P2) (5) JSON API 응답 ViewModel이면 `pnpm run codegen` 으로 `static/js/generated/api.ts` 재생성/커밋 + 소비 JS annotate (타입 계약 drift 게이트, #E6) |
| 신규 JSON API 엔드포인트 | (1) 라우터 return 어노테이션 선언 (생성 타입 원천) (2) `pnpm run codegen` -> `api.ts` 재생성/커밋 (3) 소비 JS 의 fetch 경계 응답을 생성 타입으로 annotate (4) E2 pagination 패턴(정적 row=page / 시간흐름=cursor) 택1 |
| 보고서 스냅샷 ViewModel nested 필드 추가/제거 (`EnvironmentReportSummary` 등 정적 스냅샷, #C1) | (1) ViewModel dataclass (2) mapper precompute (3) `report/serializer.*_from_dict` nested 복원 — spread 재구성은 `_build(cls, data)` 경유 의무(`_drop_unknown_fields` 항상 적용 -> 필드 제거 시 과거 스냅샷 잔존 키를 흡수, TypeError 500 방지. 명시 kwargs 재구성은 이미 제거 내성). datetime/IpAddr 재구성 누락 시 dict 잔류로 template `.attr` 런타임 깨짐 (4) 템플릿 `.attr` 접근 (5) 라운드트립 단위 테스트(`test_report_serializer`, 필드 제거 케이스 포함) |
| 신규 조건부(발화) UI 섹션 추가 | (1) 제목/카테고리 항상 노출 (2) 빈 상태 `empty_state` placeholder (3) 화면 컨텍스트 가드와 데이터 발화 가드 분리 (#E9) |
| 신규 외부 의존(HTTP/외부 큐) | (1) fail-open/close 결정(#F6) (2) timeout/재시도 정책 (3) Settings 필드 (4) #F6 매트릭스 갱신 |
| 신규 의존성(`pyproject.toml`) | (1) `uv.lock` 갱신 (2) PR 설명에 도입 사유 (3) 대형 의존성은 ADR 검토. 워크플로 단일 진실: `docs/guides/dependencies.md` |
| 신규 차트 MetricType (net/disk rate/gauge 등) | (1) `db/repositories/query/types.py` `MetricType` Literal (+ 환경 차트면 `EnvironmentMetricType` 도) (2) rate 메트릭이면 동 파일 `_RATE_PER_DIM_DEFS` (dim_col, value_col) 추가 / gauge 면 `metric_sql.py` 에 `_trend_*` builder 신설 후 `_TREND_PAIRS` 등재 — 누락/중복 둘 다 모듈 import 시점 `AssertionError` 라 기동이 막힌다(요청 시점 500 아님) (3) 페이지 JS fetch (env-metrics.js/metrics.js, seqs/Y축 suggestedMax 명명 상수/os-aware 분기) + 템플릿 차트 카드 (4) 가상 제외 필터(`device_filters`) 해당 시 표시 경계 적용 (gauge 는 미대상) (5) `test_query_repository._ALL_METRIC_TYPES` dispatch 커버 + 값 테스트 |
| 비동기 보고서 발행 (job-claim 워커/생성 디스패치/폴링) | (1) emit 라우터 `enqueue_report` 분리 (2) `report/generator.build_report_result_for_job` 생성 디스패치 (3) `worker/report_loop.py` 루프 + `worker/main.py` 기동/graceful (4) repo `claim_next_pending`/`mark_failed`/`recover_stale_running` (+ `DiagnosticRepository` 추상) (5) GET pending/running/failed 분기 + `GET /reports/{job}/status` + `report-poll.js` + `report_pending.html` (6) `WorkerSettings` 워커 설정 (7) #C1/#F11 (8) 단위테스트(`test_diagnostic_service`/`test_report_generator`/`test_worker`) |
| install task lifecycle (deadline/reaper/오프라인 advisory) | (1) `task_service` 발행 (`deadline_at`/`_online_targets` advisory/큐 `x-message-ttl`) (2) `worker/task_reaper.py` 루프 + `worker/main.py` (3) repo `expire_all_overdue_tasks` (+ `CollectRepository` 추상) (4) 설정 `install_task_deadline_sec`(WebSettings)/`install_reaper_interval_sec`/`install_reaper_shutdown_timeout_sec`(WorkerSettings) (5) 응답 `TaskCreated.target_online` + `static/js` warn 표시 (6) #F10/#F11 (7) `test_task_queries`/`test_worker` (expire/complete_task signal_no) |
