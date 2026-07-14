"""mappers — Outbound DTO → ViewModel + enrich idempotent 검증."""

from datetime import UTC, datetime, timedelta

import pytest

from assessment_engine.db.dtos.outbound import (
    MetricGapWarningRaw,
    MountUsageRaw,
    ReportRowRaw,
    ServerDetail,
    ServerSummary,
    StorageWithUsage,
)
from assessment_engine.web.services.mappers.attention import (
    to_gap_warning_item,
)
from assessment_engine.web.services.mappers.server import (
    _to_disk_item,
    _to_listen_port_item,
    _to_service_item,
    _usage_badge_class,
    _usage_severity,
    build_server_inventory,
    enrich_server_detail,
    infer_role,
    to_server_detail,
    to_server_list_item,
    to_storage_detail,
    workload_category_counter,
)
from assessment_engine.web.services.mappers.shared import spec_display_line, windows_legacy_version_from_build

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


# ─── raw dict → ViewModel 변환 ────────────────────────────────────────────


def test_to_disk_item_returns_none_for_non_physical():
    assert _to_disk_item({"name": "sda1"}) is None
    assert _to_disk_item({"name": "dm-0"}) is None
    assert _to_disk_item({"name": "loop0"}) is None


def test_to_disk_item_for_physical():
    item = _to_disk_item({"name": "sda", "size_bytes": 10**9, "type": "disk", "kind": "physical"})
    assert item.name == "sda"
    assert item.size_gb == 0.93  # bytes_to_gb binary divisor(GB 라벨 표기, df -h 관례) — decimal 1.0 아님


def test_to_listen_port_item_significant():
    # 비동적 포트(< 49152)는 significant — RDP 3389·app 8080 등 고포트 포함
    for port in (80, 3389, 8080):
        assert _to_listen_port_item({"proto": "tcp", "addr": "0.0.0.0", "port": port, "uid": 0}).is_significant is True


def test_to_listen_port_item_dynamic_port():
    # 동적/ephemeral(>= 49152)은 제외 (RPC 동적 할당 등 노이즈)
    item = _to_listen_port_item({"proto": "tcp", "addr": "0.0.0.0", "port": 49500, "uid": 1000})
    assert item.is_significant is False


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
        composite_id="m-1",
        hostname="host",
        os_id="ubuntu",
        os_version="22.04",
        kernel_version=None,
        cpu_cores=4,
        mem_total_bytes=8 * 1024**3,  # v2 By (v1 mem_total_kb 폐기)
        ip_external=None,
        block_devices=[{"name": "sda", "size_bytes": 100 * 10**9, "type": "disk"}],
        service_categories=[],
        last_seen_at=datetime.now(UTC),
    )
    base.update(overrides)
    return ServerSummary(**base)


def test_list_item_storage_total_sum():
    summary = _summary(
        block_devices=[
            {"name": "sda", "size_bytes": 100 * 10**9, "type": "disk"},
            {"name": "sdb", "size_bytes": 50 * 10**9, "type": "disk"},
            {"name": "loop0", "size_bytes": 999 * 10**9, "type": "loop"},  # 물리 아님(type!=disk) → 제외
        ]
    )
    item = to_server_list_item(summary)
    assert item.storage_total_gb == 139.7  # bytes_to_gb binary divisor(GB 라벨) — decimal (100+50)=150 아님


def test_list_item_badges_from_service_categories():
    """뱃지는 ingest 사전계산 service_categories(키 집합) 소비 — 카테고리당 ServiceItem 1개 (E7)."""
    summary = _summary(service_categories=["db", "web"])
    item = to_server_list_item(summary)
    assert {s.category for s in item.known_services} == {"web", "db"}
    assert item.show_unknown_badge is False


def test_list_item_no_categories_no_badge():
    """service_categories 빈 집합 -> 뱃지 없음. show_unknown_badge 는 목록에서 항상 False (저장 집합만, T15)."""
    summary = _summary(service_categories=[])
    item = to_server_list_item(summary)
    assert item.known_services == []
    assert item.show_unknown_badge is False


def test_list_item_os_display():
    item = to_server_list_item(_summary(os_id="ubuntu", os_version="22.04"))
    assert item.os_display == "ubuntu 22.04"
    item_partial = to_server_list_item(_summary(os_id=None, os_version="22.04"))
    assert item_partial.os_display == "22.04"
    item_none = to_server_list_item(_summary(os_id=None, os_version=None))
    assert item_none.os_display == "-"


