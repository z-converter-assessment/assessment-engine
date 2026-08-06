"""OS 지원 종료(EOL) 판정 — endoflife.date 스냅샷 카탈로그 기반.

카탈로그는 `scripts/snapshot_os_eol.py` 가 생성한 정적 JSON 이다. 판정이 이미지 안에서 닫혀 있어야
같은 이미지가 언제 돌든 같은 결과를 낸다 — 갱신 = 스냅샷 재실행 + commit.

Windows 표시 라벨(build/ProductName 파싱)도 여기 둔다. EOL 판정이 같은 입력을 쓰기 때문이다.
"""

import json
import re
from datetime import date
from importlib import resources
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject


# 패키지 데이터로 읽는다 — 이 저장소는 wheel 을 빌드해 이미지로 배포하므로 `__file__` 기준 경로는
# 소스 트리가 그대로 깔려 있다는 전제를 깔고, 그 전제가 깨지면 import 가 아니라 파일 읽기에서 터진다.
_EOL_CATALOG: dict[str, list[JsonObject]] = json.loads(
    (resources.files(__package__) / "os_eol_catalog.json").read_text(encoding="utf-8")
)

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
    "ol": "oracle-linux",  # Oracle Linux /etc/os-release ID="ol"
    "oracle": "oracle-linux",  # 일부 배포 변형 alias
}

# --- OS distro 필터 ---
# endoflife.date 카탈로그 product 전체를 OS 필터 옵션으로 (수집 무관 — 지원 distro 노출, ADR 0031 출처).
# 수집 os_id <-> distro(product slug) 정규화. windows 는 os_id=="windows" -> windows-server.
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
# 필터 드롭다운 옵션 — (slug, 라벨). 카탈로그(_EOL_CATALOG) 키 순서 단일 진실.
DISTRO_FILTER_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    (slug, _DISTRO_LABELS.get(slug, slug)) for slug in _EOL_CATALOG
)


def os_id_to_distro(os_id: str | None) -> str:
    """수집 os_id -> 카탈로그 distro(product slug). windows 별도, linux 는 _OS_ID_TO_EOL_PRODUCT, 미등록은 그대로."""
    if not os_id:
        return ""
    if os_id == "windows":
        return "windows-server"
    return _OS_ID_TO_EOL_PRODUCT.get(os_id, os_id)


class OsEolInfo(NamedTuple):
    """OS 지원 단계 판정 결과. 미매칭(판정 불가)은 None 으로 표현하므로 unknown 은 여기 안 담긴다.

    경계 셋이 릴리즈 하나의 수명을 넷으로 가른다 — support 는 기능 업데이트가, eol 은 무상 보안
    패치가, extended_support 는 유상 보안 패치가 끊기는 시점이다.
    - "full"           기능 업데이트 + 보안 패치. support 미도래(또는 support 미수록 + eol 미도래).
    - "security_only"  보안 패치만, 무상. support 경과 + eol 미도래.
    - "paid_only"      유상 계약자만 보안 패치. eol 경과 + extended_support 미도래.
    - "ended"          어떤 경로로도 패치 없음. extended_support 경과, 또는 eol 경과인데 유상 경로 부재.

    벤더가 부르는 이름은 다르다 (Windows 는 Mainstream·Extended·ESU, RHEL 은 Full·Maintenance·ELS).
    특히 Microsoft 의 "Extended Support" 는 여기서 security_only 구간이라 카탈로그 필드명과 반대다.
    """

    eol_iso: str
    support_iso: str | None
    extended_support_iso: str | None
    label: str
    status: str  # "full" | "security_only" | "paid_only" | "ended"


# 심각도 순 — Windows 커널 build 가 복수 채널에 겹칠 때 후보 중 최소 심각도를 택하는 데 쓴다.
_EOL_SEVERITY = ("full", "security_only", "paid_only", "ended")


class OsEolDisplay(NamedTuple):
    """지원 단계 표시 원자 — 라벨·색·툴팁·정렬 순서. 서버 목록·보고서·상세가 같은 말을 쓰게 한다 (P2).

    화면마다 분기를 복제하면 상태가 늘 때마다 네 곳이 갈린다. 여기서 한 번 정하고 템플릿은 꺼내 쓴다.
    sort 는 심각한 것이 커서 목록 정렬이 위험 순으로 선다 (P3 — 템플릿 계산 금지).
    """

    label: str
    css: str
    title: str
    sort: int


# status -> (라벨, 색 클래스, 툴팁 틀, 정렬 가중치). 툴팁 {eol} 은 매칭 날짜로 채운다.
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
    """경계 3개 -> 4상태. 없는 경계는 그 구간이 존재하지 않는다는 뜻이다."""
    if extended_iso and date.fromisoformat(extended_iso) <= today:
        return "ended"
    if date.fromisoformat(eol_iso) <= today:
        return "paid_only" if extended_iso else "ended"
    if support_iso and date.fromisoformat(support_iso) <= today:
        return "security_only"
    return "full"


