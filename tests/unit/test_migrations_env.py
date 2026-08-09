from alembic.config import Config

from assessment_engine.config import WebSettings


def _escape_for_alembic(url: str) -> str:
    return url.replace("%", "%%")


def test_database_url_with_slash_and_plus_password_survives_set_main_option():
    settings = WebSettings(postgres_password="Jp/MmSP9QM6gizrtHEYTKP0gFHi3pr1BGworLrjBqt4=")  # pyright: ignore[reportArgumentType]
    url = settings.database_url
    assert "%2F" in url or "%2B" in url or "%3D" in url

    config = Config()
    config.set_main_option("sqlalchemy.url", _escape_for_alembic(url))

    assert config.get_main_option("sqlalchemy.url") == url
    section = config.get_section(config.config_ini_section, {})
    assert section["sqlalchemy.url"] == url


def test_database_url_without_percent_char_unaffected():
    settings = WebSettings(postgres_password="plainpassword123")  # pyright: ignore[reportArgumentType]
    url = settings.database_url
    assert "%" not in url

    config = Config()
    config.set_main_option("sqlalchemy.url", _escape_for_alembic(url))
    assert config.get_main_option("sqlalchemy.url") == url
