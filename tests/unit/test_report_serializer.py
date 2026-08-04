"""report_serializer 라운드트립 — env_report_to_dict <-> env_report_from_dict 정적 스냅샷 정합.

발행 시점 ViewModel 을 JSONB dict 로 저장(`env_report_to_dict`=asdict) 후 GET 시 dict 를 ViewModel 로
복원(`env_report_from_dict`)해 정적 렌더한다. 신규 nested(server_inventory·memory_breakdown·cpu_breakdown·
service_catalog)가 dict 가 아닌 dataclass 로 복원돼야 template 의 `.attr` 접근이 안 깨진다(#C1 정적 스냅샷).
본 테스트는 그 복원 정합과, 필드가 나중에 제거돼도 과거 스냅샷의 잔존 키가 복원을 깨지 않는지(`_drop_unknown_
fields`)를 고정한다.
"""

import dataclasses
from datetime import UTC, datetime

import pytest

from assessment_engine.web.services.mappers.environment_report import to_environment_report
from assessment_engine.web.services.report_serializer import (
    _report_row_from_dict,
    env_report_from_dict,
    env_report_to_dict,
)
from assessment_engine.web.view_models.attention import ActionTargets, AttentionSignals, EnvironmentOverview
from assessment_engine.web.view_models.environment_report import (
    CpuBreakdown,
    MemoryBreakdown,
    ServerInventorySnapshot,
    ServiceCatalogGroup,
    ServiceHost,
    ServiceNameCount,
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
        action=ActionTargets(),
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
    assert restored.memory_breakdown is None
    assert restored.cpu_breakdown is None


def test_env_report_roundtrip_restores_period_assessment_storage_network():
    """단일 보고서(engineer) 전용 — period_assessment(재귀 nested)·storage_tree(재귀)·network_interfaces
    가 dict 가 아닌 dataclass 로 복원 (single_report CPU/메모리/스토리지/네트워크 상세 카드가 `.attr` 로 접근)."""
    from assessment_engine.web.view_models.metric import (
        PeriodAssessment,
        PeriodErrorRow,
        PeriodExtraGroup,
        PeriodResource,
        PeriodSignalRow,
    )
    from assessment_engine.web.view_models.server import NetIfaceAddress, NetworkInterfaceInfo, StorageNode

    vm = _make_env_report()
    row = PeriodSignalRow(label="사용률", value="70.0%", threshold="임계 70%", over=True, measured=True)
    vm.period_assessment = PeriodAssessment(
        resources=[
            PeriodResource(
                name="CPU",
                util_rows=[row],
                util_over=1,
                sat_rows=[row],
                sat_over=1,
                has_util=True,
                detail_slug="cpu",
                verdict_label="자원 부족",
                verdict_color="#dc2626",
                extra_groups=[PeriodExtraGroup(label="부하 신호", rows=[row])],
                error_rows=[
                    PeriodErrorRow(
                        key="mem_oom", label="OOM Kill", badge_text="1건", badge_class="badge-danger",
                        note="14일 내 1회", sizing_signal="메모리 자원 부족",
                    )
                ],
            )
        ],
        error_rows=[],
        window_days=14,
        classification_label="자원 부족",
        classification_color="#dc2626",
    )
    child = StorageNode(name="vda1", kind="part", kind_label="파티션", size_gb=100.0, mount="/", usage_pct=42.5)
    vm.storage_tree = [StorageNode(name="vda", kind="disk", kind_label="디스크", size_gb=100.0, children=[child])]
    addr = NetIfaceAddress(value="10.0.0.1/24", is_ipv4=True)
    vm.network_interfaces = [NetworkInterfaceInfo(name="eth0", mac="aa:bb:cc:dd:ee:ff", addresses=[addr])]

    restored = env_report_from_dict(env_report_to_dict(vm))

    pa = restored.period_assessment
    assert isinstance(pa, PeriodAssessment)
    cpu = pa.resources[0]
    assert isinstance(cpu, PeriodResource) and isinstance(cpu.util_rows[0], PeriodSignalRow)
    grp = cpu.extra_groups[0]
    assert isinstance(grp, PeriodExtraGroup) and isinstance(grp.rows[0], PeriodSignalRow)
    assert isinstance(cpu.error_rows[0], PeriodErrorRow)
    assert cpu.error_rows[0].sizing_signal == "메모리 자원 부족"

    root = restored.storage_tree[0]
    assert isinstance(root, StorageNode)
    assert isinstance(root.children[0], StorageNode) and root.children[0].usage_pct == 42.5

    iface = restored.network_interfaces[0]
    assert isinstance(iface, NetworkInterfaceInfo)
    assert isinstance(iface.addresses[0], NetIfaceAddress) and iface.addresses[0].value == "10.0.0.1/24"


def test_env_report_roundtrip_empty_period_assessment_and_storage_stay_default():
    """environment·selection 양식(단일 전용 필드 미설정)도 안전 — None/빈 list 보존."""
    restored = env_report_from_dict(env_report_to_dict(_make_env_report()))
    assert restored.period_assessment is None
    assert restored.storage_tree == []
    assert restored.network_interfaces == []


def _minimal_row_dict() -> dict:
    """최소 유효 row dict — 필수 필드만. 필드 변경 시 자동 추종(라운드트립 dict->dataclass 검증용)."""
    d: dict = {}
    for f in dataclasses.fields(ReportRowItem):
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            d[f.name] = False if f.type == "bool" else (1 if f.name == "server_id" else None)
    d["hostname"] = "test-host"
    return d


def test_report_row_roundtrip_drops_removed_legacy_fields():
    """과거 스냅샷에 남은 폐기 필드(saturation_axes — ReportRowItem 에서 제거됨)가 복원을 깨지 않는다.

    보고서는 발행 시점 정적 JSONB 스냅샷(#C1) — 필드를 나중에 빼도 옛 스냅샷은 그 키를 그대로 들고 있다.
    `_drop_unknown_fields` 가 현재 스키마에 없는 키를 걸러내 `ReportRowItem(**data)` 가 unexpected keyword
    argument 로 실패하지 않아야 한다.
    """
    data = env_report_to_dict(_make_env_report())
    row = _minimal_row_dict()
    row["saturation_axes"] = [{"axis": "CPU 포화", "signal": "load avg / core", "value": "0.25"}]
    data["top_risks"] = [row]

    restored = env_report_from_dict(data)

    assert not hasattr(restored.top_risks[0], "saturation_axes")


def test_action_targets_roundtrip_drops_removed_metrics_fields():
    """과거 스냅샷의 action.hosts[].metrics·action.metric_labels(ADR 0056 이전, CapacityMetric 폐기)가
    복원을 깨지 않는다 — top_risks 와 동일 `_drop_unknown_fields` 경로."""
    data = env_report_to_dict(_make_env_report())
    data["action"] = {
        "hosts": [
            {
                "public_id": "p1",
                "hostname": "legacy-host",
                "classification": "under_provisioned",
                "classification_label": "자원 부족",
                "badge_class": "rec-under_provisioned",
                "classification_rank": 0,
                "active_causes": [],
                "services": {},
                "metrics": [
                    {"label": "CPU p95", "value": "90.0%", "active": True, "measured": True, "color": "#dc2626"}
                ],
                "confidence_notes": [],
                "recommendation_action": "",
                "root_cause_label": "",
                "severity_score": 0.0,
                "net_status_label": "",
                "net_status_color": "",
                "disk_io_status_label": "",
                "disk_io_status_color": "",
                "spec_display": "",
            }
        ],
        "metric_labels": ["CPU p95"],
        "total": 1,
        "under_count": 1,
        "efficiency_count": 0,
        "efficiency_vcpus": 0,
        "efficiency_memory_gb": 0.0,
        "efficiency_disk_gb": 0,
    }

    restored = env_report_from_dict(data)

    assert not hasattr(restored.action, "metric_labels")
    assert not hasattr(restored.action.hosts[0], "metrics")
    assert restored.action.hosts[0].hostname == "legacy-host"


def test_nested_overview_and_storage_drop_removed_fields_via_build():
    """환경scope nested(overview.saturation_donuts·storage_tree)도 _build 일률 적용으로 필드제거 내성.

    _drop_unknown_fields 가 5개 dataclass 만 커버하던 것을 _build 로 전 재구성 진입점 일률화한 회귀 —
    nested 타입에 옛 필드가 남은 과거 스냅샷도 TypeError 없이 복원돼야 한다(#C1 정적 스냅샷).
    """
    data = env_report_to_dict(_make_env_report())
    # overview nested(SaturationDonut)에 폐기 가정 키 주입
    data["overview"]["saturation_donuts"] = [
        {
            "label": "CPU 포화", "count": 0, "total": 1,
            "dash_length": 0.0, "dash_offset": 0.0, "color": "#dc2626", "pct": 0.0,
            "_legacy_removed_axis": "load",  # 현재 스키마에 없는 옛 필드
        }
    ]
    # storage_tree 재귀 노드에도 폐기 가정 키
    data["storage_tree"] = [
        {"name": "vda", "kind": "disk", "kind_label": "디스크", "size_gb": 100.0,
         "children": [], "_legacy_major_minor": "252:0"}
    ]

    restored = env_report_from_dict(data)

    sd = restored.overview.saturation_donuts[0]
    assert not hasattr(sd, "_legacy_removed_axis") and sd.label == "CPU 포화"
    node = restored.storage_tree[0]
    assert not hasattr(node, "_legacy_major_minor") and node.name == "vda"


def _row_kwargs(**overrides) -> dict:
    """ReportRowItem 복원 입력 — 필수 필드를 타입별 최소값으로 채운다."""
    base: dict = {}
    for f in dataclasses.fields(ReportRowItem):
        if f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING:
            continue
        t = str(f.type)
        base[f.name] = 0 if ("int" in t or "float" in t) else (False if "bool" in t else "")
    return {**base, **overrides}


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        # 옛 어휘로 저장된 스냅샷 — 지원 단계 이름이 바뀌기 전 발행분
        ("eol", "ended"),
        ("extended", "security_only"),
        ("supported", "full"),
        # 현행 어휘와 판정 불가는 그대로 통과
        ("ended", "ended"),
        ("paid_only", "paid_only"),
        ("security_only", "security_only"),
        ("full", "full"),
        ("unknown", "unknown"),
        ("", ""),
    ],
)
def test_legacy_os_eol_status_restored(stored, expected):
    """보고서는 발행 시점 정적 스냅샷이라 옛 상태 문자열이 그대로 되살아난다.

    표시 계층이 현행 어휘만 분기하므로 역직렬화에서 옮긴다. 매핑이 빠지면 예외 없이
    "미상" 으로 렌더돼 조용히 실패한다.
    """
    assert _report_row_from_dict(_row_kwargs(os_eol_status=stored)).os_eol_status == expected
