"""`/api/right-sizing` 응답 계약 스키마 — 외부 자동화 타입 계약.

라우터가 `responses={200: {"model": RightSizingResponse}}` 로 OpenAPI 스키마를 문서화하고 거기서 TS 타입이
생성된다. 응답 검증·재구성에는 쓰지 않는다(frozen 계약 출력을 바꾸지 않는다) — 문서화 전용이다.

`TypedDict` 인 이유는 매퍼가 만드는 것이 dict 이기 때문이다. 같은 모양을 `BaseModel` 로 적어 두면 매퍼 쪽
dict 리터럴은 아무 검사도 받지 못하고, 두 선언이 어긋났는지는 테스트가 돌 때에야 드러난다. 여기 선언을
매퍼 반환 타입으로 달면 키 이름·타입이 컴파일 시점에 맞춰진다.

`NotRequired` 는 실제로 생략되는 키에만 붙는다 — 계약 규약은 "값이 없으면 키를 생략하지 않고 null" 이고,
그 예외는 `docs/reference/contracts/assessment-api.md` "필드 존재 대 값" 절이 명시한다.
"""

from typing import NotRequired, TypedDict

from pydantic import ConfigDict

from assessment_engine.domain.right_sizing import Recommendation, ResourceStatus

# extra=forbid — 매퍼가 계약 밖 키를 얹으면 검증 테스트가 잡는다. OpenAPI 로는 additionalProperties:false.
_CONTRACT = ConfigDict(extra="forbid")


class RsSaturation(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    signal: str | None
    value: float | None
    threshold: float | None
    unit: str | None
    measured: bool | None
    saturated: bool | None


class RsNetSignal(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    value: float | None
    threshold: float | None
    exceeded: bool | None
    measured: bool | None


class RsNetSignals(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    retransmit_pct: RsNetSignal
    drop_pct: RsNetSignal
    conntrack_ratio: RsNetSignal


class RsNetwork(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    status: ResourceStatus | None
    status_label: str | None
    congested: bool | None
    detail: str | None
    signals: RsNetSignals


class RsAction(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    resource: str | None
    op: str | None
    target_display: str | None
    # 사이징 목표(under/over 축) — 자원 종류별 하나만 present(타입 키로 파싱). tier_up 등은 부재.
    target_cores: NotRequired[int | None]
    target_mb: NotRequired[int | None]
    target_gb: NotRequired[int | None]


class RsRecommendation(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    summary: str | None
    kind: str | None
    actions: list[RsAction] | None
    suppressed: list[RsAction] | None


class RsCpuResource(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    status: ResourceStatus | None
    status_label: str | None
    utilization_p95_pct: float | None
    saturation: RsSaturation | None
    evidence: list[str] | None
    confidence_notes: list[str] | None
    current_cores: int | None
    sizing_target_cores: int | None
    recommendation: str | None
    detail: str | None


class RsMemoryResource(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    status: ResourceStatus | None
    status_label: str | None
    utilization_p95_pct: float | None
    saturation: RsSaturation | None
    evidence: list[str] | None
    confidence_notes: list[str] | None
    current_mb: int | None
    sizing_target_mb: int | None
    recommendation: str | None
    detail: str | None


class RsDiskCapacity(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    status: ResourceStatus | None
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


class RsDiskIo(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    status: ResourceStatus | None
    status_label: str | None
    saturation: RsSaturation | None
    evidence: list[str] | None
    confidence_notes: list[str] | None
    detail: str | None


class RsDisk(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    capacity: RsDiskCapacity
    io: RsDiskIo


class RsResources(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    cpu: RsCpuResource
    memory: RsMemoryResource
    disk: RsDisk


class RightSizingServer(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    public_id: str | None
    hostname: str | None
    hostname_ambiguous: bool | None
    primary_ip: str | None
    os_family: str | None
    online: bool | None
    classification: Recommendation | None
    classification_label: str | None
    root_cause: str | None
    confidence_notes: list[str] | None
    recommendation: RsRecommendation
    resources: RsResources
    network: RsNetwork


class RsWindow(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    days: int | None
    start: str | None
    end: str | None
    basis: str | None


class RsFilter(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    hostname: list[str] | None
    ip: list[str] | None
    public_id: list[str] | None
    pair: list[str] | None


class RsWarnings(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    ambiguous_hostnames: list[str] | None
    unresolved_pairs: list[str] | None


class RightSizingEnvelope(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    engine_id: str | None
    generated_at: str | None
    window: RsWindow
    filter: RsFilter
    warnings: RsWarnings
    count: int
    servers: list[RightSizingServer]


class RightSizingResponse(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    right_sizing: RightSizingEnvelope
