#!/usr/bin/env python3
"""endoflife.date 에서 OS 지원 종료일을 받아 정적 카탈로그로 저장한다.

런타임에 호출하지 않는다. EOL 날짜는 실시간성이 필요 없는데,
런타임 호출은 그 대가로 외부 서비스의 장애와 스키마 변경을 우리 가용성 안으로 들인다.
갱신은 이 스크립트를 다시 돌려 카탈로그를 재커밋하는 것이다.

갱신 절차는 `docs/guides/local-dev.md`, 매칭 규약은 `mappers/os_eol.py` 의 `_eol_info` 가 갖는다.

사용: make eol
"""

import json
import sys
import urllib.request
from pathlib import Path

from loguru import logger

# endoflife.date 가 제공하는 전체 product 중 운영 환경에 등장 가능한 distro 만 싣는다.
# 여기 slug 는 agent 가 보내는 os_id 와 대체로 같고, 어긋나는 것만 os_eol._OS_ID_TO_EOL_PRODUCT 가 매핑한다.
_LINUX_PRODUCTS = [
    "debian",
    "ubuntu",
    "rhel",
    "rocky-linux",
    "almalinux",
    "centos",
    "centos-stream",
    "sles",
    "opensuse",
    "amazon-linux",
    "fedora",
    "oracle-linux",
]


# 응답 릴리즈 한 건. 날짜 자리에 문자열 대신 bool 이 오므로 값 타입을 좁히지 않고 사용처에서 narrow 한다.
Release = dict[str, object]
# 카탈로그 항목. 값은 전부 문자열이다 (cycle·경계 날짜·build).
Entry = dict[str, str]


def _fetch(url: str) -> list[Release]:
    """product 하나의 릴리즈 목록. 릴리즈마다 dict 하나이고 필요한 필드만 골라 쓴다.

    {"cycle": "13", "codename": "Trixie", "releaseDate": "2025-08-09", "eol": "2028-08-09",
     "latest": "13.6", "lts": false, "extendedSupport": "2030-06-30", ...}
    """
    # URL 은 이 스크립트가 조립하는 endoflife.date https 주소 하나뿐이다.
    req = urllib.request.Request(url, headers={"User-Agent": "assessment-engine-eol-snapshot"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.load(resp)


def _dates(d: Release) -> Entry | None:
    """릴리즈 하나에서 경계 날짜 셋을 뽑는다 — support < eol < extendedSupport 순.

    support 는 기능 업데이트가, eol 은 무상 보안 패치가, extendedSupport 는 유상 보안 패치가
    끊기는 시점이다. 벤더마다 부르는 이름이 다르지만(Windows 는 Mainstream·Extended·ESU,
    RHEL 은 Full·Maintenance·ELS) 경계의 의미는 같다.

    날짜 필드에는 ISO 문자열 외에 false·true 도 온다 (지원중 / 종료됐으나 날짜 불명 / 해당 없음).
    eol 이 날짜가 아니면 None — 나머지 경계는 날짜만 취하고 아니면 키 자체를 넣지 않아 소비자가 유무로 판단한다.
    """
    eol = d.get("eol")
    if not isinstance(eol, str):
        return None
    entry: Entry = {"cycle": str(d["cycle"]), "eol": eol}
    for key in ("support", "extendedSupport"):
        value = d.get(key)
        if isinstance(value, str):
            entry[key] = value
    return entry


def build_catalog() -> dict[str, list[Entry]]:
    catalog: dict[str, list[Entry]] = {}

    for product in _LINUX_PRODUCTS:
        data = _fetch(f"https://endoflife.date/api/{product}.json")
        catalog[product] = [entry for d in data if (entry := _dates(d)) is not None]

    # Windows 는 버전 문자열이 아니라 커널 build 로 맞춘다. latest 가 X.Y.NNNNN 형식이라
    # 마지막 토막이 build 다 ("10.0.26100" -> "26100").
    windows: list[Entry] = []
    for d in _fetch("https://endoflife.date/api/windows-server.json"):
        latest = str(d.get("latest", ""))
        if latest.count(".") < 2:
            continue
        entry = _dates(d)
        if entry is None:
            continue
        entry["build"] = latest.rsplit(".", 1)[-1]
        windows.append(entry)
    catalog["windows-server"] = windows

    return catalog


def main() -> int:
    if len(sys.argv) != 2:
        logger.error("사용: uv run python scripts/snapshot_os_eol.py <출력 카탈로그 경로>")
        return 1
    out = Path(sys.argv[1])
    catalog = build_catalog()
    out.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    total = sum(len(v) for v in catalog.values())
    logger.info("카탈로그 생성: {} ({} products, {} cycles)", out, len(catalog), total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
