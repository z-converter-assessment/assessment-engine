"""OS 지원 종료(EOL) 판정 — endoflife.date 스냅샷 카탈로그 기반.

카탈로그는 `scripts/snapshot_os_eol.py` 가 생성한 정적 JSON 이다. 판정이 이미지 안에서 닫혀 있어야
같은 이미지가 언제 돌든 같은 결과를 낸다 — 갱신 = 스냅샷 재실행 + commit.

Windows 표시 라벨(build/ProductName 파싱)도 여기 둔다. EOL 판정이 같은 입력을 쓰기 때문이다.
판정 4상태의 경계 정의는 `docs/reference/web/view-models.md` 가 갖는다.
"""

import json
import re
from datetime import date
from importlib import resources
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject


_EOL_CATALOG: dict[str, list[JsonObject]] = json.loads(
    (resources.files(__package__) / "os_eol_catalog.json").read_text(encoding="utf-8")
)


# windows 는 build 기반이라 본 dict 밖 별도 분기.
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
    "ol": "oracle-linux",  # Oracle Linux /etc/os-release ID="ol"
    "oracle": "oracle-linux",
}

# 필터 옵션은 수집된 distro 가 아니라 카탈로그 product 전체 — 지원 범위 자체를 노출한다.
_DISTRO_LABELS: dict[str, str] = {
    "debian": "Debian",
    "ubuntu": "Ubuntu",
    "rhel": "RHEL",
    "rocky-linux": "Rocky Linux",
    "almalinux": "AlmaLinux",
    "centos": "CentOS",
    "centos-stream": "CentOS Stream",
    "sles": "SLES",
    "opensuse": "openSUSE",
    "amazon-linux": "Amazon Linux",
    "fedora": "Fedora",
    "oracle-linux": "Oracle Linux",
    "windows-server": "Windows",
}

DISTRO_FILTER_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    (slug, _DISTRO_LABELS.get(slug, slug)) for slug in _EOL_CATALOG
)


def os_id_to_distro(os_id: str | None) -> str:
    if not os_id:
        return ""
    if os_id == "windows":
        return "windows-server"
    return _OS_ID_TO_EOL_PRODUCT.get(os_id, os_id)


class OsEolInfo(NamedTuple):
    """OS 지원 단계 판정 결과. 미매칭(판정 불가)은 None 으로 표현하므로 unknown 은 여기 안 담긴다.

    경계 셋은 무엇이 끊기는지로 정의한다 — support 는 기능 업데이트, eol 은 무상 보안 패치,
    extended_support 는 유상 보안 패치. 벤더 용어와는 어긋난다. 특히 Microsoft 의 "Extended
    Support" 는 여기서 security_only 구간이라 카탈로그 필드명과 반대다.
    """

    eol_iso: str
    support_iso: str | None
    extended_support_iso: str | None
    label: str
    status: str


# 심각도 순 — Windows 커널 build 가 복수 채널에 겹칠 때 후보 중 최소 심각도를 택하는 데 쓴다.
_EOL_SEVERITY = ("full", "security_only", "paid_only", "ended")


class OsEolDisplay(NamedTuple):
    """지원 단계 표시 원자. 서버 목록·보고서·상세가 같은 말을 쓰게 한다.

    화면마다 분기를 복제하면 상태가 늘 때마다 네 곳이 갈린다. sort 는 심각한 것이 커서 목록
    정렬이 위험 순으로 선다.
    """

    label: str
    css: str
    title: str
    sort: int


_OS_EOL_DISPLAY: dict[str, tuple[str, str, str, int]] = {
    "ended": ("지원 종료", "text-danger", "어떤 경로로도 보안 패치 없음 · EOL {eol}", 4),
    "paid_only": (
        "무상 종료",
        "text-danger",
        "무상 보안 패치 종료 {eol} · 연장 지원 단계 — 배포판에 따라 유상 계약이 필요하다",
        3,
    ),
    "security_only": ("보안 패치만", "text-warn", "기능 업데이트 종료 · 보안 패치는 유지 · 무상 종료 {eol}", 2),
    "full": ("지원 중", "text-meta", "기능 업데이트 + 보안 패치 · 무상 종료 {eol}", 0),
}
_OS_EOL_UNKNOWN = OsEolDisplay("미상", "text-unknown", "EOL 카탈로그 미수록·미매칭 — 지원 여부 미판정(확인 필요)", 1)


def os_eol_display(status: str, eol_iso: str) -> OsEolDisplay:
    """지원 단계 -> 표시 원자. 미판정과 미등록 상태는 같은 칸으로 흡수한다."""
    entry = _OS_EOL_DISPLAY.get(status)
    if entry is None:
        return _OS_EOL_UNKNOWN
    label, css, tmpl, sort = entry
    title = tmpl.format(eol=eol_iso) if eol_iso else tmpl.split(" · 무상 종료 {eol}")[0].format(eol="")
    return OsEolDisplay(label, css, title, sort)


