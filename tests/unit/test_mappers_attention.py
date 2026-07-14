"""attention mapper — build_environment_realtime · build_action_targets 단위 테스트.

capacity-weighted 평균·top_n 피크·포화 카운트(realtime) + 분류순·심각도 정렬·효율 집계(action targets) 검증.
"""

from datetime import UTC, datetime, timedelta

from assessment_engine import recommendation
from assessment_engine.db.dtos.outbound import ReportRowRaw
from assessment_engine.web.services.mappers.attention import (
    _UTIL_COLOR_GAUGE,
    _UTIL_COLOR_NONE,
    _UTIL_DONUT_CIRC,
    build_action_targets,
    build_environment_realtime,
    to_capacity_warning_item,
)

_NOW = datetime(2026, 5, 12, tzinfo=UTC)


# ─── 헬퍼 ────────────────────────────────────────────────────────────────


def _snap(hostname: str, public_id: str, **kw) -> dict:
    """realtime 스냅샷 dict — build_environment_realtime 입력 1건.

    필수 키(hostname/public_id)만 고정, 나머지 값·가중치는 kw 로 주입(미지정은 dict 미포함 = get None).
    """
    d = {"hostname": hostname, "public_id": public_id}
    d.update(kw)
    return d


def _raw(**kw) -> ReportRowRaw:
    """ReportRowRaw 최소 구성 — 모든 신호 축 None 기본, kw 로 발화 축만 채움.

    tests/factories·conftest 수정 금지 규약 상 인라인 구성(지정 파일 한정 편집).
    """
    d = dict(
        server_id=1,
        public_id="a",
        hostname="h",
        os_family=None,
        os_id="ubuntu",
        os_version="22.04",
        os_codename="jammy",
        kernel_version="5.15",
        net_interfaces=[],
        services=None,
        last_seen_at=_NOW,
        cpu_avg_pct=None,
        cpu_p95_pct=None,
        cpu_peak_pct=None,
        mem_avg_pct=None,
        mem_p95_pct=None,
        mem_peak_pct=None,
        iowait_p95_pct=None,
        iowait_peak_pct=None,
        cpu_run_queue_p95=None,
        mem_pages_input_rate_p95=None,
        cpu_cores=2,
        mem_total_bytes=2 * 1024**3,
        block_devices=[{"name": "sda", "size_bytes": 50 * 10**9, "type": "disk"}],
        boot_time=_NOW - timedelta(days=30),
        disk_capacity_driving_mount=None,
        worst_mount_used_pct=None,
        reboot_count=0,
        disk_iops_baseline=None,
        disk_throughput_kbps=None,
        net_rx_kbps=None,
        net_tx_kbps=None,
        cpu_sufficiency=None,
        mem_sufficiency=None,
        procs_blocked_p95=None,
        mem_swap_paging=False,
        disk_await_p95_ms=None,
        disk_capacity_runway_days=None,
        disk_inode_runway_days=None,
        net_retrans_pct=None,
        net_drop_pct=None,
        history_hours=None,
        cpu_burst_ratio=None,
        cpu_trend_slope=None,
        mem_trend_slope=None,
        cpu_steal_p95_pct=None,
        cpu_percore_p95_max=None,
        procs_running_p95=None,
        oom_occurred=False,
    )
    d.update(kw)
    return ReportRowRaw(**d)


# ─── build_environment_realtime — capacity-weighted 평균 ─────────────────


def _two_snaps() -> list[dict]:
    """CPU 코어 가중이 서로 다른 2 스냅샷 — capacity-weighted 와 단순평균이 갈리게 구성."""
    return [
        _snap(
            "h1", "p1", cpu_pct=50.0, mem_pct=50.0, disk_pct=30.0, cpu_cores=2,
            mem_used_bytes=1e9, mem_total_bytes=2e9, fs_used_gb=30.0, fs_total_gb=100.0,
            cpu_sat_index=1.5, disk_sat_index=0.5, mem_pressure=True,
        ),
        _snap(
            "h2", "p2", cpu_pct=80.0, mem_pct=75.0, disk_pct=90.0, cpu_cores=8,
            mem_used_bytes=3e9, mem_total_bytes=4e9, fs_used_gb=90.0, fs_total_gb=100.0,
            cpu_sat_index=0.2, disk_sat_index=2.0, mem_pressure=False,
        ),
    ]


