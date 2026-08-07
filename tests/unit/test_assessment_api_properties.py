"""Assessment API 매퍼 property-based 감사 (hypothesis) — /api/assessment 계약 불변식.

수천 가짓수의 합성 ReportRowRaw + per-mount 용량을 build_assessment_entry/envelope 에 먹여, 어떤 입력에도
계약(assessment-api.md 4절)이 깨지지 않음을 검증한다: JSON 직렬화 가능·필수 키·사이징 never-worse(under->증설/
over->감축)·recommended never-null·분류/신뢰도 enum·근본원인 축 정합. 도메인 커널 property(test_right_sizing_
properties)가 rollup_host 를 덮고, 본 스위트는 그 위 API 표현 계층(dict 계약)을 덮는다.
"""

import json
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.strategies import DrawFn
from pydantic import TypeAdapter

from assessment_engine.contract import API_CONTRACT_VERSION
from assessment_engine.db.dtos.outbound import MountCapacityRaw, ReportRowRaw
from assessment_engine.domain import right_sizing
from assessment_engine.json_types import JsonObject
from assessment_engine.web.services.mappers.assessment_api import (
    build_assessment_entry,
    build_assessment_envelope,
)
from assessment_engine.web.services.mappers.right_sizing_api import _action
from assessment_engine.web.view_models.assessment_api import AssessmentServer
from tests.builders import report_row_raw
from tests.hypothesis_scale import examples

# TypedDict 는 인스턴스 메서드가 없어 어댑터를 거친다 — extra=forbid 검증은 그대로다.
_SERVER_SCHEMA = TypeAdapter(AssessmentServer)

_ACTIONS = {"increase", "decrease", "keep"}
_QUALITY = {"exact", "floor", "uncertain"}
_DIAG_AXES = {"cpu", "memory", "disk", "disk_io", "network"}
_ROOT_CAUSE_AXES = _DIAG_AXES | {None}


def _opt(strat: st.SearchStrategy[Any]) -> st.SearchStrategy[Any]:
    return st.one_of(st.none(), strat)


_pct = st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False)
_cores = st.sampled_from([1, 2, 4, 8, 16, 32, 64])
_mem_bytes = st.sampled_from([512, 1024, 2048, 4096, 8192, 16384, 32768, 65536]).map(lambda mb: mb * 1024 * 1024)


@st.composite
def _interfaces(draw: DrawFn) -> list[JsonObject]:
    """net_interfaces — 물리/가상 혼합, ipv4 주소 유무. primary_ip·reproduction 경로 자극."""
    n = draw(st.integers(min_value=0, max_value=3))
    out: list[JsonObject] = []
    for i in range(n):
        addrs: list[JsonObject] = []
        if draw(st.booleans()):
            addrs.append({"address": f"10.0.{i}.5", "prefix": 24, "family": "ipv4", "origin": "static"})
        out.append(
            {
                "id": f"mac:aa:bb:cc:00:00:0{i}",
                "id_type": "mac",
                "name": f"eth{i}",
                "kind": draw(st.sampled_from(["physical", "bond_master", "bridge", "virtual", None])),
                "mtu": draw(st.sampled_from([1500, 9000, None])),
                "addresses": addrs,
                "bond_mode": draw(st.sampled_from(["802.3ad", "active-backup", "weird-unmapped", None])),
            }
        )
    return out


@st.composite
def _block_devices(draw: DrawFn) -> list[JsonObject]:
    """block_devices — disk/partition, size_bytes 합이 disk_total_bytes(사이징 base). mountpoint 로 device_ref 조인."""
    n = draw(st.integers(min_value=0, max_value=3))
    return [
        {
            "id": f"by-path:pci-0000:00:0{i}",
            "id_type": "by-path",
            "name": f"sd{chr(97 + i)}",
            "type": draw(st.sampled_from(["disk", "part", "lvm", "swap"])),
            "size_bytes": draw(st.sampled_from([10, 20, 50, 100, 500])) * 1024**3,
            "mountpoint": draw(st.sampled_from(["/", "/data", "/mnt/x", None])),
            "raid_level": draw(st.sampled_from(["raid1", "raid5", "linear", 0, None])),
        }
        for i in range(n)
    ]


