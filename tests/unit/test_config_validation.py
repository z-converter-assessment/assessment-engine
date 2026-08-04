# pyright: reportCallIssue=false
#   `_env_file`·`_secrets_dir` 는 pydantic-settings 가 실제로 받는 init 인자이고 비밀번호는 env·파일에서
#   온다. pyright 는 dataclass_transform 이 만든 시그니처만 보고 둘 다 오류로 읽는다.
"""config.py 설정 안전망 — 비밀번호 필수·뻔한 값 거부·채널 충돌 거부.

검증은 환경으로 갈리지 않는다. 비밀번호에 기본값이 없어 미설정은 어디서든 실패하고,
뻔한 값(`password`/`admin`/`root`/`changeme`)과 secret 파일·환경변수 동시 존재도 마찬가지다.
multi-node 분리 배포에서 web/consumer/diagnostic 각 노드가 자기 Settings 만 만들어도
노드별로 작동한다 (Composition Root #F4 정합).
"""

from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from assessment_engine.config import _WEAK_VALUES, ConsumerSettings, DiagnosticSettings, WebSettings


def _web_kwargs(**overrides: Any) -> dict[str, Any]:
    """WebSettings 인스턴스화 기본 kwargs — prod 강제 + 강한 default. 개별 테스트가 override로 약한 값 주입."""
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


# ─── WebSettings — _validate_web_secrets ──────────────────────────────────


