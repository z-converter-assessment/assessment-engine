from dataclasses import dataclass


# ---------- Inbound ----------

@dataclass
class ServerInventoryCreate:
    pass


@dataclass
class ServerMetricCreate:
    pass


# ---------- Outbound ----------

@dataclass
class ServerListItemResponse:
    pass


@dataclass
class ServerResponse:
    pass


@dataclass
class StorageResponse:
    pass


@dataclass
class NetworkResponse:
    pass


@dataclass
class CollectionStatusResponse:
    pass


@dataclass
class ServerMetricResponse:
    pass


@dataclass
class MetricSeriesResponse:
    pass