@st.composite
def _report_row(draw: DrawFn) -> ReportRowRaw:
    return ReportRowRaw(
        server_id=draw(st.integers(min_value=1, max_value=10_000)),
        public_id=draw(st.uuids().map(str)),
        hostname=draw(st.text(min_size=1, max_size=20).filter(lambda s: s.strip())),
        os_family=draw(st.sampled_from(["linux", "windows", None])),
        os_id=draw(st.sampled_from(["ubuntu", "rocky", "windows", None])),
        os_version=draw(st.sampled_from(["22.04", "9.3", "2022", None])),
        os_codename=draw(st.sampled_from(["jammy", None])),
        kernel_version=draw(st.sampled_from(["5.15.0", "10.0.20348", None])),
        net_interfaces=draw(_opt(_interfaces())),
        services=draw(st.none()),
        last_seen_at=draw(st.none()),
        cpu_p95_pct=draw(_opt(_pct)),
        cpu_avg_pct=draw(_opt(_pct)),
        cpu_peak_pct=draw(_opt(_pct)),
        mem_p95_pct=draw(_opt(_pct)),
        mem_avg_pct=draw(_opt(_pct)),
        mem_peak_pct=draw(_opt(_pct)),
        mem_near_peak_pct=draw(_opt(_pct)),
        cpu_cores=draw(_opt(_cores)),
        mem_total_bytes=draw(_opt(_mem_bytes)),
        block_devices=draw(_opt(_block_devices())),
        lvm_vgs=draw(st.none()),
        cpu_run_queue_p95=draw(_opt(st.floats(0, 30, allow_nan=False))),
        mem_pages_input_rate_p95=draw(_opt(st.floats(0, 2000, allow_nan=False))),
        cpu_percore_p95_max=draw(_opt(_pct)),
        procs_blocked_p95=draw(_opt(st.floats(0, 30, allow_nan=False))),
        procs_running_p95=draw(_opt(st.floats(0, 30, allow_nan=False))),
        mem_swap_paging=draw(st.booleans()),
        oom_occurred=draw(st.booleans()),
        disk_await_p95_ms=draw(_opt(st.floats(0, 2000, allow_nan=False))),
        disk_iops_baseline=draw(_opt(st.integers(0, 10000))),
        disk_capacity_runway_days=draw(_opt(st.floats(0, 1000, allow_nan=False))),
        disk_capacity_driving_mount=draw(st.sampled_from(["/", "/data", None])),
        disk_capacity_driving_used_pct=draw(_opt(_pct)),
        disk_inode_runway_days=draw(_opt(st.floats(0, 1000, allow_nan=False))),
        disk_inode_used_pct=draw(_opt(_pct)),
        disk_capacity_target_gb=draw(_opt(st.floats(0, 5000, allow_nan=False))),
        net_rx_kbps=draw(_opt(st.floats(0, 1_000_000, allow_nan=False))),
        net_tx_kbps=draw(_opt(st.floats(0, 1_000_000, allow_nan=False))),
        net_retrans_pct=draw(_opt(st.floats(0, 20, allow_nan=False))),
        net_drop_pct=draw(_opt(st.floats(0, 20, allow_nan=False))),
        conntrack_ratio=draw(_opt(st.floats(0, 1.5, allow_nan=False))),
        cpu_steal_p95_pct=draw(_opt(_pct)),
        cpu_burst_ratio=draw(_opt(st.floats(0, 10, allow_nan=False))),
        history_hours=draw(_opt(st.floats(0, 1000, allow_nan=False))),
        cpu_sufficiency=draw(_opt(st.floats(0, 1.2, allow_nan=False))),
        mem_sufficiency=draw(_opt(st.floats(0, 1.2, allow_nan=False))),
        cpu_trend_slope=draw(_opt(st.floats(-5, 5, allow_nan=False))),
        mem_trend_slope=draw(_opt(st.floats(-5, 5, allow_nan=False))),
    )


