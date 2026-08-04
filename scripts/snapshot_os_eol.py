#!/usr/bin/env python3
"""endoflife.date 에서 OS 지원 종료일을 받아 정적 카탈로그로 저장한다.

런타임에 호출하지 않는다. EOL 날짜는 실시간성이 필요 없는데,
런타임 호출은 그 대가로 외부 서비스의 장애와 스키마 변경을 우리 가용성 안으로 들인다.
갱신은 이 스크립트를 다시 돌려 카탈로그를 재커밋하는 것이다.

갱신 절차는 `docs/guides/local-dev.md`, 매칭 규약은 `mappers/shared.py` 의 `_eol_info` 가 갖는다.

사용: python3 scripts/snapshot_os_eol.py src/assessment_engine/web/services/mappers/os_eol_catalog.json
"""

import json
import sys
import urllib.request
from pathlib import Path

from loguru import logger

# endoflife.date 가 제공하는 전체 product 중 운영 환경에 등장 가능한 distro 만 싣는다.
# 여기 slug 는 agent 가 보내는 os_id 와 대체로 같고, 어긋나는 것만 shared._OS_ID_TO_EOL_PRODUCT 가 매핑한다.
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


def _fetch(url: str) -> list[dict]:
    """product 하나의 릴리즈 목록. 릴리즈마다 dict 하나이고 필요한 필드만 골라 쓴다.

    {"cycle": "13", "codename": "Trixie", "releaseDate": "2025-08-09", "eol": "2028-08-09",
     "latest": "13.6", "lts": false, "extendedSupport": "2030-06-30", ...}
    """
    req = urllib.request.Request(url, headers={"User-Agent": "assessment-engine-eol-snapshot"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _dates(d: dict) -> dict:
    """릴리즈 하나에서 경계 날짜 셋을 뽑는다 — support < eol < extendedSupport 순.

    support 는 기능 업데이트가, eol 은 무상 보안 패치가, extendedSupport 는 유상 보안 패치가
    끊기는 시점이다. 벤더마다 부르는 이름이 다르지만(Windows 는 Mainstream·Extended·ESU,
    RHEL 은 Full·Maintenance·ELS) 경계의 의미는 같다.
    """
    entry = {"cycle": str(d["cycle"]), "eol": d["eol"]}
    for key in ("support", "extendedSupport"):
        if isinstance(d.get(key), str):
            entry[key] = d[key]
    return entry


def build_catalog() -> dict[str, list[dict]]:
    catalog: dict[str, list[dict]] = {}

    for product in _LINUX_PRODUCTS:
        data = _fetch(f"https://endoflife.date/api/{product}.json")
        # 날짜 필드에는 ISO 문자열 외에 false·true 도 온다 (지원중 / 종료됐으나 날짜 불명 / 해당 없음).
        # 날짜만 취하고 나머지는 키 자체를 넣지 않는다 — 소비자가 유무로 판단한다.
        entries = []
        for d in data:
            if not isinstance(d.get("eol"), str):
                continue
            entries.append(_dates(d))
        catalog[product] = entries

    # Windows 는 버전 문자열이 아니라 커널 build 로 맞춘다. latest 가 X.Y.NNNNN 형식이라
    # 마지막 토막이 build 다 ("10.0.26100" -> "26100").
    ws = _fetch("https://endoflife.date/api/windows-server.json")
    windows: list[dict] = []
    for d in ws:
        if not (isinstance(d.get("eol"), str) and str(d.get("latest", "")).count(".") >= 2):
            continue
        entry = _dates(d)
        entry["build"] = str(d["latest"]).split(".")[-1]
        windows.append(entry)
    catalog["windows-server"] = windows

    return catalog


def main() -> int:
    if len(sys.argv) != 2:
        logger.error("사용: python3 snapshot_os_eol.py <출력 카탈로그 경로>")
        return 1
    out = Path(sys.argv[1])
    catalog = build_catalog()
    out.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    total = sum(len(v) for v in catalog.values())
    logger.info("카탈로그 생성: {} ({} products, {} cycles)", out, len(catalog), total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
