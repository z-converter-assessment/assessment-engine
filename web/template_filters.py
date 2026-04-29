from datetime import datetime, timedelta, timezone

_KST = timezone(timedelta(hours=9))


def kst(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_KST).strftime("%Y-%m-%d %H:%M")


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