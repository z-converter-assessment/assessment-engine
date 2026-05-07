import os
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# pydantic-settings의 secrets_dir은 디렉토리가 존재하지 않으면 무시되지만,
# 일부 환경에서 경로 문제로 noisy 경고가 발생할 수 있어 명시적으로 분기.
# - prod 컨테이너: docker-compose `secrets:` 블록이 /run/secrets 에 마운트
# - dev (호스트 또는 dev compose): 디렉토리 없음 → None
_SECRETS_DIR = "/run/secrets" if os.path.isdir("/run/secrets") else None

# prod에서 거부할 약한 default 값 (dev/PoC 표준 자격을 prod에 그대로 흘리는 사고 방지).
# 본 프로젝트의 dev default는 "assessment". 다른 흔한 약한 값도 함께 차단.
_WEAK_VALUES = frozenset({"", "assessment", "password", "admin", "root", "changeme"})


class WebSettings(BaseSettings):
    # 우선순위: OS env > .env (cwd) > /run/secrets/<field> 파일 > 코드 default
    model_config = SettingsConfigDict(
        env_file=".env",
        secrets_dir=_SECRETS_DIR,
        extra="ignore",
    )

    # 환경 마커. prod일 때 model_validator가 약한 default를 거부.
    app_env: Literal["dev", "staging", "prod"] = "dev"

    postgres_host: str = "postgres"
    postgres_db: str = "assessment"
    postgres_user: str = "assessment"
    postgres_password: SecretStr = SecretStr("assessment")
    postgres_port: int = 5432
    web_port: int = 8000

    redis_host: str = "redis"
    redis_port: int = 6379

    # TTL (seconds)
    redis_ttl_idempotent: int = 86400   # 24h — 재발행 메시지 중복 차단
    redis_ttl_online: int = 90          # 90s — 마지막 메트릭 수신 후 오프라인 판단
    redis_ttl_token: int = 3600         # 1h  — 인증 토큰

    # Key prefixes
    redis_key_cache_inventory: str = "cache:inventory:{}"
    redis_key_cache_metrics: str = "cache:metrics:{}"
    redis_key_cache_resolve: str = "cache:resolve:{}"
    redis_key_idempotent: str = "idempotent:{}"
    redis_key_online: str = "online:{}"
    redis_key_token: str = "token:{}"

    # PUB/SUB channels
    redis_channel_metrics: str = "metrics.events"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password.get_secret_value()}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}"

    @model_validator(mode="after")
    def _validate_prod_web_secrets(self) -> "WebSettings":
        if self.app_env != "prod":
            return self
        if self.postgres_password.get_secret_value() in _WEAK_VALUES:
            raise ValueError(
                "POSTGRES_PASSWORD is unset or uses a dev default in prod. "
                "Provide via Docker secret (/run/secrets/postgres_password) or env var."
            )
        if self.postgres_user in _WEAK_VALUES:
            raise ValueError("POSTGRES_USER must be set to a non-default value in prod.")
        return self


class ConsumerSettings(WebSettings):

    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672
    rabbitmq_vhost: str = "/assessment"   # 에이전트가 발행하는 전용 vhost
    rabbitmq_user: str = "assessment"
    rabbitmq_password: SecretStr = SecretStr("assessment")
    rabbitmq_exchange: str = "assessment"
    rabbitmq_routing_key_inventory: str = "server.inventory"
    rabbitmq_routing_key_metrics: str = "server.metrics"
    rabbitmq_routing_key_error: str = "server.error"

    @property
    def broker_url(self) -> str:
        # vhost의 '/'는 AMQP URL에서 %2F로 인코딩되어야 함
        # (e.g. '/assessment' → '%2Fassessment')
        encoded_vhost = self.rabbitmq_vhost.replace("/", "%2F")
        return (
            f"amqp://{self.rabbitmq_user}:{self.rabbitmq_password.get_secret_value()}"
            f"@{self.rabbitmq_host}:{self.rabbitmq_port}/{encoded_vhost}"
        )

    @model_validator(mode="after")
    def _validate_prod_consumer_secrets(self) -> "ConsumerSettings":
        if self.app_env != "prod":
            return self
        if self.rabbitmq_password.get_secret_value() in _WEAK_VALUES:
            raise ValueError(
                "RABBITMQ_PASSWORD is unset or uses a dev default in prod. "
                "Provide via Docker secret (/run/secrets/rabbitmq_password) or env var."
            )
        if self.rabbitmq_user in _WEAK_VALUES:
            raise ValueError("RABBITMQ_USER must be set to a non-default value in prod.")
        return self


class SchedulerSettings(WebSettings):
    scheduler_interval_seconds: int = 3600  # 기본 1시간


web_settings       = WebSettings()
consumer_settings  = ConsumerSettings()
scheduler_settings = SchedulerSettings()