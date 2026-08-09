# pyright: reportCallIssue=false

from typing import TYPE_CHECKING, Any

import pytest
from pydantic import SecretStr, ValidationError

from assessment_engine.config import _WEAK_VALUES, ConsumerSettings, DiagnosticSettings, WebSettings

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


def _web_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "app_env": "prod",
        "postgres_user": "strong_user",
        "postgres_password": SecretStr("strong-random-secret-32chars"),
        "postgres_db": "assessment_prod",
        "postgres_host": "db.internal",
        "zdm_default_ip": "10.20.30.40",
        "zdm_default_user": "ops@customer.example",
    }
    base.update(overrides)
    return base


def _consumer_kwargs(**overrides: Any) -> dict[str, Any]:
    base = _web_kwargs()
    base.update(
        {
            "rabbitmq_user": "strong_mq_user",
            "rabbitmq_password": SecretStr("strong-mq-secret-32chars"),
        }
    )
    base.update(overrides)
    return base


@pytest.mark.parametrize("app_env", ["dev", "prod"])
def test_password_is_required_in_every_env(app_env: str, monkeypatch: pytest.MonkeyPatch):
    for key in ("POSTGRES_PASSWORD", "RABBITMQ_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError):
        WebSettings(app_env=app_env, _env_file=None)


def test_empty_password_rejected():
    with pytest.raises(ValidationError):
        WebSettings(**_web_kwargs(postgres_password=SecretStr("")))


def test_web_settings_prod_with_strong_defaults_passes():
    s = WebSettings(**_web_kwargs())
    assert s.app_env == "prod"
    assert s.postgres_user == "strong_user"


@pytest.mark.parametrize("weak_password", sorted(_WEAK_VALUES))
def test_web_settings_prod_rejects_weak_postgres_password(weak_password: str):
    with pytest.raises(ValidationError) as exc:
        WebSettings(**_web_kwargs(postgres_password=SecretStr(weak_password)))
    assert "POSTGRES_PASSWORD" in str(exc.value)


@pytest.mark.parametrize("weak_user", sorted(_WEAK_VALUES))
def test_web_settings_prod_rejects_weak_postgres_user(weak_user: str):
    with pytest.raises(ValidationError) as exc:
        WebSettings(**_web_kwargs(postgres_user=weak_user))
    assert "POSTGRES_USER" in str(exc.value)


def test_consumer_settings_prod_with_strong_defaults_passes():
    s = ConsumerSettings(**_consumer_kwargs())
    assert s.rabbitmq_user == "strong_mq_user"


@pytest.mark.parametrize("weak_password", sorted(_WEAK_VALUES))
def test_consumer_settings_prod_rejects_weak_rabbitmq_password(weak_password: str):
    with pytest.raises(ValidationError) as exc:
        ConsumerSettings(**_consumer_kwargs(rabbitmq_password=SecretStr(weak_password)))
    assert "RABBITMQ_PASSWORD" in str(exc.value)


@pytest.mark.parametrize("weak_user", sorted(_WEAK_VALUES))
def test_consumer_settings_prod_rejects_weak_rabbitmq_user(weak_user: str):
    with pytest.raises(ValidationError) as exc:
        ConsumerSettings(**_consumer_kwargs(rabbitmq_user=weak_user))
    assert "RABBITMQ_USER" in str(exc.value)


def test_consumer_settings_prod_inherits_web_validation():
    with pytest.raises(ValidationError) as exc:
        ConsumerSettings(**_consumer_kwargs(postgres_password=SecretStr("changeme")))
    assert "POSTGRES_PASSWORD" in str(exc.value)


def test_diagnostic_settings_prod_inherits_consumer_validation():
    with pytest.raises(ValidationError) as exc:
        DiagnosticSettings(**_consumer_kwargs(rabbitmq_password=SecretStr("password")))
    assert "RABBITMQ_PASSWORD" in str(exc.value)


def test_web_settings_database_url_includes_secret_value():
    s = WebSettings(**_web_kwargs())
    url = s.database_url
    assert url.startswith("postgresql+asyncpg://")
    assert "strong_user" in url
    assert "strong-random-secret-32chars" in url


def test_consumer_settings_broker_url_encodes_vhost():
    s = ConsumerSettings(**_consumer_kwargs(rabbitmq_vhost="/assessment"))
    assert s.broker_url.endswith("/%2Fassessment")


def _prod_env(monkeypatch: pytest.MonkeyPatch, secrets_dir: Path) -> None:
    monkeypatch.setenv("SECRETS_DIR", str(secrets_dir))
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("POSTGRES_USER", "strong_user")
    monkeypatch.setenv("RABBITMQ_USER", "strong_mq_user")
    for key in ("POSTGRES_PASSWORD", "RABBITMQ_PASSWORD"):
        monkeypatch.delenv(key, raising=False)


def _patch_secrets_dir(monkeypatch: pytest.MonkeyPatch, secrets_dir: Path) -> ModuleType:
    import assessment_engine.config as config_module

    monkeypatch.setattr(config_module, "_SECRETS_DIR", str(secrets_dir))
    return config_module


def test_prod_accepts_secret_file_without_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    (tmp_path / "postgres_password").write_text("file-only-secret-32chars")
    (tmp_path / "rabbitmq_password").write_text("file-only-mq-secret-32chars")
    _prod_env(monkeypatch, tmp_path)
    config = _patch_secrets_dir(monkeypatch, tmp_path)

    web = config.WebSettings(_env_file=None, _secrets_dir=str(tmp_path))
    consumer = config.ConsumerSettings(_env_file=None, _secrets_dir=str(tmp_path))
    assert web.postgres_password.get_secret_value() == "file-only-secret-32chars"
    assert consumer.rabbitmq_password.get_secret_value() == "file-only-mq-secret-32chars"


def test_prod_rejects_env_shadowing_postgres_secret_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    (tmp_path / "postgres_password").write_text("file-secret-32chars")
    _prod_env(monkeypatch, tmp_path)
    monkeypatch.setenv("POSTGRES_PASSWORD", "env-wins-and-leaks")
    config = _patch_secrets_dir(monkeypatch, tmp_path)

    with pytest.raises(ValidationError, match="secret file is ignored"):
        config.WebSettings(_env_file=None, _secrets_dir=str(tmp_path))


def test_prod_rejects_env_shadowing_rabbitmq_secret_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    (tmp_path / "postgres_password").write_text("file-secret-32chars")
    (tmp_path / "rabbitmq_password").write_text("file-mq-secret-32chars")
    _prod_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RABBITMQ_PASSWORD", "env-wins-and-leaks")
    config = _patch_secrets_dir(monkeypatch, tmp_path)

    with pytest.raises(ValidationError, match="secret file is ignored"):
        config.ConsumerSettings(_env_file=None, _secrets_dir=str(tmp_path))


def test_env_only_channel_passes_when_no_secret_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _prod_env(monkeypatch, tmp_path)
    monkeypatch.setenv("POSTGRES_PASSWORD", "env-channel-secret-32chars")
    config = _patch_secrets_dir(monkeypatch, tmp_path)

    web = config.WebSettings(_env_file=None, _secrets_dir=str(tmp_path))
    assert web.postgres_password.get_secret_value() == "env-channel-secret-32chars"


def test_shadowing_rejected_in_dev_too(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    (tmp_path / "postgres_password").write_text("file-secret-32chars")
    _prod_env(monkeypatch, tmp_path)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("POSTGRES_PASSWORD", "env-wins")
    config = _patch_secrets_dir(monkeypatch, tmp_path)

    with pytest.raises(ValidationError, match="secret file is ignored"):
        config.WebSettings(_env_file=None, _secrets_dir=str(tmp_path))
