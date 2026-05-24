"""mappers — Outbound DTO → ViewModel + enrich idempotent 검증."""

from datetime import UTC, datetime, timedelta

import pytest

from assessment_engine.db.dtos.outbound import (
    DiskUsageWarningRaw,
    MetricGapWarningRaw,
    ServerDetail,
    ServerSummary,
    StorageWithUsage,
)
from assessment_engine.web.services.mappers.attention import (
    to_disk_warning_item,
    to_gap_warning_item,
)
from assessment_engine.web.services.mappers.server import (
    _to_disk_item,
    _to_listen_port_item,
    _to_service_item,
    _usage_badge_class,
    _usage_bar_color,
    _usage_severity,
    enrich_server_detail,
    to_server_detail,
    to_server_list_item,
    to_storage_detail,
)

# ─── 임계값·severity ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pct, severity",
    [
        (None, "ok"),
        (0.0, "ok"),
        (50.0, "ok"),
        (74.9, "ok"),
        (75.0, "warn"),
        (89.9, "warn"),
        (90.0, "danger"),
        (100.0, "danger"),
    ],
)
def test_usage_severity(pct, severity):
    assert _usage_severity(pct) == severity


def test_usage_badge_class_none_returns_empty():
    assert _usage_badge_class(None) == ""


@pytest.mark.parametrize(
    "pct, expected",
    [
        (50.0, "badge-ok"),
        (80.0, "badge-warn"),
        (95.0, "badge-danger"),
    ],
)
def test_usage_badge_class(pct, expected):
    assert _usage_badge_class(pct) == expected


@pytest.mark.parametrize(
    "pct, expected",
    [
        (None, "#22c55e"),  # bar는 None이어도 default green
        (50.0, "#22c55e"),
        (80.0, "#f59e0b"),
        (95.0, "#ef4444"),
    ],
)
def test_usage_bar_color(pct, expected):
    assert _usage_bar_color(pct) == expected


# ─── raw dict → ViewModel 변환 ────────────────────────────────────────────


def test_to_disk_item_returns_none_for_non_physical():
    assert _to_disk_item({"name": "sda1"}) is None
    assert _to_disk_item({"name": "dm-0"}) is None
    assert _to_disk_item({"name": "loop0"}) is None


def test_to_disk_item_for_physical():
    item = _to_disk_item({"name": "sda", "size_bytes": 1024**3, "type": "disk"})
    assert item.name == "sda"
    assert item.size_gb == 1.0
    assert item.type == "disk"


def test_to_listen_port_item_well_known():
    item = _to_listen_port_item({"proto": "tcp", "addr": "0.0.0.0", "port": 80, "uid": 0})
    assert item.is_well_known is True
    assert item.port == 80


def test_to_listen_port_item_high_port():
    item = _to_listen_port_item({"proto": "tcp", "addr": "0.0.0.0", "port": 8080, "uid": 1000})
    assert item.is_well_known is False


def test_to_service_item_without_listen_ports_has_empty_ports():
    """list 화면 — listen_ports 안 줌 → ports=[]"""
    item = _to_service_item({"unit": "nginx.service", "sub": "running"})
    assert item.ports == []
    assert item.category == "web"
    assert item.display_name == "nginx"


def test_to_service_item_with_listen_ports_matches():
    """detail 화면 — listen_ports 주면 matched_ports로 채움"""
    item = _to_service_item(
        {"unit": "nginx.service", "sub": "running"},
        listen_ports=[{"proto": "tcp", "port": 80, "uid": 0, "comm": "nginx"}],
    )
    pairs = {(p.proto, p.port) for p in item.ports}
    assert ("tcp", 80) in pairs


# ─── to_server_list_item ──────────────────────────────────────────────────


def _summary(**overrides) -> ServerSummary:
    base = dict(
        id=1,
        public_id="pub-1",
        host_id="m-1",
        hostname="host",
        os_id="ubuntu",
        os_version="22.04",
        cpu_cores=4,
        mem_total_kb=8 * 1024**2,
        ip_external=None,
        disks=[{"name": "sda", "size_bytes": 100 * 1024**3, "type": "disk"}],
        services=None,
        last_seen_at=datetime.now(UTC),
    )
    base.update(overrides)
    return ServerSummary(**base)


def test_list_item_storage_total_sum():
    summary = _summary(
        disks=[
            {"name": "sda", "size_bytes": 100 * 1024**3, "type": "disk"},
            {"name": "sdb", "size_bytes": 50 * 1024**3, "type": "disk"},
            {"name": "loop0", "size_bytes": 999 * 1024**3, "type": "loop"},  # 가상은 제외
        ]
    )
    item = to_server_list_item(summary)
    assert item.storage_total_gb == 150.0


