"""Assessment API 매퍼 property-based 감사 (hypothesis) — /api/assessment 계약 불변식.

수천 가짓수의 합성 ReportRowRaw + per-mount 용량을 build_assessment_entry/envelope 에 먹여, 어떤 입력에도
계약(assessment-api.md 4절)이 깨지지 않음을 검증한다: JSON 직렬화 가능·필수 키·사이징 never-worse(under->증설/
over->감축)·recommended never-null·분류/신뢰도 enum·근본원인 축 정합. 도메인 커널 property(test_recommendation_
properties)가 rollup_host 를 덮고, 본 스위트는 그 위 API 표현 계층(dict 계약)을 덮는다.
"""

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from assessment_engine import recommendation as R
from assessment_engine.contract import CONTRACT_VERSION
from assessment_engine.db.dtos.outbound import MountCapacityRaw, ReportRowRaw
from assessment_engine.web.services.mappers.assessment_api import (
    build_assessment_entry,
    build_assessment_envelope,
)

_ACTIONS = {"increase", "decrease", "keep"}
_QUALITY = {"exact", "floor", "uncertain"}
_DIAG_AXES = {"cpu", "memory", "disk", "disk_io", "network"}
_ROOT_CAUSE_AXES = _DIAG_AXES | {None}


def _opt(strat):
    return st.one_of(st.none(), strat)


_pct = st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False)
_cores = st.sampled_from([1, 2, 4, 8, 16, 32, 64])
_mem_bytes = st.sampled_from([512, 1024, 2048, 4096, 8192, 16384, 32768, 65536]).map(lambda mb: mb * 1024 * 1024)


@st.composite
def _interfaces(draw):
    """net_interfaces — 물리/가상 혼합, ipv4 주소 유무. primary_ip·reproduction 경로 자극."""
    n = draw(st.integers(min_value=0, max_value=3))
    out = []
    for i in range(n):
        addrs = []
        if draw(st.booleans()):
            addrs.append({"address": f"10.0.{i}.5", "prefix": 24, "family": "ipv4", "origin": "static"})
        out.append({
            "id": f"mac:aa:bb:cc:00:00:0{i}", "id_type": "mac", "name": f"eth{i}",
            "kind": draw(st.sampled_from(["physical", "bond_master", "bridge", "virtual", None])),
            "mtu": draw(st.sampled_from([1500, 9000, None])), "addresses": addrs,
            "bond_mode": draw(st.sampled_from(["802.3ad", "active-backup", "weird-unmapped", None])),
        })
    return out


@st.composite
def _block_devices(draw):
    """block_devices — disk/partition, size_bytes 합이 disk_total_bytes(사이징 base). mountpoint 로 device_ref 조인."""
    n = draw(st.integers(min_value=0, max_value=3))
    out = []
    for i in range(n):
        out.append({
            "id": f"by-path:pci-0000:00:0{i}", "id_type": "by-path", "name": f"sd{chr(97 + i)}",
            "type": draw(st.sampled_from(["disk", "part", "lvm", "swap"])),
            "size_bytes": draw(st.sampled_from([10, 20, 50, 100, 500])) * 1024**3,
            "mountpoint": draw(st.sampled_from(["/", "/data", "/mnt/x", None])),
            "raid_level": draw(st.sampled_from(["raid1", "raid5", "linear", 0, None])),
        })
    return out


@st.composite
def _report_row(draw) -> ReportRowRaw:
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
def _mounts(draw) -> list[MountCapacityRaw]:
    n = draw(st.integers(min_value=0, max_value=3))
    out = []
    for i in range(n):
        out.append(MountCapacityRaw(
            mountpoint=draw(st.sampled_from(["/", "/data", "/mnt/x", f"/m{i}"])),
            total_bytes=draw(_opt(st.sampled_from([10, 50, 100, 500]).map(lambda g: g * 1024**3))),
            used_pct=draw(_opt(_pct)),
            byte_runway_days=draw(_opt(st.floats(0, 2000, allow_nan=False))),
            inode_runway_days=draw(_opt(st.floats(0, 2000, allow_nan=False))),
            inode_used_pct=draw(_opt(_pct)),
            target_bytes=draw(_opt(st.sampled_from([20, 100, 1000]).map(lambda g: g * 1024**3))),
        ))
    return out


@settings(max_examples=3000)
@given(_report_row(), _mounts(), st.booleans(), st.booleans())
def test_entry_contract_invariants(raw, mounts, is_online, ambiguous):
    """어떤 유효 입력에도 서버 항목 계약이 성립 (위반 = 확정 버그)."""
    entry = build_assessment_entry(raw, mounts, is_online, ambiguous)

    # 1. 최상위 5블록 (decision-first 계약)
    assert set(entry) == {"identity", "reproduction", "sizing", "assessment", "diagnostics"}
    # 2. 전체 JSON 직렬화 가능 — 타입 누수(datetime/set/IP객체)·비직렬 PII 노출 즉시 검출
    json.dumps(entry)

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
            assert isinstance(rec, int) and rec > 0, a
            assert a["unit"] in ("vcpus", "mib"), a
        else:  # disk
            assert a["axis"] == "disk" and a["unit"] == "gib", a
            assert a.get("mountpoint") is not None, a

    # 5. assessment enum + data_quality 불변식
    asmt = entry["assessment"]
    assert asmt["classification"] in R.LABEL_KO, asmt
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


@settings(max_examples=1500)
@given(_report_row(), _mounts(), st.booleans())
def test_entry_deterministic(raw, mounts, is_online):
    """동일 입력 -> byte-identical 출력 (순수 함수)."""
    e1 = build_assessment_entry(raw, mounts, is_online, False)
    e2 = build_assessment_entry(raw, mounts, is_online, False)
    assert json.dumps(e1, sort_keys=True) == json.dumps(e2, sort_keys=True)


@settings(max_examples=1000)
@given(st.lists(st.tuples(_report_row(), _mounts(), st.booleans()), max_size=5))
def test_envelope_contract(rows):
    """envelope(4.1) — count==len(servers), contract_version, warnings 3키, JSON 직렬."""
    servers = [build_assessment_entry(raw, mounts, online, False) for raw, mounts, online in rows]
    result = {
        "servers": servers,
        "ambiguous_hostnames": [],
        "unresolved_pairs": [],
        "unmatched_filters": [],
    }
    env = build_assessment_envelope(
        result, generated_at="2026-01-01T00:00:00+00:00", window_days=14,
        window_start="2025-12-18T00:00:00+00:00", window_end="2026-01-01T00:00:00+00:00",
        filters={"hostname": [], "ip": [], "public_id": [], "pair": []},
    )
    assert env["contract_version"] == CONTRACT_VERSION
    assert env["count"] == len(servers)
    assert env["servers"] is servers
    assert set(env["warnings"]) == {"ambiguous_hostnames", "unresolved_pairs", "unmatched_filters"}
    assert set(env["window"]) == {"days", "start", "end", "basis"}
    json.dumps(env)
