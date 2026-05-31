from datetime import UTC, datetime, timedelta, timezone

from assessment_engine.web.services.service_classifier import BADGE_CLASS_BY_CATEGORY

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


def kbps(kb: float | None) -> str:
    if kb is None:
        return "—"
    if kb >= 1024:
        return f"{round(kb / 1024, 1)} MBps"
    return f"{kb} kBps"


def or_dash(value: object) -> str:
    return str(value) if value is not None else "-"
