from datetime import UTC, datetime, timedelta, timezone

from markupsafe import Markup

from assessment_engine.service_classifier import BADGE_CLASS_BY_CATEGORY

_KST = timezone(timedelta(hours=9))


def service_badge_class(category: str | None) -> str:
    return BADGE_CLASS_BY_CATEGORY.get(category or "", "")


def kst(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_KST).strftime("%Y-%m-%d %H:%M:%S")


def disksize(gb: float | None) -> str:
    if gb is None:
        return "-"
    if gb >= 1024:
        return f"{round(gb / 1024, 1)} TB"
    return f"{gb} GB"


def storagesize(gb: float | None) -> str:
    """스토리지 용량 — 소수 2자리 + 상식 단위(MB/GB/TB). 시스템 정보 스토리지 전용(정밀 표기).

    disk 용량은 decimal(bytes_to_gb=10^9) 기준이라 1000 단위 환산(디스크 벤더 관례). None 이면 '-'.
    """
    if gb is None:
        return "-"
    if gb >= 1000:
        return f"{gb / 1000:.2f} TB"
    if gb >= 1:
        return f"{gb:.2f} GB"
    return f"{gb * 1000:.2f} MB"


def disksize_styled(gb: float | None) -> Markup:
    """메모리 총량 값 + 단위(.stat-unit) 인라인. 메모리는 binary(1024) 기준. 디스크는 storagesize_styled(1000)."""
    if gb is None:
        return Markup("-")
    if gb >= 1024:
        value, unit = round(gb / 1024, 1), "TB"
    else:
        value, unit = gb, "GB"
    return Markup(f'{value} <span class="stat-unit">{unit}</span>')


def storagesize_styled(gb: float | None) -> Markup:
    """디스크 총량 값 + 단위(.stat-unit) 인라인. 디스크는 decimal(bytes_to_gb=10^9) 기준이라 1000 단위(벤더 관례).

    disksize_styled(메모리·1024)와 base 만 다름 — 디스크 KPI/보고서 총량이 storage 탭(storagesize)과 같은 base 로.
    """
    if gb is None:
        return Markup("-")
    if gb >= 1000:
        value, unit = round(gb / 1000, 1), "TB"
    elif gb >= 1:
        value, unit = round(gb), "GB"
    else:
        value, unit = round(gb * 1000), "MB"
    return Markup(f'{value} <span class="stat-unit">{unit}</span>')


def kbps(kb: float | None) -> str:
    # 단위 표기 "kB/s"/"MB/s" — 차트(fmtKbChart·fmtThroughput)·format_net_rate 와 통일.
    if kb is None:
        return "—"
    if kb >= 1024:
        return f"{round(kb / 1024, 1)} MB/s"
    return f"{kb} kB/s"


def or_dash(value: object) -> str:
    return str(value) if value is not None else "-"