@st.composite
def _mounts(draw: DrawFn) -> list[MountCapacityRaw]:
    n = draw(st.integers(min_value=0, max_value=3))
    return [
        MountCapacityRaw(
            mountpoint=draw(st.sampled_from(["/", "/data", "/mnt/x", f"/m{i}"])),
            total_bytes=draw(_opt(st.sampled_from([10, 50, 100, 500]).map(lambda g: g * 1024**3))),
            used_pct=draw(_opt(_pct)),
            byte_runway_days=draw(_opt(st.floats(0, 2000, allow_nan=False))),
            inode_runway_days=draw(_opt(st.floats(0, 2000, allow_nan=False))),
            inode_used_pct=draw(_opt(_pct)),
            target_bytes=draw(_opt(st.sampled_from([20, 100, 1000]).map(lambda g: g * 1024**3))),
        )
        for i in range(n)
    ]


@settings(max_examples=examples(3000))
@given(_report_row(), _mounts(), st.booleans(), st.booleans())
def test_entry_contract_invariants(raw: ReportRowRaw, mounts: list[MountCapacityRaw], is_online: bool, ambiguous: bool):
    """어떤 유효 입력에도 서버 항목 계약이 성립 (위반 = 확정 버그)."""
    entry = build_assessment_entry(raw, mounts, is_online, ambiguous)

    # 1. 최상위 5블록 (decision-first 계약)
    assert set(entry) == {"identity", "reproduction", "sizing", "assessment", "diagnostics"}
    # 2. 전체 JSON 직렬화 가능 — 타입 누수(datetime/set/IP객체)·비직렬 PII 노출 즉시 검출
    json.dumps(entry)
    # 2b. 응답 스키마(계약 타입 미러) drift 가드 — 매퍼 dict 가 AssessmentServer(extra=forbid)에 검증.
    #     신규/누락 키나 타입 불일치 = OpenAPI 스키마와 매퍼 어긋남 = 즉시 실패.
    _SERVER_SCHEMA.validate_python(entry)

    # 3. identity passthrough
    ident = entry["identity"]
    assert ident["public_id"] == raw.public_id
    assert ident["hostname"] == raw.hostname
    assert ident["online"] is is_online
    assert ident["hostname_ambiguous"] is ambiguous
    assert ident["os_family"] == raw.os_family

    # 4. sizing.axes never-worse + never-null recommended
    for a in entry["sizing"]["axes"]:
        assert a["recommended"] is not None, a  # never-null 계약
        assert a["current"] is not None, a  # current 미상이면 축 생략
        assert a["action"] in _ACTIONS, a
        assert a["estimate_quality"] in _QUALITY, a
        cur, rec, act = a["current"], a["recommended"], a["action"]
        if act == "increase":
            assert rec >= cur, a  # 부족을 더 부족하게 만들지 않음
        elif act == "decrease":
            assert rec <= cur, a  # 과다 감축은 현재 이하
        else:  # keep
            assert rec == cur, a
        if a["axis"] in ("cpu", "memory"):
            assert isinstance(rec, int), a
            assert rec > 0, a
            assert a["unit"] in ("vcpus", "mib"), a
        else:  # disk
            assert a["axis"] == "disk", a
            assert a["unit"] == "gib", a
            assert a.get("mountpoint") is not None, a

    # 5. assessment enum + data_quality 불변식
    asmt = entry["assessment"]
    assert asmt["classification"] in right_sizing.RECOMMENDATION_LABEL_KO, asmt
    assert asmt["confidence"] in ("low", "medium", "high"), asmt
    dq = asmt["data_quality"]
    assert dq["sufficient"] is (asmt["confidence"] == "high")
    if asmt["confidence"] != "high":
        assert dq["notes"], asmt  # high 아니면 근거 노트 최소 1

    # 6. diagnostics 축 정합
    diag = entry["diagnostics"]
    assert diag["root_cause"] in _ROOT_CAUSE_AXES, diag["root_cause"]
    res_axes = [r["axis"] for r in diag["resources"]]
    assert set(res_axes) == _DIAG_AXES, res_axes
    assert isinstance(diag["advisory"]["network_congested"], bool)


