"""task.result 메시지 핸들러 — agent worker 가 task 실행 종료 후 발행."""

from typing import TYPE_CHECKING
from uuid import UUID

from loguru import logger

from assessment_engine.consumer.handlers._common import _check_idempotent, _db_retry, _in_message_context
from assessment_engine.consumer.schemas import TaskResultInput
from assessment_engine.consumer.task_policy import effective_task_result
from assessment_engine.db.dtos.inbound import TaskResultUpdate

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from assessment_engine.consumer.handlers._types import MessageHandler
    from assessment_engine.db.repositories.collect import CollectRepository


def make_task_result_handler(
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], CollectRepository],
    redis: Redis,
    success_exit_codes: Mapping[str, Sequence[int]],
) -> MessageHandler:
    """Task 결과를 정책으로 보정해 저장하는 핸들러를 만든다.

    UUID가 아니거나 존재하지 않는 task_id는 재시도하지 않고 ack한다.
    """

    async def _store(data: TaskResultInput) -> None:
        if not await _check_idempotent(redis, data.message_id):
            logger.info("task_result duplicate skipped message_id={}", data.message_id)
            return

        try:
            # task_id 는 wire 계약상 free string 이지만 tasks.public_id 는 uuid 컬럼이라, 비 UUID 값은

            task_public_id = str(UUID(data.task_id))
        except ValueError:
            logger.warning("task_result task_id not uuid, unmatchable (silent ack) message_id={}", data.message_id)
            return

        async def commit(repo: CollectRepository) -> tuple[bool, str, str | None]:

            eff_status, eff_reason = effective_task_result(
                status=data.status,
                failure_reason=data.failure_reason,
                exit_code=data.exit_code,
                os_family=data.os_family,
                os_version=data.os_version,
                os_id=data.os_id,
                success_exit_codes=success_exit_codes,
                task_policy=data.task_policy,
            )
            update = TaskResultUpdate(
                public_id=task_public_id,
                status=eff_status,
                failure_reason=eff_reason,
                exit_code=data.exit_code,
                signal_no=data.signal_no,
                task_policy=data.task_policy,
                duration_ms=data.duration_ms,
                stdout_tail=data.stdout_tail,
                stderr_tail=data.stderr_tail,
                completed_at=data.completed_at,
            )
            ok = await repo.complete_task(update)
            return ok, eff_status, eff_reason

        updated, eff_status, eff_reason = await _db_retry(session_factory, repo_factory, commit)
        if not updated:
            logger.warning("task_result for unknown task_id={} (silent ack)", data.task_id)
            return

        if eff_status != data.status:
            logger.info(
                "task_result status remapped task_id={} {}->{} exit_code={}",
                data.task_id,
                data.status,
                eff_status,
                data.exit_code,
            )
        logger.info(
            "task_result stored task_id={} status={} failure_reason={} composite_id={}",
            data.task_id,
            eff_status,
            eff_reason,
            data.composite_id,
        )

    return _in_message_context(TaskResultInput, "task_result", _store)
