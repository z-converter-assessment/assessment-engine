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
    disk_bytes = disk_total_bytes(block_devices or [])

    mem_gib = bytes_to_gib(mem_total_bytes)
    disk_gb = bytes_to_gb(disk_bytes) if disk_bytes else None
    return " · ".join(
        [
            f"{cpu_cores}코어" if cpu_cores else "—",
            f"{mem_gib:.1f}GB" if mem_gib else "—",
            f"{disk_gb:.0f}GB" if disk_gb else "—",
        ]
    )
