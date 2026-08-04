"""CollectRepository 통합 테스트 — Phase 1 정확화 6가지(A~F) 검증.

각 테스트는 db_session이 function-scope이라 transaction rollback으로 격리.
테스트 시나리오:
- upsert_server (C — DRY): 멱등 INSERT, agent_id UNIQUE
- find_server_id: 존재/미존재
- ensure_server_id (D — facade): auto_registered 플래그
- record_metrics (A — 분리, F — MetricInsertResult 반환):
  - 4개 시계열 테이블 행 수 정확
  - ON CONFLICT DO NOTHING 멱등성 (재호출 시 0행)
  - 빈 entries 시 해당 카운트 0
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from assessment_engine.db.dtos.inbound import (
    DiskIoEntry,
    FilesystemEntry,
    NetIoEntry,
    PressureEntry,
)
from assessment_engine.db.repositories.collect_repository import CollectRepository
from tests.factories import make_inventory, make_metrics

pytestmark = pytest.mark.asyncio


# ─── upsert_server (C) ────────────────────────────────────────────────────


async def test_upsert_server_inserts_new(collect_repo: CollectRepository):
    inv = make_inventory(agent_id="00000000-0000-4000-8000-000000000001", hostname="h1")
    server_id = await collect_repo.upsert_server(inv)
    assert server_id > 0


async def test_upsert_server_stores_machine_id(
    collect_repo: CollectRepository,
    db_session: AsyncSession,
):
    """machine_id 표시 컬럼 저장 (ADR 0027) — agent_id 식별과 별개, 표시 전용 nullable."""
    inv = make_inventory(
        agent_id="00000000-0000-4000-8000-000000000002",
        machine_id="raw-machine-xyz",
        hostname="h",
    )
    sid = await collect_repo.upsert_server(inv)
    machine_id = (
        await db_session.execute(
            text("SELECT machine_id FROM server_inventory WHERE id = :i"),
            {"i": sid},
        )
    ).scalar_one()
    assert machine_id == "raw-machine-xyz"


async def test_upsert_server_overwrites_fields_on_conflict(
    collect_repo: CollectRepository,
    db_session: AsyncSession,
):
    """ON CONFLICT DO UPDATE — 같은 agent_id 의 다른 필드가 덮어씀."""
    aid = "00000000-0000-4000-8000-000000000003"
    inv1 = make_inventory(agent_id=aid, hostname="srv-a", cpu_cores=4)
    inv2 = make_inventory(agent_id=aid, hostname="srv-a", cpu_cores=16)
    await collect_repo.upsert_server(inv1)
    await collect_repo.upsert_server(inv2)

    row = (
        await db_session.execute(
            text("SELECT cpu_cores FROM server_inventory WHERE agent_id = :a"),
            {"a": aid},
        )
    ).one()
    assert row.cpu_cores == 16


async def test_upsert_server_same_agent_id_converges_across_reboot(
    collect_repo: CollectRepository,
    db_session: AsyncSession,
):
    """agent_id 단일 UNIQUE 키 — 같은 agent_id 면 composite_id·hostname 이 바뀌어도 한 row 로 수렴 (ADR 0049).

    agent_id 는 첫 실행 시 생성·영구저장한 불변 UUID. 재부팅으로 NIC MAC 재발급 -> composite_id 가
    바뀌어도(OpenStack Windows VM) 같은 agent_id 라 동일 row 의 composite_id·hostname 만 갱신 —
    별도 row 생성 안 함 (agent_id 불변이라 호스트 재연결 로직 불요, ADR 0049).
    """
    aid = "00000000-0000-4000-8000-000000000004"
    inv1 = make_inventory(agent_id=aid, composite_id="reboot-A", hostname="host-a", cpu_cores=4)
    inv2 = make_inventory(agent_id=aid, composite_id="reboot-B", hostname="host-b", cpu_cores=8)
    sid1 = await collect_repo.upsert_server(inv1)
    sid2 = await collect_repo.upsert_server(inv2)
    assert sid1 == sid2  # 같은 agent_id -> server_id(시계열 FK·history) 보존

    row = (
        await db_session.execute(
            text("SELECT hostname, composite_id FROM server_inventory WHERE agent_id = :a"),
            {"a": aid},
        )
    ).one()
    assert row.hostname == "host-b"  # 마지막 upsert 값으로 갱신
    assert row.composite_id == "reboot-B"

    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM server_inventory WHERE agent_id = :a"),
            {"a": aid},
        )
    ).scalar_one()
    assert count == 1


# ─── find_server_id ───────────────────────────────────────────────────────


async def test_find_server_id_existing(collect_repo: CollectRepository):
    aid = "00000000-0000-4000-8000-000000000011"
    sid = await collect_repo.upsert_server(make_inventory(agent_id=aid, hostname="h"))
    assert await collect_repo.find_server_id(aid) == sid


async def test_find_server_id_missing(collect_repo: CollectRepository):
    # 미존재 agent_id — UUID 컬럼이라 유효 UUID 형식 (비-UUID 문자열은 DataError).
    assert await collect_repo.find_server_id("00000000-0000-4000-8000-0000000000ff") is None


async def test_find_server_id_distinct_agent_ids_isolated(
    collect_repo: CollectRepository,
):
    """서로 다른 agent_id 는 각각 다른 server_id 로 격리 (agent_id 단일 UNIQUE 매칭)."""
    aid_a = "00000000-0000-4000-8000-000000000012"
    aid_b = "00000000-0000-4000-8000-000000000013"
    sid_a = await collect_repo.upsert_server(make_inventory(agent_id=aid_a, hostname="a"))
    sid_b = await collect_repo.upsert_server(make_inventory(agent_id=aid_b, hostname="b"))
    assert await collect_repo.find_server_id(aid_a) == sid_a
    assert await collect_repo.find_server_id(aid_b) == sid_b
    assert sid_a != sid_b


# ─── clone 격리 (같은 machine_id+hostname, 다른 agent_id) ───────────────────


async def test_different_agent_id_same_machine_id_hostname_isolated(
    collect_repo: CollectRepository,
):
    """agent_id 가 다르면 machine_id·hostname 이 같아도 별개 행 — clone(미sysprep) 오병합 방지.

    agent_id 불변 UUID 는 clone 마다 고유해 오병합 위험 자체가 없다 — 호스트 재연결 로직 없이 자연 격리 (ADR 0049).
    """
    sid_a = await collect_repo.upsert_server(
        make_inventory(
            agent_id="00000000-0000-4000-8000-000000000021",
            machine_id="mid-clone",
            hostname="same-host",
        )
    )
    sid_b = await collect_repo.upsert_server(
        make_inventory(
            agent_id="00000000-0000-4000-8000-000000000022",
            machine_id="mid-clone",
            hostname="same-host",
        )
    )
    assert sid_b != sid_a  # 별개 행


# ─── ensure_server_id (D — facade with auto_registered flag) ──────────────


async def test_ensure_server_id_auto_registers_when_missing(
    collect_repo: CollectRepository,
):
    """agent_id 미등록 시 fallback inventory로 INSERT, auto_registered=True."""
    aid = "00000000-0000-4000-8000-000000000031"
    fallback = make_inventory(agent_id=aid, hostname="placeholder")
    server_id, auto = await collect_repo.ensure_server_id(aid, fallback)
    assert server_id > 0
    assert auto is True


async def test_ensure_server_id_uses_existing_without_fallback(
    collect_repo: CollectRepository,
    db_session: AsyncSession,
):
    """기존 server_id 사용. fallback은 미사용 — 데이터가 fallback 값으로 덮이지 않음."""
    aid = "00000000-0000-4000-8000-000000000032"
    real = make_inventory(agent_id=aid, hostname="real-host", cpu_cores=8)
    sid_real = await collect_repo.upsert_server(real)

    fallback = make_inventory(agent_id=aid, hostname="real-host", cpu_cores=1)
    sid_ensured, auto = await collect_repo.ensure_server_id(aid, fallback)

    assert sid_ensured == sid_real
    assert auto is False
    # fallback이 실제로 미사용됐는지 — 데이터가 real 그대로
    row = (
        await db_session.execute(
            text("SELECT cpu_cores FROM server_inventory WHERE id = :id"),
            {"id": sid_real},
        )
    ).one()
    assert row.cpu_cores == 8


# ─── record_metrics (A — 분리, F — MetricInsertResult) ────────────────────


async def test_record_metrics_inserts_all_four_tables(
    collect_repo: CollectRepository,
):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-rec-1"))
    metrics = make_metrics(
        collected_at=datetime.now(UTC),
        disk_io=[
            # device_id = 안정 id 문자열("<scheme>:<value>", 이름 아님). ops/By counter (v2).
            DiskIoEntry(
                device_id="by-path:pci-0000:00:05.0",
                device_name="sda",
                ops_read=100,
                ops_write=50,
                io_read_bytes=1000 * 512,  # 512B/sector
                io_write_bytes=500 * 512,
            ),
            DiskIoEntry(
                device_id="by-path:pci-0000:00:06.0",
                device_name="sdb",
                ops_read=200,
                ops_write=100,
                io_read_bytes=2000 * 512,
                io_write_bytes=1000 * 512,
            ),
        ],
        filesystems=[
            # used = total - avail, free = avail 상당 (v2 FilesystemEntry, 실 fs).
            FilesystemEntry(
                mountpoint="/",
                fstype="ext4",
                used_bytes=50_000_000_000 - 18_000_000_000,
                free_bytes=18_000_000_000,
            ),
            FilesystemEntry(
                mountpoint="/data",
                fstype="ext4",
                used_bytes=100_000_000_000 - 78_000_000_000,
                free_bytes=78_000_000_000,
            ),
            FilesystemEntry(
                mountpoint="/var",
                fstype="ext4",
                used_bytes=10_000_000_000 - 4_000_000_000,
                free_bytes=4_000_000_000,
            ),
        ],
        net_io=[
            NetIoEntry(
                iface_id="mac:52:54:00:12:34:56",
                iface_name="eth0",
                rx_bytes=1_000_000,
                tx_bytes=500_000,
                rx_packets=1000,
                tx_packets=500,
                rx_errors=0,
                tx_errors=0,
            ),
        ],
    )
    result = await collect_repo.record_metrics(sid, metrics)
    assert result.metrics == 1
    assert result.disk_io == 2
    assert result.net_io == 1
    assert result.filesystem == 3


async def test_record_metrics_stores_pressure_rows(
    collect_repo: CollectRepository,
):
    """agent 발행값 전부 보존(#B) — PSI 는 server_pressure 시계열(resource x scope) 로 저장.

    PressureEntry(resource, scope, stall_time_s counter + ratio gauge) 로 정규화 — stall_time_s 가
    14일 saturation canonical. make_metrics 는 pressure kwarg 미노출이라 반환 dataclass 에 직접 주입
    (평면 dataclass, 가변).
    """
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-store-all"))
    ts = datetime.now(UTC)
    m = make_metrics(collected_at=ts)
    m.pressure = [
        PressureEntry(resource="cpu", scope="some", stall_time_s=111.0,
                      ratio_avg10=0.1, ratio_avg60=0.05, ratio_avg300=0.01),
        PressureEntry(resource="memory", scope="some", stall_time_s=222.0),
        PressureEntry(resource="io", scope="full", stall_time_s=333.0),
    ]
    result = await collect_repo.record_metrics(sid, m)
    assert result.pressure == 3

    rows = (
        await collect_repo.session.execute(
            text(
                "SELECT resource, scope, stall_time_s FROM server_pressure "
                "WHERE server_id = :s AND collected_at = :t"
            ),
            {"s": sid, "t": ts},
        )
    ).all()
    assert {(r.resource, r.scope, r.stall_time_s) for r in rows} == {
        ("cpu", "some", 111.0),
        ("memory", "some", 222.0),
        ("io", "full", 333.0),
    }


async def test_record_metrics_idempotent_on_conflict(
    collect_repo: CollectRepository,
):
    """같은 (server_id, collected_at) 재호출 시 ON CONFLICT DO NOTHING — 모든 카운트 0."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-rec-2"))
    ts = datetime.now(UTC)
    m = make_metrics(collected_at=ts)

    first = await collect_repo.record_metrics(sid, m)
    second = await collect_repo.record_metrics(sid, m)

    assert first.metrics == 1 and first.disk_io == 1 and first.net_io == 1 and first.filesystem == 1
    assert second.metrics == 0
    assert second.disk_io == 0
    assert second.net_io == 0
    assert second.filesystem == 0