@settings(max_examples=examples(1500))
@given(_report_row(), _mounts(), st.booleans())
def test_entry_deterministic(raw: ReportRowRaw, mounts: list[MountCapacityRaw], is_online: bool):
    """동일 입력 -> byte-identical 출력 (순수 함수)."""
    e1 = build_assessment_entry(raw, mounts, is_online, False)
    e2 = build_assessment_entry(raw, mounts, is_online, False)
    assert json.dumps(e1, sort_keys=True) == json.dumps(e2, sort_keys=True)


@settings(max_examples=examples(1000))
@given(st.lists(st.tuples(_report_row(), _mounts(), st.booleans()), max_size=5))
def test_envelope_contract(rows: list[tuple[ReportRowRaw, list[MountCapacityRaw], bool]]):
    """envelope(4.1) — count==len(servers), contract_version, warnings 3키, JSON 직렬."""
    servers = [build_assessment_entry(raw, mounts, online, False) for raw, mounts, online in rows]
    result = {
        "servers": servers,
        "ambiguous_hostnames": [],
        "unresolved_pairs": [],
        "unmatched_filters": [],
    }
    env = build_assessment_envelope(
        result,
        generated_at="2026-01-01T00:00:00+00:00",
        window_days=14,
        window_start="2025-12-18T00:00:00+00:00",
        window_end="2026-01-01T00:00:00+00:00",
        filters={"hostname": [], "ip": [], "public_id": [], "pair": []},
    )
    assert env["contract_version"] == API_CONTRACT_VERSION
    assert env["count"] == len(servers)
    assert env["servers"] is servers
    assert set(env["warnings"]) == {"ambiguous_hostnames", "unresolved_pairs", "unmatched_filters"}
    assert set(env["window"]) == {"days", "start", "end", "basis"}
    json.dumps(env)


def test_reproduction_reshapes_os_boot_mounts():
    """_reproduction os/boot/mounts 값 reshape — 채워진 boot dict.get 픽업 + nonblock_mounts 컴프리헨션."""
    raw = ReportRowRaw(
        server_id=1,
        public_id="00000000-0000-0000-0000-000000000001",
        hostname="h1",
        os_family="linux",
        os_id="rocky",
        os_version="9.3",
        os_codename=None,
        kernel_version="5.14.0",
        net_interfaces=None,
        services=None,
        last_seen_at=None,
        cpu_p95_pct=None,
        cpu_avg_pct=None,
        cpu_peak_pct=None,
        mem_p95_pct=None,
        mem_avg_pct=None,
        mem_peak_pct=None,
        arch="aarch64",
        bits=64,
        boot_firmware="uefi",
        secure_boot=False,
        edition="Datacenter",
        timezone="UTC",
        rtc_utc=True,
        boot={"kernel_cmdline": "ro quiet", "root_ref_type": "label", "grub_install_target": None},
        nonblock_mounts=[
            {"source": "tmpfs", "target": "/run", "fstype": "tmpfs", "options": ["rw"], "fs_freq": 0, "fs_passno": 0}
        ],
    )
    repro = build_assessment_entry(raw, [], True)["reproduction"]
    assert repro["os"]["arch"] == "aarch64"
    assert repro["os"]["bits"] == 64
    assert repro["os"]["boot_firmware"] == "uefi"
    assert repro["os"]["secure_boot"] is False
    assert repro["os"]["edition"] == "Datacenter"
    assert repro["os"]["timezone"] == "UTC"
    assert repro["os"]["rtc_utc"] is True
    assert repro["boot"]["kernel_cmdline"] == "ro quiet"
    assert repro["boot"]["root_ref_type"] == "label"
    assert repro["boot"]["grub_install_target"] is None
    assert repro["mounts"][0]["source"] == "tmpfs"
    assert repro["mounts"][0]["fstype"] == "tmpfs"
    assert repro["mounts"][0]["options"] == ["rw"]
    assert repro["mounts"][0]["fs_passno"] == 0


