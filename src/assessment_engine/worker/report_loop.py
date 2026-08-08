"""비동기 보고서 생성 워커 루프 — 전용 워커 프로세스(assessment_engine.worker)가 구동.

발행(emit)은 parent job 을 pending 으로 enqueue 만 하고 즉시 반환하고, 본 워커가 claim 해서 생성한다.
job 상태가 DB(diagnostic_jobs)에 있어 in-flight 손실이 없다 — 멀티노드 분산은 `claim_pending` 의
FOR UPDATE SKIP LOCKED 가, SIGTERM 으로 미완인 건은 다음 기동 `recover_stale` 회수가 담당한다.

구체 인스턴스(QueryService·DiagnosticService)는 composition root(worker/main.py)가 구성해 주입.
"""

from typing import TYPE_CHECKING

from loguru import logger

from assessment_engine.web.services.report import (
    ReportGenerationError,
    build_report_result_for_job,
)
from assessment_engine.worker.lifecycle import sleep_or_stop

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from assessment_engine.db.dtos.outbound import DiagnosticJobRecord
    from assessment_engine.web.services.diagnostic_service import DiagnosticService
    from assessment_engine.web.services.query import QueryService


async def _process_one(
    diag_service: DiagnosticService,
    query_service_factory: Callable[[], AbstractAsyncContextManager[QueryService]],
    rec: DiagnosticJobRecord,
) -> None:
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
        # raw 예외는 traceback 로그로만 남기고 사용자 노출 error_message 는 sanitize (#F8).
        logger.exception("report job failed (internal) job_id={}", rec.id)
        await diag_service.finish_failed(rec.id, "internal error")


async def run_report_loop(
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
        # 기동 시 DB 일시 장애면 복구를 포기하고 루프에 진입한다 — 이후 claim 이 재시도한다.
        logger.exception("report worker stale recovery failed at startup")
    logger.info("report worker started poll_interval={}s stale={}s", poll_interval_sec, stale_seconds)

    while not stop_event.is_set():
        try:
            rec = await diag_service.claim_pending()
        except Exception:  # noqa: BLE001  루프를 죽이지 않는 것이 목적이라 좁히지 않는다
            logger.exception("report worker claim failed")
            await sleep_or_stop(stop_event, poll_interval_sec)
            continue
        if rec is None:
            await sleep_or_stop(stop_event, poll_interval_sec)
            continue
        try:
            await _process_one(diag_service, query_service_factory, rec)
        except Exception:  # noqa: BLE001  루프를 죽이지 않는 것이 목적이라 좁히지 않는다
            logger.exception("report worker process failed job_id={}", rec.id)

    logger.info("report worker stopped")
