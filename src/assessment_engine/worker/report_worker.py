"""비동기 보고서 생성 워커 루프 — 전용 워커 프로세스(assessment_engine.worker)가 구동.

발행(emit)은 parent job 을 pending 으로 enqueue 만 하고 즉시 반환 -> 본 워커가 claim 해서 생성한다.
job 상태가 DB(diagnostic_jobs)에 있어 메모리 상태 기반(ADR 0004 옵션 A)의 in-flight 손실 문제가 없다:
- 멀티노드 분산 = `claim_pending` 의 FOR UPDATE SKIP LOCKED (row-lock, 큐 없이 DB 가 조정).
- graceful(F11) = stop_event 로 새 claim 중단, 진행 중 1건은 shutdown timeout 안 완료 시도, 미완은
  running 으로 남겨 다음 기동 `recover_stale` 가 pending 으로 회수.

구체 인스턴스(QueryService·DiagnosticService)는 composition root(worker/main.py)가 구성해 주입.
"""

from typing import TYPE_CHECKING

from loguru import logger

from assessment_engine.web.services.report_generator import (
    ReportGenerationError,
    build_report_result_for_job,
)
from assessment_engine.worker.worker_lifecycle import sleep_or_stop

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from assessment_engine.db.dtos.outbound import DiagnosticJobRecord
    from assessment_engine.web.services.diagnostic_service import DiagnosticService
    from assessment_engine.web.services.query_service import QueryService


async def _process_one(
    diag_service: DiagnosticService,
    query_service_factory: Callable[[], AbstractAsyncContextManager[QueryService]],
    rec: DiagnosticJobRecord,
) -> None:
    """claim 된 job 1건 생성·저장. 생성 불가 -> failed, 내부 예외 -> failed(워커 격리)."""
    try:
        async with query_service_factory() as query_service:
            result = await build_report_result_for_job(query_service, diag_service, rec)
        await diag_service.finish_succeeded(rec.id, result)
        logger.info("report job generated job_id={} scope={}", rec.id, rec.scope)
    except ReportGenerationError as e:
        # 도메인 사유(등록 서버 0·유효 id 0 등) — 운영자에게 그대로 노출 가능(PII 아님).
        await diag_service.finish_failed(rec.id, str(e))
        logger.info("report job failed (generation) job_id={} reason={}", rec.id, str(e))
    except Exception:  # noqa: BLE001  루프를 죽이지 않는 것이 목적이라 좁히지 않는다
        # 워커 격리 — 한 job 실패가 루프를 중단시키면 안 됨(F6 except Exception 예외: reraise 시 워커 사망).
        # raw 예외는 traceback 로그만, 사용자 노출 error_message 는 sanitize(F8).
        logger.exception("report job failed (internal) job_id={}", rec.id)
        await diag_service.finish_failed(rec.id, "internal error")


async def run_report_worker(
    *,
    diag_service: DiagnosticService,
    query_service_factory: Callable[[], AbstractAsyncContextManager[QueryService]],
    poll_interval_sec: float,
    stale_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    """워커 메인 루프 — 기동 시 stale 복구 1회 후 pending job 을 polling claim·생성.

    query_service_factory: job 마다 독립 세션의 QueryService 를 yield (생성 쿼리 트랜잭션 분리).
    stop_event: SIGTERM 시 set — 루프가 다음 점검에서 종료.
    """
    try:
        recovered = await diag_service.recover_stale(stale_seconds)
        if recovered:
            logger.info("report worker recovered stale running jobs n={}", recovered)
    except Exception:  # noqa: BLE001  루프를 죽이지 않는 것이 목적이라 좁히지 않는다
        # 기동 시 DB 일시 장애 — 워커를 죽이지 않고 루프 진입(claim 이 이후 재시도). F6 격리.
        logger.exception("report worker stale recovery failed at startup")
    logger.info("report worker started poll_interval={}s stale={}s", poll_interval_sec, stale_seconds)

    while not stop_event.is_set():
        try:
            rec = await diag_service.claim_pending()
        except Exception:  # noqa: BLE001  루프를 죽이지 않는 것이 목적이라 좁히지 않는다
            # claim 자체 실패(DB 일시 장애 등) — 루프 유지, poll 후 재시도.
            logger.exception("report worker claim failed")
            await sleep_or_stop(stop_event, poll_interval_sec)
            continue
        if rec is None:
            await sleep_or_stop(stop_event, poll_interval_sec)
            continue
        try:
            await _process_one(diag_service, query_service_factory, rec)
        except Exception:  # noqa: BLE001  루프를 죽이지 않는 것이 목적이라 좁히지 않는다
            # _process_one 은 생성 예외를 내부 격리하나 finish_succeeded/finish_failed 의 DB 호출은 전파 가능 —
            # 루프에서 방어(워커 사망 방지). job 은 running 잔류 -> 다음 기동 recover_stale 가 회수(F6 격리).
            logger.exception("report worker process failed job_id={}", rec.id)

    logger.info("report worker stopped")
