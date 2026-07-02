"""config.py multi-node refactor 핵심 invariant — `_validate_prod_*` weak default 거부.

본 테스트 영역은 prod 운영 안전망 단일 진실 (CLAUDE.md #A0·#F8·docs/operations/env.md 6절).
APP_ENV=prod 시 weak default(`password`/`admin`/`root`/`changeme`/``)가 흘러가지
못하게 차단. multi-node 분리 배포에서 web/consumer/diagnostic 각 노드가 자기 Settings만
인스턴스화해도 본 검증이 노드별 작동 (Composition Root #F4 정합).
"""

import pytest
from pydantic import SecretStr, ValidationError

from assessment_engine.config import _WEAK_VALUES, ConsumerSettings, DiagnosticSettings, WebSettings


def _web_kwargs(**overrides):
    """WebSettings 인스턴스화 기본 kwargs — prod 강제 + 강한 default. 개별 테스트가 override로 약한 값 주입."""
    base = {
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


def _consumer_kwargs(**overrides):
    base = _web_kwargs()
    base.update(
        {
            "rabbitmq_user": "strong_mq_user",
            "rabbitmq_password": SecretStr("strong-mq-secret-32chars"),
        }
    )
    base.update(overrides)
    return base


# ─── WebSettings — _validate_prod_web_secrets ─────────────────────────────


def test_web_settings_dev_default_passes():
    """app_env=dev면 weak default 그대로 허용 — 검증 skip."""
    # _env_file=None — 코드 default 검증이 목적이라 ambient .env(로컬 dev 스택용) 배제 (CI·로컬 무관 robust).
    s = WebSettings(app_env="dev", _env_file=None)  # user default "assessment", password default "changeme"
    assert s.app_env == "dev"
    assert s.postgres_password.get_secret_value() == "changeme"


def test_web_settings_prod_with_strong_defaults_passes():
    """prod + 강한 secret이면 검증 통과."""
    s = WebSettings(**_web_kwargs())
    assert s.app_env == "prod"
    assert s.postgres_user == "strong_user"


@pytest.mark.parametrize("weak_password", sorted(_WEAK_VALUES))
def test_web_settings_prod_rejects_weak_postgres_password(weak_password):
    """POSTGRES_PASSWORD가 weak default면 ValidationError — `_WEAK_VALUES` 단일 진실."""
    with pytest.raises(ValidationError) as exc:
        WebSettings(**_web_kwargs(postgres_password=SecretStr(weak_password)))
    assert "POSTGRES_PASSWORD" in str(exc.value)


@pytest.mark.parametrize("weak_user", sorted(_WEAK_VALUES))
def test_web_settings_prod_rejects_weak_postgres_user(weak_user):
    """POSTGRES_USER도 weak default 거부 — 사용자 식별까지 강제."""
    with pytest.raises(ValidationError) as exc:
        WebSettings(**_web_kwargs(postgres_user=weak_user))
    assert "POSTGRES_USER" in str(exc.value)


# ─── ConsumerSettings — _validate_prod_consumer_secrets + WebSettings 상속 ─


def test_consumer_settings_prod_with_strong_defaults_passes():
    """prod + 강한 secret이면 통과 (WebSettings 상속 + ConsumerSettings 검증 둘 다)."""
    s = ConsumerSettings(**_consumer_kwargs())
    assert s.rabbitmq_user == "strong_mq_user"


@pytest.mark.parametrize("weak_password", sorted(_WEAK_VALUES))
def test_consumer_settings_prod_rejects_weak_rabbitmq_password(weak_password):
    """RABBITMQ_PASSWORD weak default 거부 — broker 자격 보호."""
    with pytest.raises(ValidationError) as exc:
        ConsumerSettings(**_consumer_kwargs(rabbitmq_password=SecretStr(weak_password)))
    assert "RABBITMQ_PASSWORD" in str(exc.value)


@pytest.mark.parametrize("weak_user", sorted(_WEAK_VALUES))
def test_consumer_settings_prod_rejects_weak_rabbitmq_user(weak_user):
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
