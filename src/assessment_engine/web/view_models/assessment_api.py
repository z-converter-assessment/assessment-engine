"""`/api/assessment` 응답 계약 스키마 — 외부(재해복구·마이그레이션 자동화) 타입 계약.

라우터가 `responses={200: {"model": AssessmentEnvelope}}` 로 OpenAPI 스키마를 문서화하고 거기서 TS 타입이
생성된다. 응답 검증·재구성에는 쓰지 않는다(frozen 계약 출력을 바꾸지 않는다) — 문서화 전용이다.

`TypedDict` 인 이유는 매퍼가 만드는 것이 dict 이기 때문이다. 같은 모양을 `BaseModel` 로 적어 두면 매퍼 쪽
dict 리터럴은 아무 검사도 받지 못하고, 두 선언이 어긋났는지는 테스트가 돌 때에야 드러난다. 여기 선언을
매퍼 반환 타입으로 달면 키 이름·타입이 컴파일 시점에 맞춰진다.

`NotRequired` 는 실제로 생략되는 키에만 붙는다 — 계약 규약은 "값이 없으면 키를 생략하지 않고 null" 이고,
그 예외(`sizing.axes` 축별 키 집합 차이)는 `docs/reference/contracts/assessment-api.md` 가 명시한다.
"""

from typing import Literal, NotRequired, TypedDict

from pydantic import ConfigDict

from assessment_engine.recommendation import Recommendation, ResourceStatus

# extra=forbid — 매퍼가 계약 밖 키를 얹으면 검증 테스트가 잡는다. OpenAPI 로는 additionalProperties:false.
_CONTRACT = ConfigDict(extra="forbid")


class AssessmentIdentity(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    public_id: str | None
    hostname: str | None
    hostname_ambiguous: bool | None
    primary_ip: str | None
    os_family: str | None
    online: bool | None


class ReproOs(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    family: str | None
    id: str | None
    version: str | None
    codename: str | None
    kernel: str | None
    arch: str | None
    bits: int | None
    boot_firmware: str | None
    secure_boot: bool | None
    edition: str | None
    timezone: str | None
    rtc_utc: bool | None


class ReproBoot(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    kernel_cmdline: str | None
    root_ref_type: str | None
    grub_install_target: str | None


class ReproAddress(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    address: str | None
    prefix: int | None
    family: str | None
    origin: str | None


class ReproRoute(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    dest: str | None
    via: str | None


class ReproInterface(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    id: str | None
    id_type: str | None
    name: str | None
    kind: str | None
    mtu: int | None
    addresses: list[ReproAddress] | None
    gateway: str | None
    dns: list[str] | None
    routes: list[ReproRoute] | None
    bond_mode: str | None
    vlan_id: int | None
    speed_mbps: int | None


class ReproNetwork(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    interfaces: list[ReproInterface] | None


class ReproBlockDevice(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    id: str | None
    id_type: str | None
    name: str | None
    type: str | None
    parent: str | None
    size_bytes: int | None
    partition_table: str | None
    sector_size: int | None
    serial: str | None
    wwn: str | None
    rotational: bool | None
    part_number: int | None
    part_start_bytes: int | None
    part_type: str | None
    part_name: str | None
    part_flags: list[str] | None
    fstype: str | None
    fs_uuid: str | None
    fs_label: str | None
    block_size: int | None
    mountpoint: str | None
    mount_options: list[str] | None
    fs_freq: int | None
    fs_passno: int | None
    lvm_vg: str | None
    lvm_lv: str | None
    lvm_segtype: str | None
    lvm_stripes: int | None
    lvm_stripe_size_kib: int | None
    raid_level: int | None
    raid_chunk_kib: int | None
    raid_metadata: str | None
    raid_uuid: str | None
    crypt_type: str | None


class ReproLvmVg(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    name: str | None
    vg_uuid: str | None
    size_bytes: int | None
    free_bytes: int | None
    extent_size_bytes: int | None
    pv_ids: list[str] | None


class ReproStorage(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    block_devices: list[ReproBlockDevice] | None
    lvm_vgs: list[ReproLvmVg] | None


class ReproMount(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    source: str | None
    target: str | None
    fstype: str | None
    options: list[str] | None
    fs_freq: int | None
    fs_passno: int | None


class Reproduction(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    os: ReproOs
    boot: ReproBoot
    network: ReproNetwork
    storage: ReproStorage
    mounts: list[ReproMount] | None


class SizingAxis(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    axis: str
    current: int | None
    recommended: int | None
    unit: str
    action: Literal["increase", "decrease", "keep"]
    estimate_quality: Literal["exact", "floor", "uncertain"]
    # disk 축 전용(cpu/memory 축엔 부재) — Optional 로 흡수.
    mountpoint: NotRequired[str | None]
    device_ref: NotRequired[str | None]
    used_pct: NotRequired[float | None]
    runway_days: NotRequired[int | None]
    note: NotRequired[str | None]


class Sizing(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    axes: list[SizingAxis]


class DataQuality(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    sufficient: bool | None
    notes: list[str] | None


class Assessment(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    classification: Recommendation | None
    confidence: Literal["low", "medium", "high"] | None
    data_quality: DataQuality


class DiagUtilization(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    eval_pct: float | None
    sizing_pct: float | None


class DiagSaturation(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    signal: str | None
    value: float | None
    threshold: float | None
    unit: str | None
    measured: bool | None
    saturated: bool | None


class DiagResource(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    axis: str | None
    status: ResourceStatus | None
    utilization: DiagUtilization
    saturation: DiagSaturation | None
    confidence_notes: list[str] | None


class DiagAdvisory(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    disk_io_tier_hint: str | None
    network_congested: bool | None


class Diagnostics(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    root_cause: str | None
    root_cause_detail: str | None
    resources: list[DiagResource] | None
    advisory: DiagAdvisory


class AssessmentServer(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    identity: AssessmentIdentity
    reproduction: Reproduction
    sizing: Sizing
    assessment: Assessment
    diagnostics: Diagnostics


class AssessmentWindow(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    days: int | None
    start: str | None
    end: str | None
    basis: str | None


class AssessmentFilter(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    hostname: list[str] | None
    ip: list[str] | None
    public_id: list[str] | None
    pair: list[str] | None


class AssessmentWarnings(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    ambiguous_hostnames: list[str] | None
    unresolved_pairs: list[str] | None
    unmatched_filters: list[str] | None


class AssessmentEnvelope(TypedDict):
    __pydantic_config__ = _CONTRACT  # type: ignore[misc]

    contract_version: str
    generated_at: str
    window: AssessmentWindow
    filter: AssessmentFilter
    warnings: AssessmentWarnings
    count: int
    servers: list[AssessmentServer]