def test_realtime_cpu_capacity_weighted_not_arithmetic():
    """CPU 평균 = Σ(usage%·cores)/Σcores — 코어 가중(단순평균 65 아님).

    (50*2 + 80*8) / (2+8) = 740/10 = 74.0.
    """
    r = build_environment_realtime(total=5, online=2, snapshots=_two_snaps(), last_collected_at=_NOW)
    cpu_bar = r.utilization[0]
    assert cpu_bar.label == "CPU"
    assert cpu_bar.pct == 74.0
    assert cpu_bar.bar_color == _UTIL_COLOR_GAUGE
    assert cpu_bar.dash_length == 74.0 / 100.0 * _UTIL_DONUT_CIRC


def test_realtime_mem_disk_are_used_over_total_ratio():
    """메모리 평균 = Σused/Σtotal*100 (byte 풀 비율, 스냅샷 산술평균 아님). 디스크 용량은 실시간 신호가

    아니라(느린 누적 축) utilization 도넛에서 제외 — CPU·메모리 2개만. mem = 4e9/6e9*100 = 66.7 (round 1).
    """
    r = build_environment_realtime(total=5, online=2, snapshots=_two_snaps(), last_collected_at=_NOW)
    assert r.utilization[1].label == "메모리"
    assert r.utilization[1].pct == 66.7
    assert len(r.utilization) == 2


def test_realtime_online_offline_sample_size():
    """total/online/offline + sample_size = len(snapshots) (호출자가 신선 스냅샷만 전달)."""
    r = build_environment_realtime(total=5, online=2, snapshots=_two_snaps(), last_collected_at=_NOW)
    assert r.total == 5
    assert r.online == 2
    assert r.offline == 3
    assert r.sample_size == 2
    assert r.last_collected_at == _NOW


def test_realtime_saturation_counts():
    """신호 도넛 카운트 — 순간 단일신호(신호명 라벨, 판정어 아님). 개요 dual-gate "포화" 도넛과 구분.

    cpu_sat_index>=1.0(실행 큐 임계) / disk_sat_index>=1.0(응답지연 임계) / mem_pressure truthy(페이징).
    h1: cpu 1.5·disk 0.5·mem_pressure True / h2: cpu 0.2·disk 2.0·mem_pressure False.
    -> 실행 큐 임계 1, 페이징 1, 응답지연 임계 1, 네트워크 혼잡 0 (_two_snaps 는 net_congested 미설정), 표본 2.
    """
    r = build_environment_realtime(total=5, online=2, snapshots=_two_snaps(), last_collected_at=_NOW)
    labels = {d.label: (d.count, d.total) for d in r.saturation_donuts}
    assert labels["실행 큐 임계"] == (1, 2)
    assert labels["페이징"] == (1, 2)
    assert labels["응답지연 임계"] == (1, 2)
    assert labels["네트워크 혼잡"] == (0, 2)  # 발화 카테고리 항상 노출(E9), 미발생이라 0
    # 채움 = count/total * 원주
    rq_donut = next(d for d in r.saturation_donuts if d.label == "실행 큐 임계")
    assert rq_donut.dash_length == (1 / 2) * _UTIL_DONUT_CIRC
    assert rq_donut.color == _UTIL_COLOR_GAUGE


