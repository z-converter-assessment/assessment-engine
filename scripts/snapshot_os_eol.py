#!/usr/bin/env python3

import json
import sys
import urllib.request
from pathlib import Path

from loguru import logger

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


Release = dict[str, object]

Entry = dict[str, str]


def _fetch(url: str) -> list[Release]:

    req = urllib.request.Request(url, headers={"User-Agent": "assessment-engine-eol-snapshot"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.load(resp)


def _dates(d: Release) -> Entry | None:
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
