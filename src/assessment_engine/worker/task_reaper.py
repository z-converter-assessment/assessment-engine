"""install task reaper 루프 — deadline_at 경과 pending(install) 을 failure(timeout) 로 전이.

발행 경로(TaskService)도 emit 직전 만료분을 정리하지만 그건 lazy 라, 해당 서버에 다음 emit 이 없으면
pending 이 DB 에 영구 잔류한다. 여기서 emit 과 무관하게 전역으로 훑어 terminal 로 보낸다.

큐 메시지 자체는 broker x-message-ttl(= install_task_deadline_sec)로 만료된다 — 두 시점이 같은 창이라
창을 넘긴 task 는 배달도 안 되고, zombie 지연 실행이 생기지 않는다.
"""

from typing import TYPE_CHECKING

from loguru import logger

from assessment_engine.worker.lifecycle import sleep_or_stop

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession

    from assessment_engine.db.repositories.collect import CollectRepository


async def run_task_reaper(
    *,
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    collect_repo_factory: Callable[..., CollectRepository],
    interval_sec: float,
    stop_event: asyncio.Event,
) -> None:
    """reaper 메인 루프 — interval 마다 1 tick.

    session_factory 는 tick 마다 새로 열어 전이 UPDATE 트랜잭션을 분리한다.
    stop_event 가 set 되면 다음 점검에서 루프를 빠져나간다 — 진행 중 UPDATE 1건은 짧아
    보고서 루프 같은 drain 대기가 없다.
    """
    logger.info("install task reaper started interval={}s", interval_sec)
    while not stop_event.is_set():
        try:
            async with session_factory() as session:
                repo = collect_repo_factory(session)
                expired = await repo.expire_all_overdue_tasks()
                await session.commit()
            if expired:
                logger.info("install task reaper expired overdue pending n={}", expired)
        except Exception:  # noqa: BLE001  일시 DB 장애가 reaper 를 죽이면 안 되므로 좁히지 않는다
            logger.exception("install task reaper tick failed")
        await sleep_or_stop(stop_event, interval_sec)
    logger.info("install task reaper stopped")
