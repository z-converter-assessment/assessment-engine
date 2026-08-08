"""Server 도메인 추상 인터페이스 — inventory · storage · network · collection status."""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from assessment_engine.db.dtos.outbound import (
        CollectionStatus,
        NetworkWithIo,
        ServerDetail,
        ServerSummary,
        StorageWithUsage,
    )


class ServerQueryRepository(Protocol):
    async def resolve_server_id(self, public_id: str) -> int | None: ...
    async def resolve_server_ids(self, public_ids: list[str]) -> dict[str, int]:
        """N개 public_id → {public_id: server_id} 단일 SQL. 미존재 public_id는 dict에서 누락."""
        ...

    async def get_servers(self, server_ids: list[int]) -> list[ServerDetail]:
        """N개 server_id → ServerDetail 단일 SQL. 순서는 DB 임의 — caller 정렬 책임."""
        ...

    async def list_server_public_ids(self) -> list[str]: ...

    async def list_servers(
        self,
        page: int,
        limit: int,
        search: str | None,
    ) -> list[ServerSummary]: ...
    async def get_server(self, server_id: int) -> ServerDetail | None: ...
    async def get_storage(self, server_id: int) -> StorageWithUsage | None: ...
    async def get_network(self, server_id: int) -> NetworkWithIo | None: ...
    async def get_collection_status(self, server_id: int) -> CollectionStatus | None: ...
    async def list_server_ids(self, limit: int | None = 1000) -> list[int]: ...

    async def get_latest_metric_at(self) -> datetime | None: ...