async def test_record_metrics_skips_empty_collections(
    collect_repo: CollectRepository,
):
    """disk_io/filesystems/net_io 빈 리스트면 해당 INSERT skip — count 0."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-rec-3"))
    m = make_metrics(
        collected_at=datetime.now(UTC),
        disk_io=[],
        filesystems=[],
        net_io=[],
    )
    result = await collect_repo.record_metrics(sid, m)
    assert result.metrics == 1
    assert result.disk_io == 0
    assert result.net_io == 0
    assert result.filesystem == 0


async def test_record_metrics_independent_collected_at_succeeds(
    collect_repo: CollectRepository,
):
    """collected_at이 다르면 두 번 다 INSERT (UNIQUE 자연키는 (server_id, collected_at))."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-rec-4"))
    ts1 = datetime.now(UTC)
    ts2 = ts1 + timedelta(minutes=1)

    r1 = await collect_repo.record_metrics(sid, make_metrics(collected_at=ts1))
    r2 = await collect_repo.record_metrics(sid, make_metrics(collected_at=ts2))

    assert r1.metrics == 1 and r2.metrics == 1


async def test_record_metrics_per_device_unique(
    collect_repo: CollectRepository,
):
    """server_disk_io UNIQUE = (server_id, device_id, collected_at) — 같은 ts 다른 device_id는 OK."""
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-rec-5"))
    ts = datetime.now(UTC)
    m = make_metrics(
        collected_at=ts,
        disk_io=[
            DiskIoEntry(device_id="by-path:pci-0000:00:05.0", device_name="sda", ops_read=1),
            DiskIoEntry(device_id="by-path:pci-0000:00:06.0", device_name="sdb", ops_read=2),
            DiskIoEntry(device_id="nvme-eui.0001", device_name="nvme0n1", ops_read=3),
        ],
        filesystems=[],
        net_io=[],
    )
    result = await collect_repo.record_metrics(sid, m)
    assert result.disk_io == 3


