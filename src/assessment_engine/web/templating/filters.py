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


def disksize_styled(gb: float | None) -> Markup:
    """disksize 값 + 단위(.stat-unit, 작은 폰트·옅은 색) 인라인. 값 크기에 따라 GB/TB 유동. None 이면 '-'."""
    if gb is None:
        return Markup("-")
    if gb >= 1024:
        value, unit = round(gb / 1024, 1), "TB"
    else:
        value, unit = gb, "GB"
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