@pytest.mark.parametrize(
    "kernel_version,expected",
    [
        ("9600", "2012 R2"),
        ("9200", "2012"),
        ("7601", "2008 R2"),
        ("6003", "2008"),
        ("3790", "2003"),
        ("2195", "2000"),
        ("9200.1234", "2012"),  # UBR/revision suffix 무시 (build 만)
        ("14393", None),  # Server 2016 — 비레거시(보강 범위 밖)
        ("19045", None),  # Windows 10 — 비레거시
        ("", None),
        (None, None),
    ],
)
def test_windows_legacy_version_from_build(kernel_version, expected):
    assert windows_legacy_version_from_build(kernel_version) == expected


def test_list_item_os_display_windows_legacy_from_build():
    # 레거시 Windows Server 는 agent 가 os_version 빈값으로 보냄 -> kernel build 로 보강
    item = to_server_list_item(_summary(os_id="windows", os_version="", kernel_version="9200"))
    assert item.os_display == "windows 2012"
    # os_version 있으면(클라이언트 DisplayVersion 등) 그대로 우선 — build 보강 안 함
    item_ver = to_server_list_item(_summary(os_id="windows", os_version="22H2", kernel_version="19045"))
    assert item_ver.os_display == "windows 22H2"
    # 비레거시(2016+) 빈 os_version 은 미보강 — "windows" 만 (보강 범위는 레거시 한정)
    item_2016 = to_server_list_item(_summary(os_id="windows", os_version="", kernel_version="14393"))
    assert item_2016.os_display == "windows"


# ─── to_server_detail + enrich (idempotent) ───────────────────────────────


def _detail(**overrides) -> ServerDetail:
    base = dict(
        id=1,
        public_id="pub-1",
        agent_id="00000000-0000-4000-8000-000000000001",
        composite_id="m-1",
        machine_id=None,
        hostname="host",
        agent_version="1.0.0",
        os_family=None,
        os_id="ubuntu",
        os_version="22.04",
        os_codename="jammy",
        kernel_version="5.15.0",
        cpu_cores=4,
        cpu_model="test-cpu",
        cpu_arch="x86_64",
        cpu_bits=64,
        mem_total_bytes=8 * 1024**3,  # v2 By (v1 mem_total_kb 폐기)
        boot_time=datetime(2026, 1, 1, tzinfo=UTC),
        agent_started_at=datetime(2026, 1, 1, tzinfo=UTC),
        # v2 net_interfaces — kind 는 인터페이스 레벨, 주소는 nested addresses[] (v1 flat address/prefix/family 폐기).
        net_interfaces=[
            {
                "id": "52:54:00:12:34:56",
                "id_type": "mac",
                "name": "eth0",
                "kind": "physical",
                "speed_mbps": 1000,
                "gateway": "10.0.0.254",
                "addresses": [{"address": "10.0.0.1", "prefix": 24, "family": "ipv4"}],
            }
        ],
        ip_external=None,
        # v2 block_devices (swap 은 type=swap 노드로 표현 — v1 swap_total_kb 폐기).
        block_devices=[{"name": "sda", "size_bytes": 100 * 10**9, "type": "disk"}],
        lvm_vgs=[],
        services=[
            {"unit": "nginx.service", "sub": "running"},
            {"unit": "ssh.service", "sub": "running"},  # remote (SSH)
        ],
        listen_ports=[
            {"proto": "tcp", "addr": "0.0.0.0", "port": 80, "uid": 0, "comm": "nginx"},
            {"proto": "tcp", "addr": "0.0.0.0", "port": 22, "uid": 0, "comm": "sshd"},
            {"proto": "tcp", "addr": "0.0.0.0", "port": 9999, "uid": 0, "comm": "customapp"},
        ],
        last_seen_at=datetime.now(UTC),
    )
    base.update(overrides)
    return ServerDetail(**base)


def test_to_server_detail_basic():
    resp = to_server_detail(_detail())
    assert resp.hostname == "host"
    assert len(resp.disks) == 1 and resp.disks[0].name == "sda"
    assert resp.disk_total_gb == 93.13  # bytes_to_gb binary divisor(GB 라벨) — decimal 100.0 아님
    assert resp.os_display == "ubuntu 22.04"
    assert resp.cpu_display == "test-cpu 4 cores"


def test_to_server_detail_known_services_with_ports():
    resp = to_server_detail(_detail())
    nginx = next(s for s in resp.known_services if s.category == "web")
    assert any(p.port == 80 for p in nginx.ports)


