#!/usr/bin/env python3
"""endoflife.date 스냅샷 -> 정적 OS EOL 카탈로그 생성 (운영신호 os_eol 단일 진실).

빌드/갱신 시점에 1회 실행 (인터넷 필요). 산출 카탈로그는 repo 에 commit 되어 런타임은 외부 의존 0
(고객사 폐쇄 내부망에서도 동작, #A0/#F6). endoflife.date 데이터는 벤더 공식 문서 기반 + 분기 검토
(신뢰성 확인됨). 갱신은 본 스크립트 재실행 + 카탈로그 재commit.

사용:
    python3 scripts/snapshot_os_eol.py \\
        src/assessment_engine/web/services/mappers/os_eol_catalog.json

카탈로그 구조:
    {
      "<product>": [{"cycle": "12", "eol": "2026-06-10"}, ...],   # Linux: os_version 매칭용
      "windows-server": [{"cycle": "2025", "build": "26100", "eol": "2034-10-10"}, ...]  # build 매칭용
    }

매칭 규약 (shared.resolve_os_eol):
- Linux: os_id -> product(slug), os_version == cycle 또는 startswith(cycle+".") (rocky "9.7" -> cycle "9").
- Windows: os_id=="windows" -> windows-server, kernel build == latest build (운영=Server 가정).
"""

import json
import sys
import urllib.request

from loguru import logger

# endoflife.date product slug — agent os_id 와 대체로 동일, 예외만 _OS_ID_TO_EOL_PRODUCT(shared)에서 매핑.
# 운영 환경에 등장 가능한 주요 distro 한정 (전체 456 product 중). 미등록 OS 는 런타임 침묵(의식적 한계).
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


def _fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "assessment-engine-eol-snapshot"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def build_catalog() -> dict:
    catalog: dict = {}

    for product in _LINUX_PRODUCTS:
        data = _fetch(f"https://endoflife.date/api/{product}.json")
        # eol 이 ISO 날짜 문자열인 cycle 만 (false=EOL 미정/지원중, true=이미 EOL 인데 날짜 불명 → 제외).
        catalog[product] = [{"cycle": str(d["cycle"]), "eol": d["eol"]} for d in data if isinstance(d.get("eol"), str)]

    # windows-server: latest build 에서 build 번호 추출 — agent kernel build 와 매칭.
    # latest 형식 X.Y.NNNNN: "10.0.26100"(2016+) / "6.3.9600"(2012R2) / "6.1.7601"(2008R2).
    # split(".")[-1] = build (26100/9600/7601). NT 6.x(2012 이하)도 포함해 전 버전 커버.
    # support(메인스트림 지원 종료)도 싣는다 — eol(연장지원 종료)과 2단계. Windows Server LTSC 는
    # support < eol(예: 2019 support=2024·eol=2029), SAC 는 support==eol(단일 컷오프). 이 2 날짜로
    # 엔진이 지원중/연장지원/종료 3상태를 판정. support 가 ISO 날짜 아니면(미정 등) 생략(엔진은 eol 만 사용).
    ws = _fetch("https://endoflife.date/api/windows-server.json")
    windows: list[dict] = []
    for d in ws:
        if not (isinstance(d.get("eol"), str) and str(d.get("latest", "")).count(".") >= 2):
            continue
        entry = {
            "cycle": str(d["cycle"]),
            "build": str(d["latest"]).split(".")[-1],
            "eol": d["eol"],
        }
        if isinstance(d.get("support"), str):
            entry["support"] = d["support"]
        windows.append(entry)
    catalog["windows-server"] = windows

    return catalog


def main() -> int:
    if len(sys.argv) != 2:
        logger.error("사용: python3 snapshot_os_eol.py <출력 카탈로그 경로>")
        return 1
    out_path = sys.argv[1]
    catalog = build_catalog()
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
    total = sum(len(v) for v in catalog.values())
    logger.info("카탈로그 생성: {} ({} products, {} cycles)", out_path, len(catalog), total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