def test_list_item_known_services_dedup():
    summary = _summary(
        services=[
            {"unit": "nginx.service", "sub": "running"},
            {"unit": "apache2.service", "sub": "running"},  # web 중복 → dedup
            {"unit": "postgresql.service", "sub": "running"},
            {"unit": "ssh.service", "sub": "running"},  # unknown
        ]
    )
    item = to_server_list_item(summary)
    assert {s.category for s in item.known_services} == {"web", "db"}
    assert item.show_unknown_badge is False  # known 있으니 unknown 뱃지 안 보임


def test_list_item_show_unknown_badge_when_all_unknown():
    summary = _summary(
        services=[
            {"unit": "ssh.service", "sub": "running"},
            {"unit": "cron.service", "sub": "running"},
        ]
    )
    item = to_server_list_item(summary)
    assert item.known_services == []
    assert item.show_unknown_badge is True


def test_list_item_no_services_no_badge():
    """services=None은 non-systemd 호스트 — show_unknown_badge=False"""
    summary = _summary(services=None)
    item = to_server_list_item(summary)
    assert item.show_unknown_badge is False


def test_list_item_os_display():
    item = to_server_list_item(_summary(os_id="ubuntu", os_version="22.04"))
    assert item.os_display == "ubuntu 22.04"
    item_partial = to_server_list_item(_summary(os_id=None, os_version="22.04"))
    assert item_partial.os_display == "22.04"
    item_none = to_server_list_item(_summary(os_id=None, os_version=None))
    assert item_none.os_display == "-"


# ─── to_server_detail + enrich (idempotent) ───────────────────────────────


def _detail(**overrides) -> ServerDetail:
    base = dict(
        id=1,
        public_id="pub-1",
        host_id="m-1",
        hostname="host",
        agent_version="1.0.0",
        os_family=None,
        os_id="ubuntu",
        os_version="22.04",
        os_codename="jammy",
        kernel_version="5.15.0",
        cpu_cores=4,
        cpu_model="test-cpu",
        mem_total_kb=8 * 1024**2,
        swap_total_kb=2 * 1024**2,
        boot_time=datetime(2026, 1, 1, tzinfo=UTC),
        ip_internal=["10.0.0.1"],
        ip_external=None,
        disks=[{"name": "sda", "size_bytes": 100 * 1024**3, "type": "disk"}],
        mounts=[],
        services=[
            {"unit": "nginx.service", "sub": "running"},
            {"unit": "ssh.service", "sub": "running"},  # unknown
        ],
        listen_ports=[
            {"proto": "tcp", "addr": "0.0.0.0", "port": 80, "uid": 0, "comm": "nginx"},
            {"proto": "tcp", "addr": "0.0.0.0", "port": 22, "uid": 0, "comm": "sshd"},
        ],
        last_seen_at=datetime.now(UTC),
    )
    base.update(overrides)
    return ServerDetail(**base)


def test_to_server_detail_basic():
    resp = to_server_detail(_detail())
    assert resp.hostname == "host"
    assert len(resp.disks) == 1 and resp.disks[0].name == "sda"
    assert resp.disk_total_gb == 100.0
    assert resp.os_display == "ubuntu 22.04"
    assert resp.cpu_display == "test-cpu 4 cores"


def test_to_server_detail_known_services_with_ports():
    resp = to_server_detail(_detail())
    nginx = next(s for s in resp.known_services if s.category == "web")
    assert any(p.port == 80 for p in nginx.ports)


def test_to_server_detail_key_listen_ports_excludes_service_mapped():
    """key_listen_ports = is_well_known AND 서비스 매핑 포트 제외"""
    resp = to_server_detail(_detail())
    # nginx의 80은 services에 매핑되어 chip 표시 → key_listen_ports에서 제외
    assert all(lp.port != 80 for lp in resp.key_listen_ports)
    # ssh 22는 unknown 카테고리라 매핑 안 됨, well-known이라 key_listen_ports에 포함
    assert any(lp.port == 22 for lp in resp.key_listen_ports)


def test_enrich_server_detail_idempotent():
    """enrich를 두 번 호출해도 결과 동일 — cache_serializer 역직렬화 후 재호출 시나리오."""
    resp = to_server_detail(_detail())
    sorted_before = list(resp.sorted_services)
    known_before = list(resp.known_services)
    key_ports_before = list(resp.key_listen_ports)

    resp_again = enrich_server_detail(resp)

    assert resp_again is resp  # 같은 인스턴스 (mutates in place)
    assert resp.sorted_services == sorted_before
    assert resp.known_services == known_before
    assert resp.key_listen_ports == key_ports_before


# ─── to_storage_detail ────────────────────────────────────────────────────


