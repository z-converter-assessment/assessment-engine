"""외부 계약 API 3라우터가 공유하는 쿼리 파라미터 파싱.

`/api/assessment`·`/api/right-sizing`·`/api/exports/inventory` 는 저장소 밖 사내 자동화가 소비하는
frozen 계약이다. 쉼표 목록이라는 wire 형식 자체가 계약의 일부라 `Annotated[list[str], BeforeValidator]`
로 바꾸지 않는다 — 그러면 FastAPI 가 OpenAPI 파라미터를 array 로 내고 쿼리 파싱이 반복 파라미터
기준으로 바뀐다. 파라미터 어노테이션은 `str | None` 그대로 두고 핸들러 첫 줄에서 여기를 부른다.
"""

from datetime import UTC, datetime

WINDOW_FLOOR_DAYS = 14
"""assessment·exports 의 window_days 하한. 14 미만은 관측 부족으로 과소 사이징을 유발한다(계약 3절).

`/api/right-sizing` 은 `ge=1` 이다. 이 상수를 그쪽 하한으로 재사용하면
`?window_days=7` 이 200 에서 422 로 바뀐다 — 하한과 기본값은 다른 개념이다.
"""

WINDOW_DEFAULT_DAYS = 14
"""세 endpoint 공통 기본 창. 자원 적정성 표준 창(#F10)과 같은 값이다."""


def split_csv(value: str | None) -> list[str]:
    return [s.strip() for s in (value or "").split(",") if s.strip()]


def split_pairs(tokens: list[str]) -> list[tuple[str, str]]:
    """`hostname~discriminator` 순서쌍 파싱. discriminator 는 IP 또는 public_id.

    형식 불량 토큰은 422 가 아니라 조용히 버린다 — 배포된 계약의 현행 동작이다.
    """
    out: list[tuple[str, str]] = []
    for token in tokens:
        if "~" not in token:
            continue
        host, _, disc = token.partition("~")
        host, disc = host.strip(), disc.strip()
        if host and disc:
            out.append((host, disc))
    return out


def split_pairs_csv(value: str | None) -> list[tuple[str, str]]:
    return split_pairs(split_csv(value))


def normalize_anchor(end: datetime | None) -> datetime:
    """창 종료 시각을 tz-aware UTC 로 맞춘다. 미지정이면 현재."""
    anchor = end or datetime.now(UTC)
    return anchor.replace(tzinfo=UTC) if anchor.tzinfo is None else anchor
