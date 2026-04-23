from abc import ABC, abstractmethod
from typing import Optional


class ICollectRepository(ABC):

    # TODO: hostname 대신 에이전트가 생성한 UUID로 식별하도록 변경
    @abstractmethod
    async def find_server(self, hostname: str) -> Optional[int]: ...

    @abstractmethod
    async def create_server(self, hostname: str) -> int: ...

    @abstractmethod
    async def insert_metric(
        self,
        server_id: int,
        nproc: int,
        mem_total_mb: int,
        disks: list,
        ip_internal: list,
        ip_external: list,
    ) -> None: ...
