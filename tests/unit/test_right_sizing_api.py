"""right-sizing API 매퍼(build_right_sizing_entry) 테스트 — 외부 계약 보증.

이 API 는 외부 자동화가 파싱해 프로비저닝을 결정하므로 계약이 깨지면 안 된다:
- saturation·network 신호는 raw number(표시 문자열 아님).
- recommendation 은 구조 {summary, kind, actions[], suppressed[]} — actions 는 근본원인만(인과 억제).
- 사이징 목표는 타입 키(target_cores/_mb/_gb)로 파싱 가능.
"""

from assessment_engine.web.services.mappers.right_sizing_api import build_right_sizing_entry
from tests.unit.test_mappers_report import _raw


def _under_mem_root():
    """메모리 page-out 이 CPU·디스크 I/O 를 유발한 인과 결합 — root=memory, 나머지 증상."""
    return _raw(
        os_family="linux",
        cpu_p95=88.0,  # cpu util under
        mem_p95=94.0,  # mem util under
        cpu_cores=4,
        procs_running_p95=6.0,  # cpu 포화
        mem_swap_paging=True,  # 메모리 page-out (근본원인 판별)
        disk_await_p95_ms=25.0,  # disk io_bound
        mem_total_kb=8 * 1024 * 1024,
    )


def test_saturation_is_raw_numeric_not_display_string():
    """saturation.value/threshold 는 number, unit 동반 — 표시 문자열('W 1.20') 누수 금지(계약)."""
    e = build_right_sizing_entry(
        _raw(os_family="linux", cpu_p95=40.0, cpu_cores=4, procs_running_p95=6.0), is_online=True
    )
    sat = e["resources"]["cpu"]["saturation"]
    assert isinstance(sat["value"], (int, float)) and sat["value"] == 1.5  # 6/4 per-core
    assert isinstance(sat["threshold"], (int, float)) and sat["threshold"] == 1.0
    assert sat["unit"] == "per_core"
    assert sat["measured"] is True and sat["saturated"] is True
    assert sat["signal"] == "run queue (procs_running)/core"


def test_disk_io_saturation_await_numeric():
    """디스크 I/O await 도 numeric ms."""
    e = build_right_sizing_entry(_raw(os_family="linux", disk_await_p95_ms=30.0), is_online=True)
    sat = e["resources"]["disk"]["io"]["saturation"]
    assert sat["value"] == 30.0 and sat["threshold"] == 20 and sat["unit"] == "ms"
    assert sat["saturated"] is True


def test_saturation_unmeasured_is_null():
    """미측정 축은 value/saturated null (measured=false)."""
    e = build_right_sizing_entry(_raw(os_family="windows", cpu_p95=40.0, cpu_cores=4), is_online=True)
    sat = e["resources"]["cpu"]["saturation"]
    assert sat["value"] is None and sat["measured"] is False


def test_recommendation_structure_and_causal_suppression():
    """under: kind=provision, actions 는 근본원인(memory)만, 증상(cpu·disk_io)은 suppressed."""
    e = build_right_sizing_entry(_under_mem_root(), is_online=True)
    assert e["classification"] == "under_provisioned"
    rec = e["recommendation"]
    assert set(rec) == {"summary", "kind", "actions", "suppressed"}
    assert rec["kind"] == "provision"
    action_res = {a["resource"] for a in rec["actions"]}
    assert action_res == {"memory"}  # 근본원인만
    assert set(rec["suppressed"]) == {"cpu", "disk_io"}  # 증상 — 독립 조치 금지


def test_action_target_is_typed_int():
    """actions 사이징 목표는 타입 키로 파싱 가능(정수)."""
    e = build_right_sizing_entry(_under_mem_root(), is_online=True)
    mem = next(a for a in e["recommendation"]["actions"] if a["resource"] == "memory")
    assert mem["op"] == "increase" and isinstance(mem["target_mb"], int) and mem["target_mb"] > 0


def test_independent_under_actions_all_present():
    """인과 결합 없는 독립 부족 — actions 에 각 자원, suppressed 없음."""
    e = build_right_sizing_entry(
        _raw(os_family="linux", cpu_p95=88.0, cpu_cores=4, disk_await_p95_ms=25.0, mem_p95=50.0),
        is_online=True,
    )
    rec = e["recommendation"]
    action_res = {a["resource"] for a in rec["actions"]}
    assert "cpu" in action_res and "disk_io" in action_res
    assert rec["suppressed"] == []
    tier = next(a for a in rec["actions"] if a["resource"] == "disk_io")
    assert tier["op"] == "tier_up" and "target_gb" not in tier  # 수치 목표 없음


def test_optimal_maintain_no_actions():
    """정상 — kind=maintain, actions/suppressed 빈 배열. 이용률 70% 목표 == 현재 사양(축소·증설 여지 없음)."""
    e = build_right_sizing_entry(
        # cpu 65% -> 목표 ceil(65*4/70)=4=현재 / mem 75% -> 목표 > 현재(축소 불가) : 둘 다 optimal
        _raw(os_family="linux", cpu_p95=65.0, mem_p95=75.0, cpu_cores=4, procs_running_p95=1.0),
        is_online=True,
    )
    rec = e["recommendation"]
    assert rec["kind"] == "maintain" and rec["actions"] == [] and rec["suppressed"] == []


def test_network_signals_numeric_and_congested():
    """네트워크 신호는 numeric — 재전송>1% 면 congested + exceeded=true."""
    e = build_right_sizing_entry(
        _raw(os_family="linux", cpu_p95=50.0, cpu_cores=4, net_retrans_pct=2.0, net_drop_pct=0.0),
        is_online=True,
    )
    net = e["network"]
    assert net["status"] == "congested" and net["congested"] is True
    rt = net["signals"]["retransmit_pct"]
    assert rt["value"] == 2.0 and rt["threshold"] == 1.0 and rt["exceeded"] is True
    assert net["signals"]["drop_pct"]["exceeded"] is False


def test_hostname_ambiguous_flag_passthrough():
    """hostname_ambiguous 안전 신호 반영."""
    e = build_right_sizing_entry(_raw(os_family="linux"), is_online=True, hostname_ambiguous=True)
    assert e["hostname_ambiguous"] is True
    e2 = build_right_sizing_entry(_raw(os_family="linux"), is_online=True)
    assert e2["hostname_ambiguous"] is False


def test_current_resource_values_paired_with_target():
    """현재 배정값(current_cores/mb/gb)이 목표와 짝 — 소비자가 current -> target 리사이즈 판단."""
    e = build_right_sizing_entry(_under_mem_root(), is_online=True)  # cpu_cores=4, mem 8GB
    r = e["resources"]
    assert r["cpu"]["current_cores"] == 4
    assert r["memory"]["current_mb"] == 8192  # 8*1024*1024 KB -> 8192 MB
    assert "current_gb" in r["disk"]["capacity"]  # 디스크 미측정이면 null


def test_classification_and_labels_present():
    """분류·라벨·근본원인 필드 형태."""
    e = build_right_sizing_entry(_under_mem_root(), is_online=True)
    assert e["classification"] == "under_provisioned"
    assert isinstance(e["classification_label"], str) and e["classification_label"]
    assert "메모리" in (e["root_cause"] or "")