# ─── boot_time / agent_started_at 보존 (counter reset 정밀 식별 의존) ───────


async def test_record_metrics_persists_boot_time_envelope(
    collect_repo: CollectRepository,
    db_session: AsyncSession,
):
    """boot_time/agent_started_at은 envelope(server_metrics)에만 저장 (v2).

    v1 은 4개 시계열 테이블 모두에 메타를 복제했으나, v2 는 수집 1회당 1행인 server_metrics 에만
    두고 자식 시계열(disk_io·net_io·filesystem)은 동일 (server_id, collected_at) 로 본 행을 참조 —
    메타 N중복 회피(CLAUDE.md C1, 모델 docstring). counter reset 판정은 envelope 행에서 읽는다.
    """
    sid = await collect_repo.upsert_server(make_inventory(composite_id="mid-bt-1"))
    ts = datetime.now(UTC)
    boot = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    started = datetime(2026, 5, 9, 12, 5, tzinfo=UTC)
    m = make_metrics(collected_at=ts, boot_time=boot, agent_started_at=started)
    await collect_repo.record_metrics(sid, m)

    # envelope: server_metrics 에 boot_time/agent_started_at 저장.
    row = (
        await db_session.execute(
            text(
                "SELECT boot_time, agent_started_at FROM server_metrics "
                "WHERE server_id = :sid AND collected_at = :ts LIMIT 1"
            ),
            {"sid": sid, "ts": ts},
        )
    ).one()
    assert row.boot_time == boot
    assert row.agent_started_at == started

    # 자식 시계열은 동일 (server_id, collected_at) 로 envelope 행을 참조 (메타 미보유).
    for table in ("server_disk_io", "server_net_io", "server_filesystem"):
        count = (
            await db_session.execute(
                text(
                    f"SELECT COUNT(*) FROM {table} "
                    "WHERE server_id = :sid AND collected_at = :ts"
                ),
                {"sid": sid, "ts": ts},
            )
        ).scalar_one()
        assert count == 1, f"{table} should reference the envelope at same (server_id, collected_at)"