def test_to_storage_detail_filters_virtual_mounts():
    storage = StorageWithUsage(
        server_id=1,
        public_id="pub-1",
        hostname="h",
        disks=[{"name": "sda", "size_bytes": 10**11, "type": "disk", "major": 8, "minor": 0}],
        inventory_mounts=[
            {"mount": "/", "fstype": "ext4", "total_bytes": 5 * 10**10, "major": 8, "minor": 1},
            {"mount": "/proc", "fstype": "proc", "total_bytes": 0},  # 가상
            {"mount": "/snap/core/123", "fstype": "squashfs", "total_bytes": 10**8},  # 가상
        ],
        mount_usage=[],
        inventory_at=datetime.now(UTC),
    )
    resp = to_storage_detail(storage)
    paths = [m.mount for m in resp.mounts]
    assert "/" in paths
    assert "/proc" not in paths
    assert "/snap/core/123" not in paths


def test_to_storage_detail_device_name_via_major_minor():
    """mount의 (major, minor)로 disk 매핑 — sda + minor 1은 sda의 파티션."""
    storage = StorageWithUsage(
        server_id=1,
        public_id="pub-1",
        hostname="h",
        disks=[{"name": "sda", "size_bytes": 10**11, "type": "disk", "major": 8, "minor": 0}],
        inventory_mounts=[
            {"mount": "/", "fstype": "ext4", "total_bytes": 5 * 10**10, "major": 8, "minor": 1},
        ],
        mount_usage=[],
        inventory_at=datetime.now(UTC),
    )
    resp = to_storage_detail(storage)
    assert resp.mounts[0].device_name == "sda"


# ─── attention 신호 mapper (P2 단위 변환 + badge 분기) ────────────────────


def test_to_disk_warning_item_under_provisioned_at_90():
    """90% 이상 → rec-under_provisioned (위험 색). AttentionRow ViewModel."""
    now = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    raw = DiskUsageWarningRaw(
        public_id="pid",
        hostname="h",
        mount="/data",
        total_bytes=100 * 1024**3,
        avail_bytes=10 * 1024**3,  # 사용률 90%
        last_metric_at=now,  # stale 아님
    )
    item = to_disk_warning_item(raw, now)
    assert item.badge_class == "rec-under_provisioned"
    assert item.badge_text == "90%"
    assert item.link_href == "/servers/pid/storage"
    assert item.link_text == "h"
    assert item.mount_path == "/data"
    assert item.meta_text == "잔여 10.0 / 100.0 GB"
    assert item.meta_at is None  # stale 아니라 None


def test_to_disk_warning_item_warn_below_90():
    """85~90% → badge-warn (주의 amber). stale (24h+) 이면 meta_at·meta_text 갱신."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    metric_ts = now - timedelta(hours=25)  # 24h+ → stale
    raw = DiskUsageWarningRaw(
        public_id="pid",
        hostname="h",
        mount="/var",
        total_bytes=100 * 1024**3,
        avail_bytes=12 * 1024**3,  # 사용률 88%
        last_metric_at=metric_ts,
    )
    item = to_disk_warning_item(raw, now)
    assert item.badge_class == "badge-warn"
    assert item.badge_text == "88%"
    assert item.mount_path == "/var"
    assert "마지막 수집" in item.meta_text  # stale 시 추가 표시
    assert item.meta_at == metric_ts


def test_to_gap_warning_item_under_provisioned_at_30min():
    """30분+ 갭 → rec-under_provisioned."""
    now = datetime(2026, 5, 9, 12, 30, tzinfo=UTC)
    metric_ts = now - timedelta(minutes=35)
    raw = MetricGapWarningRaw(
        public_id="pid",
        hostname="h",
        last_metric_at=metric_ts,
    )
    item = to_gap_warning_item(raw, now)
    # 운영 신호 단일 active 색 — `attn-active` (사용자 의도).
    assert item.badge_class == "attn-active"
    assert item.badge_text == "35분"
    assert item.link_href == "/servers/pid"
    assert item.link_text == "h"
    assert item.meta_at == metric_ts


def test_to_gap_warning_item_right_size_short_gap():
    """5~30분 갭도 attn-active 단일 색 (운영 신호 통일)."""
    now = datetime(2026, 5, 9, 12, 30, tzinfo=UTC)
    raw = MetricGapWarningRaw(
        public_id="pid",
        hostname="h",
        last_metric_at=now - timedelta(minutes=10),
    )
    item = to_gap_warning_item(raw, now)
    assert item.badge_class == "attn-active"
    assert item.badge_text == "10분"


# ─── risk_top mapper 테스트는 2026-05-12 cleanup으로 제거됨 ─────────────
# RiskServerItem dataclass · to_risk_server_item 함수 · latest_disk_max_pct SQL
# · list-risk-cards.js 모두 dead. 위험도 신호는 capacity_warnings + EnvironmentOverview로
# 흡수됨. 신규 신호 단위 테스트는 tests/unit/test_mappers_report.py.
