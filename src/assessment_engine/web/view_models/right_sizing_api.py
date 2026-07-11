"""/api/right-sizing 응답 계약 스키마 (Pydantic) — 외부 자동화 타입 계약.

라우터가 `responses={200: {"model": RightSizingResponse}}` 로 OpenAPI 스키마 문서화 -> 생성 TS 타입.
매퍼(services/mappers/right_sizing_api.py) build dict 의 단일 진실 미러. 응답 검증/재구성 안 함(frozen
계약 출력 불변) — 문서화 전용. extra=forbid 로 골든 테스트가 매퍼<->스키마 drift 를 잡는다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RsSaturation(_Contract):
    signal: str | None
    value: float | None
    threshold: float | None
    unit: str | None
    measured: bool | None
    saturated: bool | None


class RsNetSignal(_Contract):
    value: float | None
    threshold: float | None
    exceeded: bool | None
    measured: bool | None


class RsNetSignals(_Contract):
    retransmit_pct: RsNetSignal
    drop_pct: RsNetSignal
    conntrack_ratio: RsNetSignal


class RsNetwork(_Contract):
    status: str | None
    status_label: str | None
    congested: bool | None
    detail: str | None
    signals: RsNetSignals


class RsAction(_Contract):
    resource: str | None
    op: str | None
    target_display: str | None
    # 사이징 목표(under/over 축) — 자원 종류별 하나만 present(타입 키로 파싱). tier_up 등은 부재.
    target_cores: int | None = None
    target_mb: int | None = None
    target_gb: int | None = None


class RsRecommendation(_Contract):
    summary: str | None
    kind: str | None
    actions: list[RsAction] | None
    suppressed: list[RsAction] | None


class RsCpuResource(_Contract):
    status: str | None
    status_label: str | None
    utilization_p95_pct: float | None
    saturation: RsSaturation | None
    evidence: list[str] | None
    confidence_notes: list[str] | None
    current_cores: int | None
    sizing_target_cores: int | None
    recommendation: str | None
    detail: str | None


class RsMemoryResource(_Contract):
    status: str | None
    status_label: str | None
    utilization_p95_pct: float | None
    saturation: RsSaturation | None
    evidence: list[str] | None
    confidence_notes: list[str] | None
    current_mb: int | None
    sizing_target_mb: int | None
    recommendation: str | None
    detail: str | None


class RsDiskCapacity(_Contract):
    status: str | None
    status_label: str | None
    worst_mount: str | None
    worst_mount_used_pct: float | None
    days_until_full: int | None
    evidence: list[str] | None
    confidence_notes: list[str] | None
    current_gb: int | None
    sizing_target_gb: int | None
    recommendation: str | None
    detail: str | None


class RsDiskIo(_Contract):
    status: str | None
    status_label: str | None
    saturation: RsSaturation | None
    evidence: list[str] | None
    confidence_notes: list[str] | None
    detail: str | None


class RsDisk(_Contract):
    capacity: RsDiskCapacity
    io: RsDiskIo


class RsResources(_Contract):
    cpu: RsCpuResource
    memory: RsMemoryResource
    disk: RsDisk


class RightSizingServer(_Contract):
    public_id: str | None
    hostname: str | None
    hostname_ambiguous: bool | None
    primary_ip: str | None
    os_family: str | None
    online: bool | None
    classification: str | None
    classification_label: str | None
    root_cause: str | None
    confidence_notes: list[str] | None
    recommendation: RsRecommendation
    resources: RsResources
    network: RsNetwork


class RsWindow(_Contract):
    days: int | None
    start: str | None
    end: str | None
    basis: str | None


class RsFilter(_Contract):
    hostname: list[str] | None
    ip: list[str] | None
    public_id: list[str] | None
    pair: list[str] | None


class RsWarnings(_Contract):
    ambiguous_hostnames: list[str] | None
    unresolved_pairs: list[str] | None


class RightSizingEnvelope(_Contract):
    engine_id: str | None
    generated_at: str | None
    window: RsWindow
    filter: RsFilter
    warnings: RsWarnings
    count: int
    servers: list[RightSizingServer]


class RightSizingResponse(_Contract):
    right_sizing: RightSizingEnvelope
