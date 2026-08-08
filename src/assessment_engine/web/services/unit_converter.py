"""단위 변환 — By/KB/sector 를 표시 단위로 (#E1 P2).

용량은 2진(2^30)으로 나누고 라벨은 "GB" 로 쓴다. OpenStack·OS·하이퍼바이저가 2진으로 프로비저닝하므로
30GiB 디스크가 "30GB" 로 떨어지고, `df -h`·`free -h`·클라우드 콘솔 표기와도 맞는다(10진 32GB 는 오해를 부른다).
divisor 가 경로마다 다르면 합산이 어긋나므로 전 경로가 이 함수를 거친다.
"""


def bytes_to_gb(b: int | None) -> float | None:
    return round(b / 1024**3, 2) if b is not None else None


def bytes_to_gib(b: int | None) -> float | None:
    return round(b / 1024**3, 1) if b else None


def usage_pct(used: int | None, total: int | None) -> float | None:
    if used is None or not total:
        return None
    return round(max(0.0, used / total * 100), 1)
