from app.broker import broker
from app.dto.inbound import ServerMetricInput
from app.session import AsyncSessionLocal
from app.repositories.write_repository import WriteRepository


@broker.task
async def process_metric(payload: ServerMetricInput) -> None:
    async with AsyncSessionLocal() as session:
        repo = WriteRepository(session)
        server = await repo.get_or_create(payload.hostname)
        await repo.insert_metric(
            server_id=server.id,
            nproc=int(payload.nproc),
            mem_total_mb=payload.mem_total_mb,
            disks=[d.model_dump() for d in payload.disks],
            ip_internal=payload.ip.internal,
            ip_external=payload.ip.external,
        )
        await session.commit()