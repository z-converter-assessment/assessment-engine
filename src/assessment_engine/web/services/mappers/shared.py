"""Mapper sub-package 공용 상수·타입 카탈로그 (P2 단일 진실).

여러 sub-module 이 공유하는 임계값·카탈로그·타입 alias 만 본 모듈에 둔다. 단일 sub-module 내부에서만
쓰는 상수는 해당 sub-module 내부에 유지 — 본 모듈은 다중 공유 명목으로 한정.
"""

import json
from datetime import date
from pathlib import Path
from typing import Literal

# ─── UI 임계값 — base.html body data-attribute 동기화 (#E1 P3 · ADR 0015) ────
# template_setup.py 가 본 상수를 import 해 Jinja2 globals 로 노출 → body data-attribute 단일 진실.
_USAGE_DANGER_PCT = 90  # 사용률 위험 임계 — disk_warning · server detail badge 공통
_USAGE_WARN_PCT = 75  # 사용률 주의 임계
_SWAP_DANGER_PCT = 0.1  # 스왑 사용 자체가 이슈 — 0.1% 도 빨강 (JS performance.js 동일)

# 보고서 view 분기 — 라우터 Pydantic Literal 정합 (#F3)
# service · mapper 시그니처에도 적용해 typo 차단.
ReportView = Literal["customer", "engineer"]

# ─── USE Method 도넛 카탈로그 — 대시보드 + 환경 보고서 + 서버 리스트 단일 진실 (T13) ────
# USE Method recommendation enum 1:1 매핑. (key, label, hex, description) 튜플 정렬:
#   under(빨강), over(청록), idle(회색), shutdown(보라), optimal(녹색), insufficient_data(옅은회색).
_DONUT_SEGMENT_DEFS: list[tuple[str, str, str, str]] = [
    ("under_provisioned", "under_provisioned", "#ef4444", "자원 부족 — 사양 상향 검토"),
    ("over_provisioned", "over_provisioned", "#06b6d4", "자원 여유 — 사양 축소 검토"),
    ("idle", "idle", "#64748b", "사용률 매우 낮음 — 용도 재평가"),
    ("shutdown", "shutdown", "#9333ea", "사실상 미사용 — 종료 검토"),
    ("optimal", "optimal", "#22c55e", "적정"),
    ("insufficient_data", "insufficient_data", "#cbd5e1", "평가 표본 부족"),
]

# USE Method recommendation enum -> donut segment key (식별 매핑, 라벨·색은 _DONUT_SEGMENT_DEFS 단일 진실).
_DONUT_SEGMENT_FROM_REC: dict[str, str] = {
    "under_provisioned": "under_provisioned",
    "over_provisioned": "over_provisioned",
    "idle": "idle",
    "shutdown": "shutdown",
    "optimal": "optimal",
    "insufficient_data": "insufficient_data",
}

# 서버 목록 셀 안 표시용 약어 — 좁은 칸. 도넛 범례(풀네임)와 별도 매핑.
_DONUT_SEGMENT_SHORT_LABEL: dict[str, str] = {
    "under_provisioned": "Under",
    "over_provisioned": "Over",
    "idle": "Idle",
    "shutdown": "Shutdown",
    "optimal": "Optimal",
    "insufficient_data": "No Data",
}

# list 페이지 dropdown option — _DONUT_SEGMENT_DEFS 순서 그대로.
PROVISIONING_CLASSES: tuple[str, ...] = tuple(key for key, _, _, _ in _DONUT_SEGMENT_DEFS)

# ─── 보고서·환경 보고서 공용 capacity 임박 임계 ───
# build_report_summary_bullets (report.py) + _extract_capacity_imminent (environment_report.py).
_CAPACITY_IMMINENT_DAYS = 30

# ─── OS EOL — endoflife.date 스냅샷 카탈로그 기반 (scripts/snapshot_os_eol.py 생성) ───
# 정적 JSON 을 모듈 로드 시 1회 읽음. 런타임 외부 의존 0 (폐쇄 내부망 #A0). 갱신 = 스냅샷 재실행 + commit.
# 신뢰성: endoflife.date 는 벤더 공식 문서 기반 + 분기 검토 (ADR 0031). 미등록 OS 는 침묵 (의식적 한계).
_EOL_CATALOG_PATH = Path(__file__).parent / "os_eol_catalog.json"
_EOL_CATALOG: dict = json.loads(_EOL_CATALOG_PATH.read_text(encoding="utf-8"))

# agent os_id(/etc/os-release ID) -> endoflife product slug. 대부분 동일, 예외만 명시.
# 미등록 os_id 는 None (EOL 판정 불가 침묵). windows 는 build 기반이라 본 dict 밖 별도 분기.
_OS_ID_TO_EOL_PRODUCT: dict[str, str] = {
    "debian": "debian",
    "ubuntu": "ubuntu",
    "rhel": "rhel",
    "rocky": "rocky-linux",
    "almalinux": "almalinux",
    "centos": "centos",
    "sles": "sles",
    "opensuse": "opensuse",
    "amzn": "amazon-linux",
    "fedora": "fedora",
}


def resolve_os_eol(
    os_id: str | None,
    os_version: str | None,
    kernel_version: str | None,
    today: date,
) -> tuple[str, str] | None:
    """OS EOL 단일 판정 — 카탈로그 조회 + EOL 이미 경과면 (eol_iso, 제품 라벨), 아니면 None.

    attention OS EOL 카드 + 보고서 정성 요약 공용 (P2 단일 판정 — 두 표시 경로 일관).
    - Windows: os_id=="windows" -> windows-server 카탈로그, kernel build == latest build 매칭
      (운영=Server 가정, build ↔ 제품 1:1). kernel_version "26100.8457" -> build "26100".
    - Linux: os_id -> product slug, os_version == cycle 또는 startswith(cycle+".") (rocky "9.7" -> "9").
    EOL 미도래(아직 지원 중)는 None — 카탈로그 등록만으로 발화하면 미래 EOL(Server 2025=2034) 오발화.
    카탈로그 미등록 OS 도 None (EOL 판정 불가 = 침묵, false negative 한계는 의식적 트레이드오프).
    """
    if not os_id:
        return None

    if os_id == "windows":
        build = (kernel_version or "").split(".")[0]
        for entry in _EOL_CATALOG.get("windows-server", []):
            if entry.get("build") == build:
                eol_iso = entry["eol"]
                if date.fromisoformat(eol_iso) > today:
                    return None
                return (eol_iso, f"Windows Server {entry['cycle']}")
        return None

    product = _OS_ID_TO_EOL_PRODUCT.get(os_id)
    if product is None:
        return None
    ver = os_version or ""
    for entry in _EOL_CATALOG.get(product, []):
        cycle = entry["cycle"]
        if ver == cycle or ver.startswith(cycle + "."):
            eol_iso = entry["eol"]
            if date.fromisoformat(eol_iso) > today:
                return None
            label = " ".join(p for p in [os_id, os_version] if p) or "-"
            return (eol_iso, label)
    return None