def test_to_server_detail_key_listen_ports_excludes_service_mapped():
    """key_listen_ports = is_significant(비동적) AND 서비스 매핑 포트 제외"""
    resp = to_server_detail(_detail())
    # nginx 80(web)·ssh 22(remote)는 services 에 매핑 → key_listen_ports 제외
    assert all(lp.port not in (80, 22) for lp in resp.key_listen_ports)
    # 9999 는 어느 서비스에도 매핑 안 됨 + 비동적 → key_listen_ports 포함
    assert any(lp.port == 9999 for lp in resp.key_listen_ports)


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


# ─── workload union (ADR 0032) — services 이름 ∪ listen 소켓 탐지 ─────────────


def test_workload_counter_listen_only_rescues_opaque_name():
    """이름 분류 불가(opaque) 서비스도 listen 소켓 증거로 카테고리 구제."""
    services = [{"unit": "MyCorpThing", "sub": "running"}]  # 이름 미매치
    listen = [
        {"proto": "tcp", "port": 1433, "comm": "sqlservr"},  # SQL Server
        {"proto": "tcp", "port": 22, "comm": "sshd"},  # SSH -> baseline(관리) 제외
    ]
    assert dict(workload_category_counter(services, listen)) == {"db": 1}


def test_workload_counter_no_double_count():
    """이름·listen 이 같은 워크로드를 가리켜도 이중 카운트 안 함 (nginx 1대)."""
    services = [{"unit": "nginx.service", "sub": "running"}]
    listen = [
        {"proto": "tcp", "port": 80, "comm": "nginx"},
        {"proto": "tcp", "port": 443, "comm": "nginx"},
    ]
    assert dict(workload_category_counter(services, listen)) == {"web": 1}


def test_workload_counter_container_single_instance():
    """런타임 스택(container)은 docker+containerd 가 떠도 호스트당 1. 일반 카테고리는 인스턴스 카운트 유지."""
    services = [
        {"unit": "docker.service", "sub": "running"},
        {"unit": "containerd.service", "sub": "running"},
        {"unit": "nginx.service", "sub": "running"},
        {"unit": "apache2.service", "sub": "running"},  # web 2대
    ]
    counter = dict(workload_category_counter(services, []))
    assert counter == {"container": 1, "web": 2}


def test_enrich_container_single_badge():
    """detail 뱃지도 container 는 첫 unit 1개만 (docker+containerd → container 1뱃지)."""
    resp = to_server_detail(
        _detail(
            os_family="linux",
            services=[
                {"unit": "docker.service", "sub": "running"},
                {"unit": "containerd.service", "sub": "running"},
            ],
            listen_ports=[],
        )
    )
    containers = [s for s in resp.known_services if s.category == "container"]
    assert len(containers) == 1


def test_workload_counter_listen_port_only_comm_null():
    """comm null(Windows 권한 부족)이어도 port 신호로 탐지."""
    services = [{"unit": "opaque", "sub": "running"}]
    listen = [{"proto": "tcp", "port": 6379, "comm": None}]
    assert dict(workload_category_counter(services, listen)) == {"cache": 1}


def test_infer_role_union_dominant():
    services = [{"unit": "MSSQL$PROD", "sub": "running"}, {"unit": "W3SVC", "sub": "running"}]
    listen = [{"proto": "tcp", "port": 1433, "comm": "sqlservr"}]
    # name: db + web (각 1), listen db 는 이미 포함 -> 이중카운트 없음. 동률이면 most_common 안정.
    assert infer_role(services, listen) in {"db", "web"}


def test_enrich_listen_only_synthetic_badge():
    """opaque 서비스 + listen 1433 -> db 합성 뱃지(unit 없음), show_unknown 아님."""
    resp = to_server_detail(
        _detail(
            os_family="windows",
            services=[{"unit": "MyCorpThing", "sub": "running"}],
            listen_ports=[{"proto": "tcp", "addr": "0.0.0.0", "port": 1433, "uid": None, "comm": "sqlservr"}],
        )
    )
    db = next(s for s in resp.known_services if s.category == "db")
    assert db.unit == "" and db.display_name == ""  # 특정 service 귀속 불가
    assert any(p.port == 1433 for p in db.ports)
    assert resp.show_unknown_badge is False  # listen 으로 구제됨


