"""메트릭 표시 mapper — CollectionStatus / MetricSeries → ViewModel (P2).

메트릭 대시보드 본체 (Cpu/Mem/Swap/DiskIo/NetIo/MountDash snapshot) 합성은
`web/services/mappers/metrics_calculator.py` 책임이라 본 모듈 범위 밖.
"""

from typing import TYPE_CHECKING

from assessment_engine.web.view_models.metric import CollectionStatusItem, MetricSeriesItem

if TYPE_CHECKING:
    from assessment_engine.db.dtos.outbound import CollectionStatus, MetricSeries


def to_collection_status_item(dto: CollectionStatus, is_online: bool) -> CollectionStatusItem:
    return CollectionStatusItem(
        last_metric_at=dto.last_metric_at,
        last_inventory_at=dto.last_inventory_at,
        is_online=is_online,
    )


def to_metric_series_item(dto: MetricSeries) -> MetricSeriesItem:
    return MetricSeriesItem(
        collected_at=dto.collected_at,
        value=float(dto.value) if dto.value is not None else None,
        dimension=dto.dimension,
    )