# ─── 명시 select 후 _inventory_changed 회귀 (C5) ──────────────────────────


async def test_upsert_server_history_appended_on_change(
    collect_repo: CollectRepository,
    db_session: AsyncSession,
):
    """C5 명시 select(_INVENTORY_COMPARE_COLS) 후에도 변경 감지 동일 — history 한 행 append.

    hostname 은 복합 conflict 키라 변경 시 새 row 가 되어 history append 의미가 사라짐.
    여기서는 hostname 동일 + cpu_cores·mem_total_bytes 변경으로 진짜 history 트리거 검증.
    """
    aid = "00000000-0000-4000-8000-000000000041"
    inv1 = make_inventory(agent_id=aid, hostname="h1", cpu_cores=4, mem_total_bytes=4 * 1024**3)
    inv2 = make_inventory(agent_id=aid, hostname="h1", cpu_cores=8, mem_total_bytes=8 * 1024**3)
    await collect_repo.upsert_server(inv1)
    await collect_repo.upsert_server(inv2)
    sid = await collect_repo.find_server_id(aid)
    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM server_inventory_history WHERE server_id = :sid"),
            {"sid": sid},
        )
    ).scalar_one()
    assert count == 2, "첫 등록 + 변경 = history 2건이어야 함"


async def test_upsert_server_history_not_appended_when_unchanged(
    collect_repo: CollectRepository,
    db_session: AsyncSession,
):
    """비교 컬럼 동일 + collected_at만 다름 (1h 주기 재발행 시뮬) → history 추가 없음."""
    aid = "00000000-0000-4000-8000-000000000042"
    inv1 = make_inventory(agent_id=aid, hostname="h1", cpu_cores=4, collected_at=datetime.now(UTC) - timedelta(hours=1))
    inv2 = make_inventory(
        agent_id=aid, hostname="h1", cpu_cores=4, collected_at=datetime.now(UTC)
    )  # 모든 비교 컬럼 동일
    await collect_repo.upsert_server(inv1)
    await collect_repo.upsert_server(inv2)
    sid = await collect_repo.find_server_id(aid)
    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM server_inventory_history WHERE server_id = :sid"),
            {"sid": sid},
        )
    ).scalar_one()
    assert count == 1, "변경 없는 재발행은 history 그대로 — noise 차단"


