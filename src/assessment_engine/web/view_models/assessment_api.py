"""/api/assessment 응답 계약 스키마 (Pydantic) — 외부(재해복구·마이그레이션 자동화) 타입 계약.

라우터가 `responses={200: {"model": AssessmentEnvelope}}` 로 OpenAPI 스키마를 문서화 -> 생성 TS 타입.
매퍼(services/mappers/assessment_api.py)가 build 하는 dict 구조의 단일 진실 미러. 계약 규약(assessment-api.md):
모든 키 항상 존재, 값이 없으면 null. 따라서 필드는 present + nullable(`X | None`). extra=forbid 로 골든
테스트(test_assessment_api_schema)가 매퍼 dict 와의 drift(신규/누락 키)를 잡는다.

주의: 본 스키마는 응답 검증/재구성에 쓰지 않는다(response_model 아님) — frozen 배포 계약 출력을 바꾸지
않기 위해 문서화 전용. sizing.axes 이질(cpu/mem vs disk)은 disk 전용 필드를 Optional 로 흡수.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssessmentIdentity(_Contract):
    public_id: str | None
    hostname: str | None
    hostname_ambiguous: bool | None
    primary_ip: str | None
    os_family: str | None
    online: bool | None


class ReproOs(_Contract):
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


class ReproBoot(_Contract):
    kernel_cmdline: str | None
    root_ref_type: str | None
    grub_install_target: str | None


class ReproAddress(_Contract):
    address: str | None
    prefix: int | None
    family: str | None
    origin: str | None


class ReproRoute(_Contract):
    dest: str | None
    via: str | None


class ReproInterface(_Contract):
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


class ReproNetwork(_Contract):
    interfaces: list[ReproInterface] | None


class ReproBlockDevice(_Contract):
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


class ReproLvmVg(_Contract):
    name: str | None
    vg_uuid: str | None
    size_bytes: int | None
    free_bytes: int | None
    extent_size_bytes: int | None
    pv_ids: list[str] | None


class ReproStorage(_Contract):
    block_devices: list[ReproBlockDevice] | None
    lvm_vgs: list[ReproLvmVg] | None


class ReproMount(_Contract):
    source: str | None
    target: str | None
    fstype: str | None
    options: list[str] | None
    fs_freq: int | None
    fs_passno: int | None


class Reproduction(_Contract):
    os: ReproOs
    boot: ReproBoot
    network: ReproNetwork
    storage: ReproStorage
    mounts: list[ReproMount] | None


class SizingAxis(_Contract):
    axis: str
    current: int | None
    recommended: int | None
    unit: str
    action: str
    estimate_quality: str
    # disk 축 전용(cpu/memory 축엔 부재) — Optional 로 흡수.
    mountpoint: str | None = None
    device_ref: str | None = None
    used_pct: float | None = None
    runway_days: int | None = None
    note: str | None = None


class Sizing(_Contract):
    axes: list[SizingAxis]


class DataQuality(_Contract):
    sufficient: bool | None
    notes: list[str] | None


class Assessment(_Contract):
    classification: str | None
    confidence: str | None
    data_quality: DataQuality


class DiagUtilization(_Contract):
    eval_pct: float | None
    sizing_pct: float | None


class DiagSaturation(_Contract):
    signal: str | None
    value: float | None
    threshold: float | None
    unit: str | None
    measured: bool | None
    saturated: bool | None


class DiagResource(_Contract):
    axis: str | None
    status: str | None
    utilization: DiagUtilization
    saturation: DiagSaturation | None
    confidence_notes: list[str] | None


class DiagAdvisory(_Contract):
    disk_io_tier_hint: str | None
    network_congested: bool | None


class Diagnostics(_Contract):
    root_cause: str | None
    root_cause_detail: str | None
    resources: list[DiagResource] | None
    advisory: DiagAdvisory


class AssessmentServer(_Contract):
    identity: AssessmentIdentity
    reproduction: Reproduction
    sizing: Sizing
    assessment: Assessment
    diagnostics: Diagnostics


class AssessmentWindow(_Contract):
    days: int | None
    start: str | None
    end: str | None
    basis: str | None


class AssessmentFilter(_Contract):
    hostname: list[str] | None
    ip: list[str] | None
    public_id: list[str] | None
    pair: list[str] | None


class AssessmentWarnings(_Contract):
    ambiguous_hostnames: list[str] | None
    unresolved_pairs: list[str] | None
    unmatched_filters: list[str] | None


class AssessmentEnvelope(_Contract):
    contract_version: str
    generated_at: str
    window: AssessmentWindow
    filter: AssessmentFilter
    warnings: AssessmentWarnings
    count: int
    servers: list[AssessmentServer]
