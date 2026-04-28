def _bytes_to_gb(b: int | None) -> float | None:
    return round(b / 1024 ** 3, 2) if b is not None else None


def _kb_to_gb(kb: int | None) -> float | None:
    return round(kb / 1024 ** 2, 1) if kb else None


def _usage_pct(used: int | None, total: int | None) -> float | None:
    if used is None or not total:
        return None
    return round(max(0.0, used / total * 100), 1)


def _sector_to_kbps(cur: int, prev: int, dt: float) -> float | None:
    d = cur - prev
    return None if d < 0 else round(d * 512 / 1024 / dt, 1)