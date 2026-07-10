# ADR 0055 — 전용 백그라운드 워커 컨테이너 분리 (web = HTTP 전담)

상태: Accepted (2026-07-10)

ADR 0040(비동기 보고서 job-claim 워커)·0051(install task lifecycle)의 프로세스 배치를 개정한다 — 결정(DB 상태머신·비동기·reaper)은 유지, 실행 위치만 web lifespan -> 전용 프로세스로 이전.

## Context

ADR 0040 은 보고서 생성 job-claim 워커를, ADR 0051 은 install task reaper 를 web 프로세스 lifespan 안 백그라운드 asyncio task 로 구동했다(옵션 C). 당시 근거는 "보고서 생성 코드가 web/services 강결합이라 consumer 로 위임하면 대공사"였고, 그건 지금도 유효하다.

문제는 배치다. 두 루프가 web 프로세스 안에서 돌아 다음을 유발한다.
- 생성 부하가 HTTP 요청 처리와 자원(이벤트 루프·DB 커넥션·메모리)을 공유 — 50대 동시 engineer 보고서 발행 같은 무거운 생성이 web 응답성을 압박(ADR 0040 "포기한 것"에 명시된 한계).
- 확장 축이 묶임 — 생성 처리량을 늘리려면 web 을 늘려야 하고, web 을 늘리면 job-claim 워커도 같이 늘어(중복 claim 은 SKIP LOCKED 로 안전하나 스케일 단위가 강제 결합).
- 12-factor VI(무상태 프로세스)·VIII(동시성=프로세스 모델) 관점에서 HTTP server 와 background worker 는 서로 다른 프로세스 타입이라 별도 스케일·배포가 정석.

consumer 는 이미 독립 프로세스(별도 컨테이너, `python -m assessment_engine.consumer`)로 이 패턴을 따른다. 워커만 web 에 얹혀 있는 게 비대칭이었다.

## Decision

보고서 생성 루프 + install reaper 루프를 web 에서 떼어 전용 워커 프로세스 `assessment_engine.worker` 로 분리한다.

- `worker/main.py` = composition root. DiagnosticService·QueryService·Repository 구체 인스턴스를 구성하고, `run_report_worker` 와 `run_task_reaper` 를 공유 `stop_event` 로 병행 구동(`asyncio.create_task` + `graceful_drain`). consumer 와 동일한 asyncio-native SIGTERM 처리(`loop.add_signal_handler(sig, stop_event.set)`, `signal.signal`·`os._exit` 미사용).
- 루프 모듈(`worker/report_worker.py`·`worker/task_reaper.py`·`worker/worker_lifecycle.py`)을 web/ 에서 worker/ 로 이전. web-only 래퍼였던 `lifespan_worker`·`lifespan_task_reaper` 컨텍스트 매니저는 제거(worker/main.py 가 루프를 직접 오케스트레이션).
- web/main.py lifespan 은 HTTP 인프라(broker channel·http_client·redis pool)만 구성 — 워커 배선 제거.
- 설정 분리: `WorkerSettings`(WebSettings 상속)가 `report_worker_*`·`install_reaper_*` 를 소유, `worker/settings.py` 가 composition root(F4 6번째 위치). `install_task_deadline_sec` 는 web(TaskService 발행)이 쓰므로 WebSettings 잔류.
- docker-compose `worker` 서비스 추가(consumer 미러링 — `<<: *app-base`, `command: ["assessment_engine.worker"]`, dev override watchfiles hot-reload). broker 미사용이나 postgres/redis/migrate depends_on 은 app-base 상속.

단일 OCI 이미지에 command 로 프로세스 타입을 가르는 기존 모델 유지(web/consumer/worker/migrate 동일 이미지). worker 가 `web.services.*`(DiagnosticService·QueryService·report_generator, 공유 application 계층)를 import 하는 건 같은 이미지 전제라 무해 — 패키지 추출은 하지 않는다(ADR 0040 이 기각한 대공사 그대로).

## Consequences

- 생성 부하가 web HTTP 처리와 프로세스 격리 — ADR 0040 "포기한 것"의 web 자원 경합 한계 해소. 워커를 web 과 독립으로 스케일·재시작 가능.
- graceful(F11) 보장 유지 — SIGTERM 시 두 루프가 공유 stop_event 로 종료, 진행 중 보고서 1건은 shutdown timeout 안 drain, 미완은 running 잔류 후 다음 기동 `recover_stale` 회수(in-flight 손실 0). job/task 상태는 DB 라 프로세스 분리로 인한 손실 없음.
- 멀티노드 분산은 그대로(FOR UPDATE SKIP LOCKED) — worker 를 여러 replica 로 띄워도 중복 claim 안전.
- 배포 토폴로지에 프로세스 타입 1개 추가(web·consumer·worker). 단일 호스트 compose 는 서비스 1개 증가, 자원 프로파일은 경미(DB I/O 바운드).
- worker->web.services 패키지 의존은 단일 이미지 전제에 묶임 — web/services 를 중립 패키지로 추출하려면 별도 ADR(현재는 불필요, 런타임 무해).