def test_enrich_category_port_aggregation_web_iis():
    """comm 귀속 실패한 워크로드 포트도 카테고리 단위로 뱃지에 집계 (W3SVC<->System 의 80/443)."""
    resp = to_server_detail(
        _detail(
            os_family="windows",
            services=[{"unit": "W3SVC", "sub": "running"}],  # IIS — comm 은 System 이라 unit 미매칭
            listen_ports=[
                {"proto": "tcp", "addr": "0.0.0.0", "port": 80, "uid": None, "comm": "System"},
                {"proto": "tcp6", "addr": "::", "port": 80, "uid": None, "comm": "System"},
            ],
        )
    )
    web = next(s for s in resp.known_services if s.category == "web")
    port_pairs = {(p.proto, p.port) for p in web.ports}
    assert ("tcp", 80) in port_pairs and ("tcp6", 80) in port_pairs  # 카테고리 집계로 귀속
    assert all(lp.port != 80 for lp in resp.key_listen_ports)  # 뱃지로 갔으니 주요 Listen 에서 제외


# ─── to_storage_detail ────────────────────────────────────────────────────


def test_to_storage_detail_filters_virtual_mounts():
    storage = StorageWithUsage(
        server_id=1,
        public_id="pub-1",
        hostname="h",
        block_devices=[{"name": "sda", "size_bytes": 10**11, "type": "disk"}],
        lvm_vgs=[],
        # v2: 마운트 사용량은 filesystems(df 시계열) 단일 소스 — 가상 제외는 fstype in VIRTUAL_FSTYPES 기준.
        filesystems=[
            MountUsageRaw(
                mountpoint="/", fstype="ext4", used_bytes=3 * 10**10, free_bytes=2 * 10**10,
                collected_at=datetime.now(UTC),
            ),
            MountUsageRaw(mountpoint="/proc", fstype="proc"),  # 가상 fstype -> 제외
            MountUsageRaw(mountpoint="/snap/core/123", fstype="squashfs"),  # 가상 fstype -> 제외
        ],
        inventory_at=datetime.now(UTC),
    )
    resp = to_storage_detail(storage)
    paths = [m.mount for m in resp.mounts]
    assert "/" in paths
    assert "/proc" not in paths
    assert "/snap/core/123" not in paths


# v2 는 device 부모-자식 조인을 노드 `parent`(부모 id)로 하며 major/minor 는 폐기 — storage detail 은
# device_name 을 산출하지 않는다. block_device 트리 술어 검증은 test_device_filters.py 단일 진실.


# ─── attention 신호 mapper (P2 단위 변환 + badge 분기) ────────────────────
# 운영신호는 gap/os_eol/agent_unstable 3개 — disk 신호는 USE Method 로 이동(코드 제거).


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


# ─── build_server_inventory (개별 보고서 인벤토리, raw 선택적 보강) ────────────


def _min_raw(**overrides) -> ReportRowRaw:
    base = dict(
        server_id=1, public_id="p1", hostname="h", os_family="linux", os_id="ubuntu", os_version="22.04",
        os_codename="jammy", kernel_version="5.15", net_interfaces=[], services=[], last_seen_at=None,
        cpu_p95_pct=None, cpu_avg_pct=None, cpu_peak_pct=None, mem_p95_pct=None, mem_avg_pct=None, mem_peak_pct=None,
    )
    base.update(overrides)
    return ReportRowRaw(**base)


def test_build_server_inventory_enriches_from_raw():
    """raw(ReportRowRaw) 제공 시 재현 필드(arch/bits/boot_firmware/secure_boot/edition/timezone) 채움."""
    detail = _detail()
    raw = _min_raw(boot_firmware="uefi", secure_boot=True, edition="Datacenter", timezone="Asia/Seoul")

    snap = build_server_inventory(detail, True, raw)

    assert snap.public_id == detail.public_id
    assert snap.agent_id == detail.agent_id
    assert snap.cpu_arch == detail.cpu_arch
    assert snap.cpu_bits == detail.cpu_bits
    assert snap.boot_firmware == "uefi"
    assert snap.secure_boot is True
    assert snap.os_edition == "Datacenter"
    assert snap.timezone == "Asia/Seoul"


def test_spec_display_line_missing_values_show_dash():
    """cpu_cores/mem_total_bytes/block_devices 부재는 각자 "—" — 서버 목록·환경 자원 평가 compact 표 공용."""
    assert spec_display_line(None, None, None) == "— · — · —"
    assert spec_display_line(4, None, []) == "4코어 · — · —"


def test_build_server_inventory_raw_none_leaves_reproduction_fields_none():
    """raw 미보유 스코프(환경·N대 등) — 재현 필드만 None, 나머지 detail 파생은 정상."""
    snap = build_server_inventory(_detail(), False, None)

    assert snap.boot_firmware is None
    assert snap.secure_boot is None
    assert snap.os_edition is None
    assert snap.timezone is None
    assert snap.hostname == "host"