def test_reproduction_boot_and_mounts_null_fallback():
    """raw.boot=None / nonblock_mounts=None 폴백 — boot or {} / nonblock_mounts or []."""
    raw = ReportRowRaw(
        server_id=2,
        public_id="00000000-0000-0000-0000-000000000002",
        hostname="h2",
        os_family="windows",
        os_id="windows",
        os_version="2022",
        os_codename=None,
        kernel_version=None,
        net_interfaces=None,
        services=None,
        last_seen_at=None,
        cpu_p95_pct=None,
        cpu_avg_pct=None,
        cpu_peak_pct=None,
        mem_p95_pct=None,
        mem_avg_pct=None,
        mem_peak_pct=None,
        boot=None,
        nonblock_mounts=None,
    )
    repro = build_assessment_entry(raw, [], False)["reproduction"]
    assert repro["boot"] == {"kernel_cmdline": None, "root_ref_type": None, "grub_install_target": None}
    assert repro["mounts"] == []


# --- 키 생략 (계약 문서 "필드 존재 대 값" 절) --------------------------------

_DISK_ONLY_KEYS = {"mountpoint", "device_ref", "used_pct", "runway_days", "note"}
_COMMON_AXIS_KEYS = {"axis", "unit", "current", "recommended", "action", "estimate_quality"}


def test_sizing_axes_key_sets_differ_by_axis():
    """`sizing.axes` 는 축 종류마다 키 집합이 다르다 — disk 전용 5키는 cpu/memory 에 아예 없다.

    계약 문서가 오래 "모든 키 항상 존재, 생략 아님" 이라고 말해 왔는데 이 축만 사실이 아니었다. 문서를
    현황으로 고쳤으므로 그 현황을 여기서 고정한다 — 반대 방향(문서대로 키를 채우는 변경)이 들어오면
    소비자 파싱이 바뀌므로 계약 개정 절차를 거쳐야 한다.
    """
    raw = report_row_raw(cpu_p95_pct=95.0, cpu_cores=2, mem_p95_pct=95.0, mem_total_bytes=4 * 1024**3)
    mounts = [MountCapacityRaw("/", 100 * 1024**3, 92.0, 30.0, None, 40.0, 200 * 1024**3)]

    axes = {a["axis"]: set(a) for a in build_assessment_entry(raw, mounts, True, False)["sizing"]["axes"]}

    assert axes["cpu"] == _COMMON_AXIS_KEYS
    assert axes["memory"] == _COMMON_AXIS_KEYS
    assert axes["disk"] == _COMMON_AXIS_KEYS | _DISK_ONLY_KEYS


@pytest.mark.parametrize(
    ("kind", "sizing_target", "expected_target_key"),
    [
        ("cpu", 4, "target_cores"),
        ("memory", 8192, "target_mb"),
        ("disk_capacity", 500, "target_gb"),
        ("cpu", None, None),
        ("network", 4, None),
        ("disk_io", 4, None),
    ],
)
def test_right_sizing_action_target_key_is_omitted_when_absent(
    kind: right_sizing.ResourceKind,
    sizing_target: int | None,
    expected_target_key: str | None,
):
    """`actions[]` 의 목표 수치 키는 자원 종류와 목표 유무에 따라 생략된다 (null 이 아니라 부재).

    사이징 축이 아닌 자원(network·disk_io)은 목표 키 자체가 없다 — 크기로 푸는 조치가 아니기 때문이다.
    """
    action = _action(kind, right_sizing.ResourceAssessment(kind, "under", sizing_target=sizing_target), "increase")

    target_keys = set(action) - {"resource", "op", "target_display"}
    assert target_keys == ({expected_target_key} if expected_target_key else set())