def test_realtime_network_congestion_donut_counts_flagged_hosts():
    """네트워크 혼잡 신호 도넛(4-1) — 스냅샷 net_congested True 호스트 수 집계."""
    snaps = [
        _snap("h1", "p1", cpu_cores=1, net_congested=True),
        _snap("h2", "p2", cpu_cores=1, net_congested=False),
        _snap("h3", "p3", cpu_cores=1),  # net_congested 미설정 = None(미발생)
    ]
    r = build_environment_realtime(total=3, online=3, snapshots=snaps, last_collected_at=_NOW)
    labels = {d.label: (d.count, d.total) for d in r.saturation_donuts}
    assert labels["네트워크 혼잡"] == (1, 3)


def test_realtime_peaks_sorted_descending_with_display():
    """peak_groups 자원별 top — value DESC 정렬 + display '{pct}%' precompute."""
    r = build_environment_realtime(total=5, online=2, snapshots=_two_snaps(), last_collected_at=_NOW)
    cpu_group = next(g for g in r.peak_groups if g.label == "CPU 이용률")
    assert [p.hostname for p in cpu_group.peaks] == ["h2", "h1"]
    assert [p.value for p in cpu_group.peaks] == [80.0, 50.0]
    assert [p.display for p in cpu_group.peaks] == ["80.0%", "50.0%"]
    assert r.has_peaks is True


def test_realtime_top_n_truncates_and_skips_none():
    """top_n 절단 + value None 스냅샷은 랭킹·평균 모두 제외(가중치 den 기여 안 함).

    cpu_pct None 인 b 제외 -> CPU avg = (10*1 + 90*1)/(1+1) = 50.0, top1 = c(90).
    sample_size 는 전달 스냅샷 수(None 포함) = 3 — 신선 표본은 값 유무와 무관.
    """
    snaps = [
        _snap("a", "pa", cpu_pct=10.0, cpu_cores=1),
        _snap("b", "pb", cpu_pct=None, cpu_cores=4),
        _snap("c", "pc", cpu_pct=90.0, cpu_cores=1),
    ]
    r = build_environment_realtime(total=3, online=3, snapshots=snaps, last_collected_at=None, top_n=1)
    assert r.utilization[0].pct == 50.0
    cpu_group = next(g for g in r.peak_groups if g.label == "CPU 이용률")
    assert [p.hostname for p in cpu_group.peaks] == ["c"]
    assert r.sample_size == 3


def test_realtime_empty_snapshots_none_avgs_and_no_peaks():
    """빈 스냅샷 — 평균 None(회색 색·dash 0) / 피크 없음 / 포화 카운트 0 / sample 0."""
    r = build_environment_realtime(total=3, online=0, snapshots=[], last_collected_at=None)
    for bar in r.utilization:
        assert bar.pct is None
        assert bar.bar_color == _UTIL_COLOR_NONE
        assert bar.dash_length == 0.0
    assert r.sample_size == 0
    assert r.offline == 3
    assert r.has_peaks is False
    assert all(d.count == 0 and d.total == 0 for d in r.saturation_donuts)
    assert all(d.dash_length == 0.0 for d in r.saturation_donuts)


# ─── build_action_targets — 분류순·심각도 정렬 + 효율 집계 ────────────────


def _classified_raws() -> list[ReportRowRaw]:
    """5 분류 각 1대 — 삽입 순서를 정렬 기대와 어긋나게(insuff 먼저) 배치해 정렬 검증 유효화."""
    return [
        _raw(hostname="x", public_id="px"),  # insufficient_data
        _raw(hostname="op", public_id="pop", cpu_p95_pct=50.0, mem_p95_pct=85.0),  # optimal
        _raw(hostname="i", public_id="pi", cpu_p95_pct=2.0, net_rx_kbps=0.0, net_tx_kbps=0.0),  # idle
        _raw(hostname="o", public_id="po", cpu_p95_pct=20.0, mem_p95_pct=30.0),  # over_provisioned
        _raw(hostname="u", public_id="pu", mem_p95_pct=92.0, mem_swap_paging=True),  # under_provisioned
    ]


