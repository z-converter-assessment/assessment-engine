"""호스트 인벤토리 raw -> 표시 파생 (P2).

대표 IP 와 정적 사양 한 줄. 둘 다 여러 화면·API 가 같은 표기를 써야 해서 한 곳에서 결정한다 —
같은 호스트가 목록과 계약 응답에서 다른 IP·사양으로 보이지 않게.
"""

from typing import TYPE_CHECKING

from assessment_engine.json_types import JsonObject, json_list
from assessment_engine.web.services.device_filters import disk_total_bytes, is_virtual_interface
from assessment_engine.web.services.unit_converter import bytes_to_gb, bytes_to_gib

if TYPE_CHECKING:
    from assessment_engine.db.dtos.outbound import ReportRowRaw


def primary_ip(raw: ReportRowRaw) -> str | None:
    """물리(physical/bond_master) 인터페이스의 첫 IPv4 — API identity.primary_ip. topology/상세와 동일 술어(P2 공용)."""
    for i in raw.net_interfaces or []:
        if is_virtual_interface(i.get("kind")):
            continue
        for a in json_list(i, "addresses"):
            if a.get("family") == "ipv4":
                return a.get("address")
    return None


def spec_display_line(
    cpu_cores: int | None, mem_total_bytes: int | None, block_devices: list[JsonObject] | None
) -> str:
    """정적 배정 사양 한 줄("4코어 · 8.00GB · 100GB") — 서버 목록·환경 자원 평가 compact 표 공용(P2 단일 진실).

    실무정석: 값은 2진(GiB, 2^30)이되 라벨은 "GB"(free -h·df -h·클라우드 콘솔 관습) — OS·RAM·OpenStack
    프로비저닝이 2진 기준이라 30GiB 디스크가 "30GB"로 떨어져 딱 맞음. 각 값 부재는 "—".
    """
    disk_bytes = disk_total_bytes(block_devices or [])
    # 메모리 = RAM 계열 bytes_to_gib(1dp, 카탈로그 단일진실 — 인라인 나눗셈 대신 함수 경유로 base 변경 시 한 곳).
    # 디스크 = bytes_to_gb(카탈로그). compact 표는 정수 표기(.0f)라 두 함수의 소수 자릿수는 포맷이 덮음.
    mem_gib = bytes_to_gib(mem_total_bytes)
    disk_gb = bytes_to_gb(disk_bytes) if disk_bytes else None
    return " · ".join(
        [
            f"{cpu_cores}코어" if cpu_cores else "—",
            f"{mem_gib:.1f}GB" if mem_gib else "—",
            f"{disk_gb:.0f}GB" if disk_gb else "—",
        ]
    )
