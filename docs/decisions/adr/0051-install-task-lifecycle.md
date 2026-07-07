# 0051. install task lifecycle — 오프라인 비차단 advisory + deadline<->큐 TTL 정합 + reaper

## Status

Accepted

## Context

task.install 은 운영자가 등록 호스트에 발행하는 원격 설치 명령이다 (ADR 0007 별도 큐 모델). 대상 호스트는
오프라인일 수 있고(OpenStack VM 재부팅·중단), agent worker 가 무응답일 수 있다. 발행 -> DB 저장 -> 회신 경로에서
세 결정이 필요했다:

1. 오프라인 대상 처리 — 발행을 막을지, 그냥 durable 큐에 적재할지.
2. 두 타임아웃(engine task deadline vs broker 큐 메시지 TTL)의 관계.
3. 무회신 pending 을 언제·어떻게 terminal 상태로 만들지.

기존 문제: online 여부는 Redis `online:{id}` TTL 스냅샷(마지막 메트릭 수신 기준)이라 stale·racy 하다. `deadline_at`
이 `install_timeout_sec + margin`(약 11분)인데 큐 `x-message-ttl` 은 1h 라 엇갈렸다 — 엔진은 11분에 timeout 선언,
메시지는 49분 더 큐에 생존하다가 뒤늦게 재접속한 agent 가 이미 실패 처리된 task 를 실행하는 zombie 지연 실행.
무회신 pending 은 다음 emit 때만 lazy 정리돼, 그 서버에 재발행이 없으면 영구 잔류.

## Decision

1. 오프라인은 발행 게이트가 아니라 advisory (비차단). engine 은 online/offline 무관하게 durable 큐에 발행한다
   (store-and-forward). liveness 는 stale 추정이라 배달 게이트로 쓰면 store-and-forward 를 스스로 버린다(잠깐 껐다
   켜지는 호스트가 재접속해도 못 받음). 대신 발행 시점 online 여부(`TaskService._online_targets`, Redis fail-open)를
   응답(`TaskCreated.target_online`)에 실어 운영자에게 알린다(informed consent, UI warn 토스트).

2. deadline 과 배달 TTL 을 단일 창으로 정합. `install_task_deadline_sec`(기본 3600) 하나가 engine `tasks.deadline_at`
   과 broker 큐 `x-message-ttl` 을 동시에 정한다 — 엔진이 timeout 선언하는 시점 == broker 가 미배달 메시지 버리는
   시점(zombie 지연 실행 0). 기본 3600 = 기존 큐 TTL 과 동일이라 기존 큐 재선언 충돌 없음. agent 실행 예산
   `install_timeout_sec`(600, payload `install.timeout_sec`)는 별개 개념 — 픽업 후 스크립트 wall-clock.

3. reaper 능동 정리. web lifespan `lifespan_task_reaper` 백그라운드 루프(`install_reaper_interval_sec`, 기본 60s)가
   `expire_all_overdue_tasks` 로 deadline 경과 pending 을 emit 무관하게 failure(timeout) 전역 전이한다. 발행 경로의
   lazy expire(`expire_overdue_tasks`)는 즉시성·busy 검증용 보조로 유지. task 상태는 DB(`tasks`)라 메모리 손실 0,
   SIGTERM 시 stop_event drain(진행 중 UPDATE 1건 즉시)·`signal.signal`/`os._exit` 금지 (#F11, ADR 0040 일관).

## Consequences

- 오프라인 호스트도 배달 창(1h) 안에 돌아오면 자동 설치·회신. 못 돌아오면 메시지 만료 + reaper timeout — 유령 pending 0.
- deadline<->TTL 정합으로 zombie 지연 실행 제거.
- 트레이드오프(T17): 배달 창이 online-but-crashed task 의 timeout 감지 지연을 결정 — 무회신 시 최대 1h. 온라인 실패는
  대개 agent 가 `task.result` failure 를 명시 발행하므로 실질 영향은 "agent 완전 소실" 케이스만 1h 대기.
- 코드: `web/services/task_service.py`(발행·advisory·deadline·큐 TTL) + `web/task_reaper.py` + `web/main.py` lifespan +
  `db/repositories/collect_repository.py::expire_all_overdue_tasks`(+ `BaseCollectRepository` 추상) + `config.py`
  (WebSettings: `install_task_deadline_sec`·`install_reaper_interval_sec`·`install_reaper_shutdown_timeout_sec`) +
  `static/js`(toast warn·advisory 표시). 큐 토폴로지는 ADR 0007 유지.
