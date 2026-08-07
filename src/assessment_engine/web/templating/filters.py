"""Jinja2 필터 — 표시 경계의 포맷팅만 (#E1 P3).

여기 있는 것은 계산이 아니라 이미 정해진 값의 표기다. KST 변환이 이 계층에 있는 이유는 #F2 가
표시 경계 4함수에서만 변환하라고 정하기 때문이다.
"""

from datetime import UTC, datetime, timedelta, timezone

from markupsafe import Markup

from assessment_engine.domain.service_classifier import BADGE_CLASS_BY_CATEGORY

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

    실무정석: 값은 binary base(bytes_to_gb=2^30)이고 라벨은 GB/TB, 롤업도 1024(메모리와 동일 base). None 이면 '-'.
    """
    if gb is None:
        return "-"
    if gb >= 1024:
        return f"{gb / 1024:.2f} TB"
    if gb >= 1:
        return f"{gb:.2f} GB"
    return f"{gb * 1024:.2f} MB"


def disksize_styled(gb: float | None) -> Markup:
    """메모리 총량 값 + 단위(.stat-unit) 인라인. 메모리는 binary(1024) 기준. 디스크는 storagesize_styled(1000)."""
    if gb is None:
        return Markup("-")
    if gb >= 1024:
        value, unit = round(gb / 1024, 1), "TB"
    else:
        value, unit = gb, "GB"
    # value·unit 은 위에서 숫자로 계산한 값이라 사용자 입력이 들어올 경로가 없다.
    return Markup(f'{value} <span class="stat-unit">{unit}</span>')  # noqa: S704


def storagesize_styled(gb: float | None) -> Markup:
    """디스크 총량 값 + 단위(.stat-unit) 인라인. 실무정석: binary base(bytes_to_gb=2^30), 라벨 GB/TB, 롤업 1024.

    disksize_styled(메모리)와 동일 base(1024) — 디스크·메모리 KPI 표기 통일.
    """
    if gb is None:
        return Markup("-")
    if gb >= 1024:
        value, unit = round(gb / 1024, 1), "TB"
    elif gb >= 1:
        value, unit = round(gb), "GB"
    else:
        value, unit = round(gb * 1024), "MB"
    # value·unit 은 위에서 숫자로 계산한 값이라 사용자 입력이 들어올 경로가 없다.
    return Markup(f'{value} <span class="stat-unit">{unit}</span>')  # noqa: S704


def or_dash(value: object) -> str:
    return str(value) if value is not None else "-"
