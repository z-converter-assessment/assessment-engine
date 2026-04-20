from abc import ABC, abstractmethod
from uuid import UUID

from app.dto.outbound import ServerDTO


class IWriteRepository(ABC):

    @abstractmethod
    async def get_or_create(self, hostname: str) -> ServerDTO: ...

    @abstractmethod
    async def insert_metric(
        self,
        server_id: UUID,
        nproc: int,
        mem_total_mb: int,
        disks: list,
        ip_internal: list,
        ip_external: list,
    ) -> None: ...