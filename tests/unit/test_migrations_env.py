"""migrations/env.py 의 alembic Config 런타임 주입 패턴 회귀 — configparser interpolation 안전성.

`config.set_main_option("sqlalchemy.url", ...)`은 내부적으로 configparser의 `%`-interpolation을 거친다.
`WebSettings.database_url`은 비밀번호를 `quote(..., safe="")`로 URL-인코딩하는데, `openssl rand -base64 32`
같은 생성 방식은 `/`·`+`를 흔히 포함해(44자 중 `+`나 `/`가 하나도 없을 확률 26% 수준) `%2F`/`%2B` 같은 리터럴
`%`를 만든다. env.py가 값을 이스케이프 없이 그대로 넘기면 "invalid interpolation syntax"로 죽는다(실제 재현:
릴리즈 배포 검증 중 bootstrap.sh 권장 명령으로 생성한 비밀번호로 migrate 컨테이너가 이 예외로 exit 1).
"""

from alembic.config import Config

from assessment_engine.config import WebSettings


def _escape_for_alembic(url: str) -> str:
    """migrations/env.py와 동일 이스케이프 — get 시 interpolation이 %% -> % 로 되돌린다."""
    return url.replace("%", "%%")


def test_database_url_with_slash_and_plus_password_survives_set_main_option():
    """`/`·`+` 포함 비밀번호(quote 후 %2F/%2B) — 이스케이프 없이 넣으면 ValueError, 이스케이프하면 통과."""
    settings = WebSettings(postgres_password="Jp/MmSP9QM6gizrtHEYTKP0gFHi3pr1BGworLrjBqt4=")  # pyright: ignore[reportArgumentType]
    url = settings.database_url
    assert "%2F" in url or "%2B" in url or "%3D" in url  # 인코딩으로 리터럴 % 발생 전제 확인

    config = Config()
    config.set_main_option("sqlalchemy.url", _escape_for_alembic(url))

    assert config.get_main_option("sqlalchemy.url") == url
    section = config.get_section(config.config_ini_section, {})
    assert section["sqlalchemy.url"] == url


def test_database_url_without_percent_char_unaffected():
    """`%` 없는 값(단순 비밀번호)은 이스케이프 유무와 무관하게 동일 URL로 왕복."""
    settings = WebSettings(postgres_password="plainpassword123")  # pyright: ignore[reportArgumentType]
    url = settings.database_url
    assert "%" not in url

    config = Config()
    config.set_main_option("sqlalchemy.url", _escape_for_alembic(url))
    assert config.get_main_option("sqlalchemy.url") == url