def test_action_targets_sorted_by_classification_order():
    """최초 정렬 = 분류 우선순위(부족0>과다1>유휴2>정상3>표본4) — 삽입 순서 무관."""
    at = build_action_targets(_classified_raws())
    assert [h.classification for h in at.hosts] == [
        "under_provisioned",
        "over_provisioned",
        "idle",
        "optimal",
        "insufficient_data",
    ]
    # rank 도 동반 노출(정렬 키 파생)
    assert [h.classification_rank for h in at.hosts] == [0, 1, 2, 3, 4]


def test_action_targets_severity_then_hostname_tiebreak():
    """동일 분류 내부 정렬 = 심각도 DESC 후 hostname ASC (동률 tie-break).

    metric 동일 over 2대 -> severity 동률 -> hostname 오름차순(alpha 먼저).
    """
    raws = [
        _raw(hostname="zebra", public_id="pz", cpu_p95_pct=20.0, mem_p95_pct=30.0),
        _raw(hostname="alpha", public_id="pa", cpu_p95_pct=20.0, mem_p95_pct=30.0),
    ]
    at = build_action_targets(raws)
    assert all(h.classification == "over_provisioned" for h in at.hosts)
    assert [h.hostname for h in at.hosts] == ["alpha", "zebra"]


def test_action_targets_counts_and_metric_labels():
    """total = 전 행수, under_count = 자원 부족 수, metric_labels = 첫 행 지표 라벨 precompute."""
    at = build_action_targets(_classified_raws())
    assert at.total == 5
    assert at.under_count == 1
    # 첫 행(under)의 metrics 라벨과 동일 — host_status 구동 5축(CPU 이용률/포화·메모리 이용률/포화·디스크 용량).
    # 디스크 I/O·네트워크는 host_status 미구동 orthogonal advisory 축이라 표에서 제외(각자 전용 채널로 노출).
    assert at.metric_labels == [m.label for m in at.hosts[0].metrics]
    assert len(at.metric_labels) == 5


def test_action_targets_efficiency_aggregates_over_and_idle_only():
    """효율 집계 = over_provisioned·idle 분류만 합산 (under/optimal/insufficient 제외).

    over(cores2·mem2GiB·disk50e9) + idle(cores2·mem2GiB·disk50e9) 2대:
      count=2 / vcpus=4 / mem=4.0GB / disk=int(bytes_to_gb(100e9))=93 (binary divisor, GB 라벨 표기 관례).
    """
    at = build_action_targets(_classified_raws())
    assert at.efficiency_count == 2
    assert at.efficiency_vcpus == 4
    assert at.efficiency_memory_gb == 4.0
    assert at.efficiency_disk_gb == 93


def test_action_targets_empty_raws():
    """빈 입력 — 행 0, 라벨 빈 list, 카운트·효율 전부 0 (items[0] 참조 가드)."""
    at = build_action_targets([])
    assert at.hosts == []
    assert at.metric_labels == []
    assert at.total == 0
    assert at.under_count == 0
    assert at.efficiency_count == 0
    assert at.efficiency_vcpus == 0
    assert at.efficiency_memory_gb == 0.0
    assert at.efficiency_disk_gb == 0


def test_action_targets_reuses_capacity_warning_classification():
    """build_action_targets 행 = to_capacity_warning_item 결과 재사용 (요청당 rollup 1회, 동일 산식 #E3)."""
    raw = _raw(hostname="u", public_id="pu", mem_p95_pct=92.0, mem_swap_paging=True)
    direct = to_capacity_warning_item(raw)
    at = build_action_targets([raw])
    assert at.hosts[0].classification == direct.classification
    assert at.hosts[0].severity_score == direct.severity_score
    assert at.hosts[0].classification == "under_provisioned"
    assert recommendation.CLASSIFICATION_ORDER[at.hosts[0].classification] == 0
