import asyncio

from app.celery_app import celery_app
from app.contracts.task_names import PROCESS_METRIC
from app.workers.database import WorkerSession
from app.repositories.server import ServerRepository


@celery_app.task(name=PROCESS_METRIC, bind=True, max_retries=3)
def process_metric(
    self,
    hostname: str,
    nproc: str | None,
    free: dict | None,
    lsblk_raw: list,
    ip_raw: dict,
):
    asyncio.run(_process_metric_async(hostname, nproc, free, lsblk_raw, ip_raw))


async def _process_metric_async(
    hostname: str,
    nproc: str | None,
    free: dict | None,
    lsblk_raw: list,
    ip_raw: dict,
) -> None:
    async with WorkerSession() as session:
        repo = ServerRepository(session)
        server = await repo.get_or_create(hostname)
        await repo.insert_metric(
            server_id=server.id,
            nproc=int(nproc) if nproc else None,
            mem_total_mb=free.get("mem_total_mb") if free else None,
            disks=lsblk_raw or [],
            ip_internal=ip_raw.get("internal", []),
            ip_external=ip_raw.get("external", []),
        )
        await session.commit()