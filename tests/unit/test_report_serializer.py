"""report_serializer 라운드트립 — env_report_to_dict <-> env_report_from_dict 정적 스냅샷 정합.

발행 시점 ViewModel 을 JSONB dict 로 저장(`env_report_to_dict`=asdict) 후 GET 시 dict 를 ViewModel 로
복원(`env_report_from_dict`)해 정적 렌더한다. 신규 nested(server_inventory·volumes·memory_breakdown·
cpu_breakdown·service_catalog)가 dict 가 아닌 dataclass 로 복원돼야 template 의 `.attr` 접근이 안 깨진다
(#C1 정적 스냅샷). 본 테스트는 그 복원 정합을 고정한다.
"""

import dataclasses
from datetime import UTC, datetime

from assessment_engine.web.services.mappers.environment_report import to_environment_report
from assessment_engine.web.services.report_serializer import env_report_from_dict, env_report_to_dict
from assessment_engine.web.view_models.attention import AttentionSignals, EnvironmentOverview
from assessment_engine.web.view_models.environment_report import (
    CpuBreakdown,
    MemoryBreakdown,
    ServerInventorySnapshot,
    ServiceCatalogGroup,
    ServiceHost,
    ServiceNameCount,
    VolumeUsage,
)
from assessment_engine.web.view_models.report import ReportRowItem, ReportSummary, ReportTotals
from assessment_engine.web.view_models.server import IpAddr


def _make_env_report():
    """신규 nested 필드 미설정 상태의 최소 EnvironmentReportSummary (environment 양식 base)."""
    overview = EnvironmentOverview(
        total=1,
        online=1,
        offline=0,
        total_vcpus=4,
        total_memory_gb=8.0,
        total_disk_gb=100,
        utilization=[],
        util_sample_size=1,
        risk_donut=[],
        risk_donut_total=0,
        risk_high_count=0,
    )
    base = ReportSummary(
        rows=[],
        period_days=14,
        total=1,
        online=1,
        risk_attention=0,
        risk_high=0,
        totals=ReportTotals(total_vcpus=4, total_memory_gb=8, total_disk_gb=100),
        summary_bullets=[],
        role_distribution={"web": 1},
    )
    return to_environment_report(
        view="engineer",
        base=base,
        overview=overview,
        attention=AttentionSignals(gap_warnings=[]),
        details=[],
        time_range="14d",
        anchor_at=datetime(2026, 5, 12, tzinfo=UTC),
        generated_at=datetime(2026, 5, 12, tzinfo=UTC),
    )


def test_env_report_roundtrip_restores_nested_dataclasses():
    """단일 보고서 nested(server_inventory·volumes·breakdown·service_catalog)가 dataclass 로 복원."""
    vm = _make_env_report()
    vm.server_inventory = ServerInventorySnapshot(
        hostname="host-1",
        os_display="Ubuntu 22.04",
        os_codename="jammy",
        kernel_version="6.2",
        cpu_model="Xeon",
        cpu_cores=4,
        mem_total_gb=8.0,
        swap_total_gb=2.0,
        disk_total_gb=100,
        ip_internal=[IpAddr(value="10.0.0.1/24", is_ipv4=True)],
        ip_external=[IpAddr(value="2001:db8::1", is_ipv4=False)],
        boot_time=datetime(2026, 5, 1, tzinfo=UTC),
        agent_started_at=datetime(2026, 5, 1, 3, 0, tzinfo=UTC),
        last_seen_at=datetime(2026, 5, 12, tzinfo=UTC),
        agent_version="4.1.0",
        composite_id="abc",
        machine_id="m1",
        is_online=True,
    )
    vm.volumes = [VolumeUsage(mount="/", total_gb=100.0, used_pct=42.5)]
    vm.memory_breakdown = MemoryBreakdown(used_pct=37.5, available_pct=62.5, cached_pct=12.5, buffers_pct=2.4)
    vm.cpu_breakdown = CpuBreakdown(user_pct=13.0, system_pct=3.9, iowait_pct=5.2)
    vm.service_catalog = [
        ServiceCatalogGroup(
            category="web",
            services=[
                ServiceNameCount(name="nginx", count=1, hosts=[ServiceHost(hostname="host-1", public_id="u-1")]),
            ],
        ),
    ]

    restored = env_report_from_dict(env_report_to_dict(vm))

    si = restored.server_inventory
    assert isinstance(si, ServerInventorySnapshot)
    assert si.ip_internal[0].value == "10.0.0.1/24" and si.ip_internal[0].is_ipv4 is True
    assert si.ip_external[0].is_ipv4 is False  # IPv6 보존
    assert si.boot_time == datetime(2026, 5, 1, tzinfo=UTC)  # datetime 복원 (str 아님)

    assert isinstance(restored.volumes[0], VolumeUsage) and restored.volumes[0].used_pct == 42.5
    assert isinstance(restored.memory_breakdown, MemoryBreakdown)
    assert restored.memory_breakdown.available_pct == 62.5
    assert isinstance(restored.cpu_breakdown, CpuBreakdown) and restored.cpu_breakdown.iowait_pct == 5.2

    grp = restored.service_catalog[0]
    assert isinstance(grp, ServiceCatalogGroup)
    assert isinstance(grp.services[0], ServiceNameCount)
    assert isinstance(grp.services[0].hosts[0], ServiceHost)
    assert grp.services[0].hosts[0].public_id == "u-1"


