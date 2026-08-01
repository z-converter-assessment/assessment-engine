"""task.result 메시지 핸들러 — agent worker 가 task 실행 종료 후 발행."""

from collections.abc import Callable, Coroutine, Mapping, Sequence
from typing import Any

from aio_pika.abc import AbstractIncomingMessage
from loguru import logger
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assessment_engine.consumer.handlers._common import _check_idempotent, _db_retry, _format_validation_err
from assessment_engine.consumer.schemas import TaskResultInput
from assessment_engine.db.dtos.inbound import TaskResultUpdate
from assessment_engine.db.repositories.base_collect_repository import BaseCollectRepository
from assessment_engine.task_policy import effective_task_result


def make_task_result_handler(
    session_factory: async_sessionmaker[AsyncSession],
    repo_factory: Callable[[AsyncSession], BaseCollectRepository],
    redis: Redis,
    success_exit_codes: Mapping[str, Sequence[int]],
) -> Callable[[AbstractIncomingMessage], Coroutine[Any, Any, None]]:
    """작업 결과 수신 핸들러.

    흐름: 멱등성 → 성공 보정 정책(task_policy) → DB UPDATE (status / completed_at /
    failure_reason / exit_code / duration_ms / stdout_tail / stderr_tail).
    task_id 미존재 시 silent ack (운영자가 task 삭제했을 가능성 — DLQ 부적합).
    boot_time / agent_started_at은 본 메시지에서 항상 null이라 time invariant 검증 생략.
    exit_code 는 raw 보존(감사용), status/failure_reason 만 정책 보정 결과로 저장.
    """

    async def _handle(message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                data = TaskResultInput.model_validate_json(message.body)
            except ValidationError as e:
                detail = _format_validation_err(e)
                logger.error("task_result parse error {}", detail)
                # 핸들러 밖으로 빠져나간 예외는 asyncio 가 전문을 출력한다 — 실패 필드의 입력값을
                # 문자열에 싣는 원본 대신, nack 에 필요한 만큼만 담은 예외로 바꿔 던진다.
                raise ValueError(f"task_result validation failed: {detail}") from None

            if not await _check_idempotent(redis, data.message_id):
                logger.info("task_result duplicate skipped message_id={}", data.message_id)
                return

            async def commit(repo: BaseCollectRepository) -> tuple[bool, str, str | None]:
                # 성공 보정 매칭 OS — agent 가 task.result 에 os 필드를 직접 발행 (inventory 조회 불요).
                eff_status, eff_reason = effective_task_result(
                    status=data.status,
                    failure_reason=data.failure_reason,
                    exit_code=data.exit_code,
                    os_family=data.os_family,
                    os_version=data.os_version,
                    os_id=data.os_id,
                    success_exit_codes=success_exit_codes,
                    task_policy=data.task_policy,  # 판정 1순위 — exit_code 보다 우선
                )
                update = TaskResultUpdate(
                    public_id=str(data.task_id),
                    status=eff_status,
                    failure_reason=eff_reason,
                    exit_code=data.exit_code,
                    signal_no=data.signal_no,
                    task_policy=data.task_policy,  # raw 보존 (감사·표시)
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

    return _handle