async def test_upsert_server_history_appended_on_boot_change(
    collect_repo: CollectRepository,
    db_session: AsyncSession,
):
    """_inventory_changed 가 boot(JSONB) 변경을 감지 → history append (재현 필드도 변경 감지 대상)."""
    aid = "00000000-0000-4000-8000-000000000043"
    inv1 = make_inventory(
        agent_id=aid, hostname="h1",
        boot={"kernel_cmdline": "ro quiet", "root_ref_type": "label", "grub_install_target": "i386-pc"},
    )
    inv2 = make_inventory(
        agent_id=aid, hostname="h1",
        boot={"kernel_cmdline": "ro quiet", "root_ref_type": "uuid", "grub_install_target": "i386-pc"},
    )
    await collect_repo.upsert_server(inv1)
    await collect_repo.upsert_server(inv2)
    sid = await collect_repo.find_server_id(aid)
    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM server_inventory_history WHERE server_id = :sid"),
            {"sid": sid},
        )
    ).scalar_one()
    assert count == 2, "첫 등록 + boot.root_ref_type 변경 = history 2건"


async def test_upsert_server_persists_reproduction_columns(
    collect_repo: CollectRepository,
    db_session: AsyncSession,
):
    """_inventory_row/_append_inventory_history 가 재현 컬럼을 server_inventory + history 양쪽에 기록."""
    aid = "00000000-0000-4000-8000-000000000044"
    inv = make_inventory(
        agent_id=aid, hostname="h1", arch="x86_64", rtc_utc=True,
        nonblock_mounts=[{"source": "tmpfs", "target": "/run", "fstype": "tmpfs",
                          "options": ["rw", "nosuid"], "fs_freq": 0, "fs_passno": 0}],
    )
    sid = await collect_repo.upsert_server(inv)
    for table, where in (("server_inventory", "id"), ("server_inventory_history", "server_id")):
        row = (
            await db_session.execute(
                text(f"SELECT arch, rtc_utc, boot, nonblock_mounts FROM {table} WHERE {where} = :sid"),
                {"sid": sid},
            )
        ).one()
        assert row.arch == "x86_64", table
        assert row.rtc_utc is True, table
        assert row.boot is None, table  # 미지정 -> None
        assert row.nonblock_mounts == inv.nonblock_mounts, table  # JSONB list 왕복
