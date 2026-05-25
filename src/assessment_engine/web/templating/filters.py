from datetime import UTC, datetime, timedelta, timezone

_KST = timezone(timedelta(hours=9))

_BADGE_CLASSES: dict[str, str] = {
    "web": "badge-cat-web",
    "db": "badge-cat-db",
    "cache": "badge-cat-cache",
    "mq": "badge-cat-mq",
    "container": "badge-cat-container",
    "monitor": "badge-cat-monitor",
    "unknown": "badge-cat-unknown",
}


def service_badge_class(category: str | None) -> str:
    return _BADGE_CLASSES.get(category or "", "")


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
