from dataclasses import dataclass


# ---------- 서버 목록 ----------

@dataclass
class ServerListItem:
    pass


# ---------- 1차 상세 ----------

@dataclass
class DiskItem:
    pass


@dataclass
class NetworkItem:
    pass


@dataclass
class ServerDetailResponse:
    pass


# ---------- 2차 스토리지 ----------

@dataclass
class StorageSummary:
    pass


@dataclass
class DiskDetailItem:
    pass


@dataclass
class LvmItem:
    pass


@dataclass
class FilesystemItem:
    pass


@dataclass
class StorageDetailResponse:
    pass


# ---------- 2차 네트워크 ----------

@dataclass
class NetworkSummary:
    pass


@dataclass
class NetworkInterfaceItem:
    pass


@dataclass
class RouteItem:
    pass


@dataclass
class DnsItem:
    pass


@dataclass
class NetworkDetailResponse:
    pass


# ---------- 수집 상태 ----------

@dataclass
class CollectionStatusItem:
    pass


# ---------- 메트릭 ----------

@dataclass
class MetricLatestResponse:
    pass


@dataclass
class MetricSeriesItem:
    pass