def _classify_eol(support_iso: str | None, eol_iso: str, extended_iso: str | None, today: date) -> str:
    if extended_iso and date.fromisoformat(extended_iso) <= today:
        return "ended"
    if date.fromisoformat(eol_iso) <= today:
        return "paid_only" if extended_iso else "ended"
    if support_iso and date.fromisoformat(support_iso) <= today:
        return "security_only"
    return "full"


def _eol_info(os_id: str | None, os_version: str | None, kernel_version: str | None, today: date) -> OsEolInfo | None:
    """카탈로그 매칭 + 지원 단계 판정 단일 진실. 미매칭은 None — 판정하지 않고 침묵한다.

    Windows 는 운영을 Server 로 가정하고 kernel build 로 매칭한다. 빌드 하나가 복수 채널에 겹치면
    (17763 = SAC "1809-sac" + LTSC "2019") 후보 전체를 판정한 뒤 심각도가 가장 낮은 것을 택한다 —
    불확실할 때 과소지원으로 오판하지 않는 쪽이다. 대표 라벨·날짜는 eol 최장(LTSC) 후보로 낸다.
    """
    if not os_id:
        return None
    if os_id == "windows":
        build = (kernel_version or "").split(".")[0]
        matches = [e for e in _EOL_CATALOG.get("windows-server", []) if e.get("build") == build]
        if not matches:
            return None
        rep = max(matches, key=lambda e: e["eol"])
        status = min(
            (_classify_eol(e.get("support"), e["eol"], e.get("extendedSupport"), today) for e in matches),
            key=_EOL_SEVERITY.index,
        )
        return OsEolInfo(
            rep["eol"], rep.get("support"), rep.get("extendedSupport"), f"Windows Server {rep['cycle']}", status
        )
    product = _OS_ID_TO_EOL_PRODUCT.get(os_id)
    if product is None:
        return None
    ver = os_version or ""
    for entry in _EOL_CATALOG.get(product, []):
        cycle = entry["cycle"]
        if ver == cycle or ver.startswith(cycle + "."):
            label = " ".join(part for part in (os_id, os_version) if part) or "-"
            status = _classify_eol(entry.get("support"), entry["eol"], entry.get("extendedSupport"), today)
            return OsEolInfo(entry["eol"], entry.get("support"), entry.get("extendedSupport"), label, status)
    return None


def resolve_os_eol(
    os_id: str | None,
    os_version: str | None,
    kernel_version: str | None,
    today: date,
) -> tuple[str, str] | None:
    """발화용 판정 — 무상 보안 패치가 끊긴 경우만 (eol_iso, 제품 라벨), 아니면 None.

    security_only 는 무상 패치가 유지되므로 발화하지 않는다. paid_only 는 발화한다 — 유상 계약
    여부는 수집할 수 없으므로 계약이 없다는 쪽으로 본다.
    """
    info = _eol_info(os_id, os_version, kernel_version, today)
    if info is None or info.status not in ("paid_only", "ended"):
        return None
    return (info.eol_iso, info.label)


def lookup_os_eol(
    os_id: str | None, os_version: str | None, kernel_version: str | None, today: date
) -> OsEolInfo | None:
    """표시용 조회. `resolve_os_eol` 과 달리 아직 지원 중이어도 경계 날짜와 단계를 그대로 노출한다."""
    return _eol_info(os_id, os_version, kernel_version, today)


# 모든 Windows 에 있는 CurrentBuildNumber 로 표시만 보강한다. 2016(14393) 이상은 os_version 이 오므로 미등록.
# build <-> 제품 출처는 EOL 판정과 같은 windows-server 카탈로그다. 라벨을 카탈로그에서 바로 못 꺼내고 따로

_WINDOWS_LEGACY_BUILD_LABEL: dict[str, str] = {
    "9600": "2012 R2",
    "9200": "2012",
    "7601": "2008 R2",
    "6003": "2008",
    "3790": "2003",
    "2195": "2000",
}


def windows_legacy_version_from_build(kernel_version: str | None) -> str | None:
    """레거시 Windows Server kernel build -> 표시 버전 라벨. 비레거시·미매칭은 None."""
    if not kernel_version:
        return None
    build = kernel_version.split(".")[0]
    return _WINDOWS_LEGACY_BUILD_LABEL.get(build)


_WIN_SERVER_YEAR_RE = re.compile(r"Windows Server (\d{4})(?:\s+(R2))?", re.IGNORECASE)
_WIN_CLIENT_GEN_RE = re.compile(r"Windows (10|11)\b", re.IGNORECASE)


def windows_short_label_from_product_name(product_name: str | None) -> str | None:
    if not product_name:
        return None
    m = _WIN_SERVER_YEAR_RE.search(product_name)
    if m:
        return f"{m.group(1)} R2" if m.group(2) else m.group(1)
    m = _WIN_CLIENT_GEN_RE.search(product_name)
    return m.group(1) if m else None
