# 디스크/스토리지 용량은 decimal GB(10^9) 단일 기준 — 제조사·클라우드 볼륨·df -H 산업 표준.
# 개별 서버·환경 총량·export 전 경로 동일 divisor(불일치 시 합산이 어긋남).
def bytes_to_gb(b: int | None) -> float | None:
    return round(b / 10**9, 2) if b is not None else None


# 메모리(RAM)·스왑은 binary GiB(1024^3 B=1 GiB) — 디스크와 달리 RAM 은 binary 가 관례. v2 는 By 단위.
def bytes_to_gib(b: int | None) -> float | None:
    return round(b / 1024**3, 1) if b else None


def usage_pct(used: int | None, total: int | None) -> float | None:
    if used is None or not total:
        return None
    return round(max(0.0, used / total * 100), 1)
