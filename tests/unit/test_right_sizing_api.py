"""right-sizing API 매퍼(build_right_sizing_entry) 테스트 — 외부 계약 보증.

이 API 는 외부 자동화가 파싱해 프로비저닝을 결정하므로 계약이 깨지면 안 된다:
- saturation·network 신호는 raw number(표시 문자열 아님).
- recommendation 은 구조 {summary, kind, actions[], suppressed[]} — actions 는 관측된 under 자원 전부(자원별
  독립, 억제 없음. ADR 0055). suppressed 는 항상 빈 배열(구 스키마 호환).
- 사이징 목표는 타입 키(target_cores/_mb/_gb)로 파싱 가능.
"""

from datetime import UTC, datetime

from assessment_engine.db.dtos.outbound import ReportRowRaw
from assessment_engine.web.services.mappers.right_sizing_api import build_right_sizing_entry
from assessment_engine.web.view_models.right_sizing_api import RightSizingServer

_NOW = datetime(2026, 5, 12, tzinfo=UTC)


def _raw(
    *,
    server_id: int = 1,
    public_id: str = "a",
    hostname: str = "h",
    os_family: str | None = None,
    os_id: str = "ubuntu",
    os_version: str = "22.04",
    cpu_p95: float | None = None,
    cpu_peak: float | None = None,
    mem_p95: float | None = None,
    cpu_cores: int | None = 2,
    mem_total_bytes: int | None = 2 * 1024**3,  # v2 By (v1 mem_total_kb 폐기)
    procs_running_p95: float | None = None,
    mem_swap_paging: bool = False,
    disk_await_p95_ms: float | None = None,
    net_retrans_pct: float | None = None,
    net_drop_pct: float | None = None,
) -> ReportRowRaw:
    """right-sizing API 테스트용 최소 v2 ReportRowRaw 빌더.

    본 파일 전용(자기완결) — 시계열/inventory wire 계약이 아니라 report_aggregate 산출 outbound DTO 다.
    v2 필드만 채운다: mem_total_bytes(v1 kB 폐기)·net_interfaces(v1 interfaces 폐기). 포화 신호는
    procs_running_p95/disk_await_p95_ms 등 v2 축(load/iowait/swap_used 폐기).
    """
    return ReportRowRaw(
        server_id=server_id,
        public_id=public_id,
        hostname=hostname,
        os_family=os_family,
        os_id=os_id,
        os_version=os_version,
        os_codename="jammy",
        kernel_version="5.15",
        net_interfaces=[
            {
                "id": "52:54:00:12:34:56",
                "id_type": "mac",
                "name": "eth0",
                "kind": "physical",
                "addresses": [{"address": "10.0.0.1", "prefix": 24, "family": "ipv4"}],
            }
        ],
        services=None,
        last_seen_at=_NOW,
        cpu_p95_pct=cpu_p95,
        cpu_avg_pct=None,
        cpu_peak_pct=cpu_peak,
        mem_p95_pct=mem_p95,
        mem_avg_pct=None,
        mem_peak_pct=None,
        cpu_cores=cpu_cores,
        mem_total_bytes=mem_total_bytes,
        procs_running_p95=procs_running_p95,
        mem_swap_paging=mem_swap_paging,
        disk_await_p95_ms=disk_await_p95_ms,
        net_retrans_pct=net_retrans_pct,
        net_drop_pct=net_drop_pct,
    )


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
        mem_total_bytes=8 * 1024**3,  # v2 By: 8 GiB -> mem_total_mb 8192
    )


def test_saturation_is_raw_numeric_not_display_string():
    """saturation.value/threshold 는 number, unit 동반 — 표시 문자열('W 1.20') 누수 금지(계약)."""
    e = build_right_sizing_entry(
        _raw(os_family="linux", cpu_p95=75.0, cpu_cores=4, procs_running_p95=6.0), is_online=True
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


def test_recommendation_structure_independent_actions_despite_causal_link():
    """under: kind=provision, actions 는 인과 결합(memory 근본원인)이어도 관측된 under 자원 전부(memory·cpu·
    disk_io) 포함 — ADR 0055 자원별 독립 처방. suppressed 는 항상 빈 배열(구 스키마 호환 필드)."""
    e = build_right_sizing_entry(_under_mem_root(), is_online=True)
    assert e["classification"] == "under_provisioned"
    rec = e["recommendation"]
    assert set(rec) == {"summary", "kind", "actions", "suppressed"}
    assert rec["kind"] == "provision"
    action_res = {a["resource"] for a in rec["actions"]}
    assert action_res == {"memory", "cpu", "disk_io"}  # 인과 결합이어도 전부 독립 처방
    assert rec["suppressed"] == []


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
    """정상 — kind=maintain, actions/suppressed 빈 배열. 목표 사양 == 현재(축소·증설 여지 없음).

    v2 사이징(ADR 0054): CPU 목표 70%(p95), 메모리 목표 80%(near-peak). near-peak 미측정 시 p95 폴백.
    """
    e = build_right_sizing_entry(
        # cpu 65% -> 목표 ceil(65*4/70)=4=현재 / mem 85% -> 목표 ceil(2048*85/80)=2176 > 현재 2048(축소 불가):
        # 둘 다 optimal. (v2 mem 목표 80%라 75%면 목표 1920<현재 -> over 로 새므로 near-peak 80% 위인 85% 사용)
        _raw(os_family="linux", cpu_p95=65.0, mem_p95=85.0, cpu_cores=4, procs_running_p95=1.0),
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


def test_evidence_labels_no_raw_enum_leak():
    """모든 도메인 trigger key 가 한국어 라벨로 변환 — mem_oom·net_* raw 영어 enum 누출 회귀 가드.

    _evidence_labels 가 _CAUSE_LABEL_BY_TRIGGER(6키) 미커버 trigger 를 raw 로 흘려 OOM·네트워크
    근거가 사용자에게 'mem_oom'·'net_retrans' 로 뜨던 회귀를 막는다(도메인 RS_TRIGGER_LABEL_KO 폴백).
    """
    from assessment_engine.recommendation import RS_TRIGGER_LABEL_KO
    from assessment_engine.web.services.mappers.right_sizing_api import _evidence_labels

    all_triggers = list(RS_TRIGGER_LABEL_KO)
    labels = _evidence_labels(all_triggers)
    leaked = [k for k, lbl in zip(all_triggers, labels, strict=True) if k == lbl]
    assert not leaked, f"raw enum 누출: {leaked}"
    assert "mem_oom" not in labels and "net_retrans" not in labels


def test_entry_matches_response_schema():
    """매퍼 dict 가 응답 스키마(RightSizingServer, extra=forbid)에 검증 — 매퍼<->OpenAPI 스키마 drift 가드.

    여러 시나리오(linux 포화·windows 미측정·disk io·network)로 신규/누락 키·타입 불일치를 잡는다.
    """
    scenarios = [
        _raw(os_family="linux", cpu_p95=75.0, cpu_cores=4, procs_running_p95=6.0),
        _raw(os_family="windows", cpu_p95=40.0, cpu_cores=4),
        _raw(os_family="linux", disk_await_p95_ms=30.0),
        _raw(os_family="linux", mem_p95=92.0, mem_total_bytes=8 * 1024**3),
    ]
    for raw in scenarios:
        entry = build_right_sizing_entry(raw, is_online=True)
        RightSizingServer.model_validate(entry)  # 위반 시 즉시 실패