@pytest.mark.parametrize("app_env", ["dev", "prod"])
def test_password_is_required_in_every_env(app_env: str, monkeypatch: pytest.MonkeyPatch):
    """비밀번호에 기본값을 두지 않는다 — 환경과 무관하게 미설정이면 인스턴스화가 실패한다."""
    # 코드 default 검증이 목적이라 값을 주는 채널을 전부 끊는다 — conftest autouse env·ambient .env 둘 다.
    for key in ("POSTGRES_PASSWORD", "RABBITMQ_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError):
        WebSettings(app_env=app_env, _env_file=None)


def test_empty_password_rejected():
    """빈 문자열은 필드 제약(min_length)이 잡는다 — weak 목록이 다룰 일이 아니다."""
    with pytest.raises(ValidationError):
        WebSettings(**_web_kwargs(postgres_password=SecretStr("")))


def test_web_settings_prod_with_strong_defaults_passes():
    """prod + 강한 secret이면 검증 통과."""
    s = WebSettings(**_web_kwargs())
    assert s.app_env == "prod"
    assert s.postgres_user == "strong_user"


@pytest.mark.parametrize("weak_password", sorted(_WEAK_VALUES))
def test_web_settings_prod_rejects_weak_postgres_password(weak_password: str):
    """POSTGRES_PASSWORD가 weak default면 ValidationError — `_WEAK_VALUES` 단일 진실."""
    with pytest.raises(ValidationError) as exc:
        WebSettings(**_web_kwargs(postgres_password=SecretStr(weak_password)))
    assert "POSTGRES_PASSWORD" in str(exc.value)


@pytest.mark.parametrize("weak_user", sorted(_WEAK_VALUES))
def test_web_settings_prod_rejects_weak_postgres_user(weak_user: str):
    """POSTGRES_USER도 weak default 거부 — 사용자 식별까지 강제."""
    with pytest.raises(ValidationError) as exc:
        WebSettings(**_web_kwargs(postgres_user=weak_user))
    assert "POSTGRES_USER" in str(exc.value)


# ─── ConsumerSettings — _validate_consumer_secrets + WebSettings 상속 ─────


def test_consumer_settings_prod_with_strong_defaults_passes():
    """prod + 강한 secret이면 통과 (WebSettings 상속 + ConsumerSettings 검증 둘 다)."""
    s = ConsumerSettings(**_consumer_kwargs())
    assert s.rabbitmq_user == "strong_mq_user"


@pytest.mark.parametrize("weak_password", sorted(_WEAK_VALUES))
def test_consumer_settings_prod_rejects_weak_rabbitmq_password(weak_password: str):
    """RABBITMQ_PASSWORD weak default 거부 — broker 자격 보호."""
    with pytest.raises(ValidationError) as exc:
        ConsumerSettings(**_consumer_kwargs(rabbitmq_password=SecretStr(weak_password)))
    assert "RABBITMQ_PASSWORD" in str(exc.value)


@pytest.mark.parametrize("weak_user", sorted(_WEAK_VALUES))
def test_consumer_settings_prod_rejects_weak_rabbitmq_user(weak_user: str):
    """RABBITMQ_USER도 weak default 거부."""
    with pytest.raises(ValidationError) as exc:
        ConsumerSettings(**_consumer_kwargs(rabbitmq_user=weak_user))
    assert "RABBITMQ_USER" in str(exc.value)


def test_consumer_settings_prod_inherits_web_validation():
    """ConsumerSettings은 WebSettings 상속 — POSTGRES_PASSWORD weak도 거부 의무."""
    with pytest.raises(ValidationError) as exc:
        ConsumerSettings(**_consumer_kwargs(postgres_password=SecretStr("changeme")))
    assert "POSTGRES_PASSWORD" in str(exc.value)


# ─── DiagnosticSettings — ConsumerSettings 상속, 추가 검증 없음 ────────────


def test_diagnostic_settings_prod_inherits_consumer_validation():
    """DiagnosticSettings은 ConsumerSettings 상속 — RABBITMQ·POSTGRES weak 둘 다 거부."""
    with pytest.raises(ValidationError) as exc:
        DiagnosticSettings(**_consumer_kwargs(rabbitmq_password=SecretStr("password")))
    assert "RABBITMQ_PASSWORD" in str(exc.value)


# ─── broker_url·database_url 조립 ──────────────────────────────────────────


def test_web_settings_database_url_includes_secret_value():
    """database_url은 SecretStr을 .get_secret_value()로 추출. asyncpg URL 형식 정합."""
    s = WebSettings(**_web_kwargs())
    url = s.database_url
    assert url.startswith("postgresql+asyncpg://")
    assert "strong_user" in url
    assert "strong-random-secret-32chars" in url


def test_consumer_settings_broker_url_encodes_vhost():
    """rabbitmq_vhost의 `/`는 AMQP URL에서 %2F로 인코딩 의무 — broker 연결 정합."""
    s = ConsumerSettings(**_consumer_kwargs(rabbitmq_vhost="/assessment"))
    # URL spec: amqp://user:pass@host:port/<encoded_vhost>. port와 vhost 사이 `/`는 raw,
    # vhost 자체의 `/`는 %2F. `/assessment`라는 vhost는 `%2Fassessment`로 인코딩되어 들어감.
    assert s.broker_url.endswith("/%2Fassessment")


# ─── secret 파일 vs 환경변수 충돌 — _reject_env_shadowing_secret ───────────


def _prod_env(monkeypatch: pytest.MonkeyPatch, secrets_dir: Path) -> None:
    """prod 기동에 필요한 최소 env. 개별 테스트가 비밀번호만 추가로 넣는다."""
    monkeypatch.setenv("SECRETS_DIR", str(secrets_dir))
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("POSTGRES_USER", "strong_user")
    monkeypatch.setenv("RABBITMQ_USER", "strong_mq_user")
    for key in ("POSTGRES_PASSWORD", "RABBITMQ_PASSWORD"):
        monkeypatch.delenv(key, raising=False)


def _patch_secrets_dir(monkeypatch: pytest.MonkeyPatch, secrets_dir: Path) -> ModuleType:
    """_SECRETS_DIR 는 import 시점에 굳는다. reload 는 모듈 전역을 갈아끼우고 복원하지 않아 뒤 테스트가
    그 값을 물려받으므로, monkeypatch 로 바꾸고 인스턴스화 때 `_secrets_dir` 을 함께 넘긴다."""
    import assessment_engine.config as config_module

    monkeypatch.setattr(config_module, "_SECRETS_DIR", str(secrets_dir))
    return config_module


def test_prod_accepts_secret_file_without_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """파일 채널만 쓰면 통과 — 값은 secrets_dir 에서 온다."""
    (tmp_path / "postgres_password").write_text("file-only-secret-32chars")
    (tmp_path / "rabbitmq_password").write_text("file-only-mq-secret-32chars")
    _prod_env(monkeypatch, tmp_path)
    config = _patch_secrets_dir(monkeypatch, tmp_path)

    web = config.WebSettings(_env_file=None, _secrets_dir=str(tmp_path))
    consumer = config.ConsumerSettings(_env_file=None, _secrets_dir=str(tmp_path))
    assert web.postgres_password.get_secret_value() == "file-only-secret-32chars"
    assert consumer.rabbitmq_password.get_secret_value() == "file-only-mq-secret-32chars"


def test_prod_rejects_env_shadowing_postgres_secret_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """env 가 secret 파일을 가리면 거부 — 우선순위상 파일이 무시돼 노출 회피가 무너진다."""
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
    """secret 파일이 없으면 env 채널이 정상 경로 — 충돌이 아니다 (#A0 채널 비강제)."""
    _prod_env(monkeypatch, tmp_path)
    monkeypatch.setenv("POSTGRES_PASSWORD", "env-channel-secret-32chars")
    config = _patch_secrets_dir(monkeypatch, tmp_path)

    web = config.WebSettings(_env_file=None, _secrets_dir=str(tmp_path))
    assert web.postgres_password.get_secret_value() == "env-channel-secret-32chars"


def test_shadowing_rejected_in_dev_too(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """환경으로 강도를 가르지 않는다 — dev 에서도 채널이 겹치면 거부한다."""
    (tmp_path / "postgres_password").write_text("file-secret-32chars")
    _prod_env(monkeypatch, tmp_path)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("POSTGRES_PASSWORD", "env-wins")
    config = _patch_secrets_dir(monkeypatch, tmp_path)

    with pytest.raises(ValidationError, match="secret file is ignored"):
        config.WebSettings(_env_file=None, _secrets_dir=str(tmp_path))
