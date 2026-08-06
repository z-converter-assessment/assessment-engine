"""HTTP 캡처용 시드 — 시각 의존을 정규화가 아니라 입력 고정으로 없앤다.

정규화로 지우면 "무엇이 왜 달라졌는지" 를 매번 다시 판정해야 한다. 대신 앵커를 상수로 박고 시각을 그
상대로만 만든다. OS 지원 종료 판정처럼 `date.today()` 를 읽는 파생은 경계 근처 값을 아예 쓰지 않는다 —
과거 확정(2020년)과 미래 확정(2099년) 두 극단만 시드하면 오늘이 언제든 판정이 같다.

`public_id` 는 `uuid5` 로 만들어 실행마다 같다.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_DNS, uuid5

ANCHOR = datetime(2026, 1, 1, tzinfo=UTC)
"""전 시드가 기준으로 삼는 고정 시각. 캡처 대상 endpoint 는 `end`/`anchor_at` 을 이 값으로 넘긴다."""


def public_id(n: int) -> str:
    return str(uuid5(NAMESPACE_DNS, f"http-seed-{n}"))


_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_GENERATED_AT = re.compile(r'("generated_at":\s*")[^"]+(")')
# 라이브 미리보기 보고서는 설계상 "지금" 을 표제에 찍는다 (report_page.py: preview 는 anchor 없음).
_RENDERED_NOW = re.compile(r"(발행|기준) \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
_ASSET_V = re.compile(r"\?v=[0-9a-f]+")
# 사이드바에 찍히는 엔진 버전 — 릴리즈 범프마다 전 페이지 스냅샷이 함께 바뀐다. 이 안전망이 지키는 것은
# 화면 구조이지 버전 문자열이 아니므로 지운다. 버전 표기 자체의 회귀는 `test_engine_version_is_rendered` 가 본다.
_ENGINE_VERSION = re.compile(r"\bv\d+\.\d+\.\d+\b")
_SEED_IDS = {public_id(n) for n in range(1, 7)}


def normalize(value: object) -> object:
    """실행마다 달라지는 값만 지운다. 아래 다섯 외에 남으면 정규화를 늘리지 말고 실패시킨다.

    - `?v=` 정적 자원 토큰: 프로세스 import 시각 hex
    - 사이드바 엔진 버전: 릴리즈 범프마다 바뀐다
    - 시드 밖 UUID: 발행 job id 등 런타임 생성분
    - 라이브 미리보기 보고서 표제의 발행·기준 시각: anchor 를 받지 않는 경로라 설계상 "지금" 이다.
    - 계약 응답의 `generated_at`: "이 응답을 언제 만들었나" 라 입력 고정으로 없앨 수 없다.
      창 계산(`window.start`/`window.end`)은 `?end=` 로 고정되므로 여기서 건드리지 않는다 —
      전체 타임스탬프를 지우면 창 계산 회귀를 함께 가린다.
    """
    if isinstance(value, str):
        text = _ENGINE_VERSION.sub(
            "vENGINE_VERSION",
            _RENDERED_NOW.sub(
                r"\1 RENDER_TIME", _GENERATED_AT.sub(r"\1GENERATED_AT\2", _ASSET_V.sub("?v=ASSET_V", value))
            ),
        )
        counter: dict[str, str] = {}

        def _sub(m: re.Match[str]) -> str:
            found = m.group(0)
            if found in _SEED_IDS:
                return found
            return counter.setdefault(found, f"UUID-{len(counter) + 1}")

        return _UUID_RE.sub(_sub, text)
    if isinstance(value, list):
        return [normalize(v) for v in value]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return value


def _server_detail(n: int, **over: Any) -> Any:
    from assessment_engine.db.dtos.outbound import ServerDetail

    base: dict[str, Any] = {
        "id": n,
        "public_id": public_id(n),
        "agent_id": str(uuid5(NAMESPACE_DNS, f"agent-{n}")),
        "composite_id": f"composite-{n}",
        "machine_id": None,
        "hostname": f"host-{n}",
        "agent_version": "1.0.0",
        "os_family": "linux",
        "os_id": "ubuntu",
        "os_version": "22.04",
        "os_codename": "jammy",
        "kernel_version": "5.15.0",
        "cpu_cores": 4,
        "cpu_model": "seed-cpu",
        "cpu_arch": "x86_64",
        "cpu_bits": 64,
        "mem_total_bytes": 8 * 1024**3,
        "swap_total_bytes": 0,
        "last_seen_at": ANCHOR - timedelta(minutes=1),
        "boot_time": ANCHOR - timedelta(days=30),
        "agent_started_at": ANCHOR - timedelta(days=1),
        "net_interfaces": [
            {
                "id": f"52:54:00:00:00:{n:02d}",
                "id_type": "mac",
                "name": "eth0",
                "kind": "physical",
                "speed_mbps": 1000,
                "gateway": "10.0.0.254",
                "addresses": [{"address": f"10.0.0.{n}", "prefix": 24, "family": "ipv4"}],
            }
        ],
        "ip_external": None,
        "block_devices": [{"id": f"disk-{n}", "name": "sda", "type": "disk", "size_bytes": 50 * 10**9}],
        "lvm_vgs": [],
        "services": [{"unit": "nginx.service", "sub": "running"}],
        "listen_ports": [{"proto": "tcp", "port": 80, "comm": "nginx"}],
    }
    base.update(over)
    fields = {f.name for f in __import__("dataclasses").fields(ServerDetail)}
    return ServerDetail(**{k: v for k, v in base.items() if k in fields})


def QUERY_SEED() -> dict[str, Any]:  # noqa: N802  seed 상수처럼 쓰는 팩토리
    """`InMemoryQueryRepository` 에 넣을 반환값. 호출마다 새 객체를 만든다.

    호스트 3대로 화면 분기를 태운다 — 온라인 linux, 오프라인, windows. 이보다 늘리면 스냅샷이
    커지기만 하고 새로 타는 분기가 없다(분류 분기는 `report_aggregate` 시드가 가른다).
    """
    from tests.builders import report_row_raw

    details = [_server_detail(1), _server_detail(2), _server_detail(3, os_family="windows", os_id="windows")]
    return {
        "list_server_ids": [1, 2, 3],
        "resolve_server_id": 1,
        "resolve_server_ids": {public_id(n): n for n in (1, 2, 3)},
        "list_all_server_public_ids": [public_id(n) for n in (1, 2, 3)],
        "get_servers": details,
        "get_server": details[0],
        "list_servers": [_server_summary(n) for n in (1, 2, 3)],
        "get_network": _network(1),
        "get_storage": _storage(1),
        "report_aggregate": [
            report_row_raw(server_id=1, public_id=public_id(1), hostname="host-1", cpu_p95_pct=82.0, mem_p95_pct=91.0),
            report_row_raw(server_id=2, public_id=public_id(2), hostname="host-2", cpu_p95_pct=2.0, mem_p95_pct=8.0),
            report_row_raw(server_id=3, public_id=public_id(3), hostname="host-3", os_family="windows"),
        ],
    }


def _server_summary(n: int) -> Any:
    from assessment_engine.db.dtos.outbound import ServerSummary

    fields = {f.name for f in __import__("dataclasses").fields(ServerSummary)}
    base: dict[str, Any] = {
        "id": n,
        "public_id": public_id(n),
        "hostname": f"host-{n}",
        "os_family": "linux",
        "os_id": "ubuntu",
        "os_version": "22.04",
        "cpu_cores": 4,
        "mem_total_bytes": 8 * 1024**3,
        "last_seen_at": ANCHOR - timedelta(minutes=1),
        "composite_id": f"composite-{n}",
        "kernel_version": "5.15.0",
        "product_name": None,
        "ip_external": None,
        "block_devices": [{"id": f"disk-{n}", "name": "sda", "type": "disk", "size_bytes": 50 * 10**9}],
        "service_categories": ["web"],
    }
    return ServerSummary(**{k: v for k, v in base.items() if k in fields})


def _network(n: int) -> Any:
    from assessment_engine.db.dtos.outbound import NetworkWithIo

    return NetworkWithIo(
        server_id=n,
        public_id=public_id(n),
        hostname=f"host-{n}",
        net_interfaces=[
            {
                "id": f"52:54:00:00:00:{n:02d}",
                "id_type": "mac",
                "name": "eth0",
                "kind": "physical",
                "speed_mbps": 1000,
                "gateway": "10.0.0.254",
                "addresses": [{"address": f"10.0.0.{n}", "prefix": 24, "family": "ipv4"}],
            }
        ],
        ip_external=None,
        net_io=[],
        inventory_at=ANCHOR - timedelta(minutes=5),
    )


def _storage(n: int) -> Any:
    from assessment_engine.db.dtos.outbound import MountUsageRaw, StorageWithUsage

    return StorageWithUsage(
        server_id=n,
        public_id=public_id(n),
        hostname=f"host-{n}",
        block_devices=[{"id": f"disk-{n}", "name": "sda", "type": "disk", "size_bytes": 50 * 10**9}],
        lvm_vgs=[],
        filesystems=[
            MountUsageRaw(
                mountpoint="/",
                device_id=f"disk-{n}",
                fstype="ext4",
                used_bytes=10**10,
                free_bytes=4 * 10**10,
                collected_at=ANCHOR - timedelta(minutes=5),
            )
        ],
        inventory_at=ANCHOR - timedelta(minutes=5),
    )