def _eol_info(os_id: str | None, os_version: str | None, kernel_version: str | None, today: date) -> OsEolInfo | None:
    """카탈로그 매칭 + 4단계 상태 판정 단일 진실. 미매칭(판정 불가)은 None.

    - Windows: os_id=="windows" -> windows-server 카탈로그, kernel build == latest build 매칭
      (운영=Server 가정). kernel_version "26100.8457" -> build "26100". 커널 빌드 하나가 복수 채널에
      겹치면(예: 17763 = SAC "1809-sac" + LTSC "2019") 후보 전체를 판정한 뒤 심각도가 가장 낮은 것을
      택한다 — 불확실할 때 과소지원으로 오판하지 않는 쪽이다(#E9 침묵 원칙과 동일 방향). 대표 라벨·날짜는
      eol 최장(LTSC) 후보로 표시.
    - Linux: os_id -> product slug, os_version == cycle 또는 startswith(cycle+".").
    카탈로그가 경계를 다 싣지는 않는다. 어느 경계가 없으면 그 구간이 없다는 뜻이라 판정이 건너뛴다 —
    fedora·opensuse 처럼 유상 연장이 없는 배포는 eol 경과가 곧 "ended" 다.
    카탈로그 미등록 OS 는 None (판정 불가 = 침묵, false negative 한계는 의식적 트레이드오프).
    """
    if not os_id:
        return None
    if os_id == "windows":
        build = (kernel_version or "").split(".")[0]
        matches = [e for e in _EOL_CATALOG.get("windows-server", []) if e.get("build") == build]
        if not matches:
            return None
        rep = max(matches, key=lambda e: e["eol"])  # 대표 = eol 최장(LTSC) — 표시 라벨·날짜
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
    """OS 지원 발화 판정 — 무상 보안 패치가 끊긴 경우만 (eol_iso, 제품 라벨), 아니면 None.

    attention OS EOL 카드 + 보고서 정성 요약 공용 (P2 단일 판정). security_only 는 무상 패치가
    유지되므로 발화하지 않는다. paid_only 는 발화한다 — 유상 계약 여부는 수집할 수 없으므로 계약이
    없다는 쪽으로 본다. 발화하지 않는 단계도 서버별 상태 칼럼으로는 노출된다(lookup_os_eol).
    """
    info = _eol_info(os_id, os_version, kernel_version, today)
    if info is None or info.status not in ("paid_only", "ended"):
        return None
    return (info.eol_iso, info.label)


def lookup_os_eol(
    os_id: str | None, os_version: str | None, kernel_version: str | None, today: date
) -> OsEolInfo | None:
    """인벤토리 표시용 조회 — 미래 날짜도 반환. OsEolInfo 또는 미등록 시 None.

    resolve_os_eol(발화용, 무상 패치 종료만)과 달리 시스템 정보 카드·서버 목록 상태 칼럼용 —
    아직 지원 중이어도 경계 날짜와 판정 단계를 그대로 노출한다.
    """
    return _eol_info(os_id, os_version, kernel_version, today)


# 레거시 Windows Server (build <= 9600) kernel build -> 표시용 버전 라벨.
# agent 가 Server 세대 os_version 을 빈값으로 보낸다(DisplayVersion/ReleaseId 레지스트리 키가 Server 2016
# 이전엔 부재) -> 표시가 "windows" 로만 나옴. kernel build(CurrentBuildNumber, 모든 Windows 존재)로 보강.
# build <-> 제품 출처는 resolve_os_eol 과 동일 카탈로그(windows-server), 표시 라벨만 별도(같은 build 가 여러
# SP/edition 공유 — 노이즈 제외, 운영=Server 가정). 2016(14393)+ 은 미등록(레거시 한정, os_version 보강 범위 결정).
_WINDOWS_LEGACY_BUILD_LABEL: dict[str, str] = {
    "9600": "2012 R2",
    "9200": "2012",
    "7601": "2008 R2",
    "6003": "2008",
    "3790": "2003",
    "2195": "2000",
}


def windows_legacy_version_from_build(kernel_version: str | None) -> str | None:
    """레거시 Windows Server(build <= 9600) kernel build -> 표시 버전 라벨. 비레거시·미매칭은 None."""
    if not kernel_version:
        return None
    build = kernel_version.split(".")[0]
    return _WINDOWS_LEGACY_BUILD_LABEL.get(build)


# ProductName(CurrentVersion 원문) -> 짧은 표시 라벨. os_version(DisplayVersion)이 LTSC/SAC 를 구분 못 하고
# 동일 값("1809")을 공유하는 한계를 product_name 의 연도/세대 토큰으로 보강 — Server 는 연도(2019·2012 R2),
# client 는 세대(10·11). SAC(Server Core 반기 채널)는 MS 가 ProductName 에 연도를 의도적으로 안 박으므로
# ("Windows Server Datacenter") 미매칭 -> None 반환해 호출자가 os_version 폴백(SAC 는 그쪽이 정확).
_WIN_SERVER_YEAR_RE = re.compile(r"Windows Server (\d{4})(?:\s+(R2))?", re.IGNORECASE)
_WIN_CLIENT_GEN_RE = re.compile(r"Windows (10|11)\b", re.IGNORECASE)


def windows_short_label_from_product_name(product_name: str | None) -> str | None:
    """product_name 원문 -> 짧은 표시 라벨("2019"·"2012 R2"·"10"). SAC(연도 없음)·미매칭은 None(os_version 폴백)."""
    if not product_name:
        return None
    m = _WIN_SERVER_YEAR_RE.search(product_name)
    if m:
        return f"{m.group(1)} R2" if m.group(2) else m.group(1)
    m = _WIN_CLIENT_GEN_RE.search(product_name)
    return m.group(1) if m else None
