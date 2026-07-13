# 디스크/스토리지 용량 — 실무정석: 값은 binary(2^30)이되 "GB" 라벨로 표기(df -h·free -h·클라우드 콘솔 관습).
# OpenStack·OS·하이퍼바이저가 2진으로 프로비저닝하므로 30GiB 디스크가 "30GB"로 떨어져 딱 맞음(10진 32GB 는 오해 유발).
# 전 경로 동일 divisor(불일치 시 합산 어긋남). 메모리와 동일 base(2진), 정밀도만 상이.
def bytes_to_gb(b: int | None) -> float | None:
    return round(b / 1024**3, 2) if b is not None else None


# 메모리(RAM)·스왑은 binary GiB(1024^3 B=1 GiB) — RAM 은 binary 가 관례. v2 는 By 단위.
def bytes_to_gib(b: int | None) -> float | None:
    return round(b / 1024**3, 1) if b else None


def usage_pct(used: int | None, total: int | None) -> float | None:
    if used is None or not total:
        return None
    return round(max(0.0, used / total * 100), 1)