def test_env_report_roundtrip_empty_nested_stays_default():
    """nested 미설정(환경·selection 양식)도 라운드트립 안전 — None/[] 보존 (단일만 채움)."""
    restored = env_report_from_dict(env_report_to_dict(_make_env_report()))
    assert restored.server_inventory is None
    assert restored.volumes == []
    assert restored.memory_breakdown is None
    assert restored.cpu_breakdown is None


def _legacy_row_dict() -> dict:
    """구 스냅샷 row dict 모사 — 현 필수 필드 + 폐기 키. 필드 변경 시 자동 추종(직렬화 호환이 검증 대상)."""
    d: dict = {}
    for f in dataclasses.fields(ReportRowItem):
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            d[f.name] = False if f.type == "bool" else (1 if f.name == "server_id" else None)
    d["hostname"] = "legacy-host"
    return d


def test_env_report_from_dict_drops_legacy_snapshot_keys():
    """폐기 필드가 남은 구 스냅샷 복원 호환 — row 색 6키 + summary insufficient 2키 pop (#C1 정적 스냅샷)."""
    data = env_report_to_dict(_make_env_report())
    data["insufficient_hosts"] = []
    data["insufficient_hosts_count"] = 0
    row = _legacy_row_dict()
    for legacy in (
        "saturation_color",
        "cpu_variance_color",
        "mem_variance_color",
        "worst_mount_days_color",
        "reboot_count_color",
        "agent_restart_count_color",
    ):
        row[legacy] = "#b91c1c"
    data["top_risks"] = [row]

    restored = env_report_from_dict(data)

    assert restored.top_risks[0].hostname == "legacy-host"
    assert not hasattr(restored, "insufficient_hosts")
    assert not hasattr(restored.top_risks[0], "saturation_color")


def test_report_row_roundtrip_restores_saturation_axes():
    """ReportRowItem.saturation_axes(list[SaturationAxis]) 스냅샷 라운드트립 — dict -> dataclass 복원."""
    from assessment_engine.web.view_models.report import SaturationAxis

    data = env_report_to_dict(_make_env_report())
    row = _legacy_row_dict()
    row["saturation_axes"] = [
        dataclasses.asdict(SaturationAxis("CPU 포화", "load avg / core", "0.25", ">= 1", "정상", "")),
        dataclasses.asdict(
            SaturationAxis("메모리 포화", "Memory Pages/sec p95", "N/A", ">= 1000/s", "미관측", "text-meta")
        ),
    ]
    data["top_risks"] = [row]

    restored = env_report_from_dict(data)

    axes = restored.top_risks[0].saturation_axes
    assert isinstance(axes[0], SaturationAxis)  # str dict 아니라 dataclass 복원
    assert axes[0].axis == "CPU 포화" and axes[0].status == "정상"
    assert axes[1].status == "미관측" and axes[1].status_class == "text-meta"
