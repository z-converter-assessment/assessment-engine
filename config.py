from pydantic_settings import BaseSettings, SettingsConfigDict


class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_host: str = "postgres"
    postgres_db: str = "assessment"
    postgres_user: str = "assessment"
    postgres_password: str = "assessment"
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
    redis_key_idempotent: str = "idempotent:{}"
    redis_key_online: str = "online:{}"
    redis_key_token: str = "token:{}"

    # PUB/SUB channels
    redis_channel_metrics: str = "metrics.events"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}"


class ConsumerSettings(WebSettings):

    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "assessment"
    rabbitmq_password: str = "assessment"
    rabbitmq_exchange: str = "assessment"
    rabbitmq_routing_key_inventory: str = "server.inventory"
    rabbitmq_routing_key_metrics: str = "server.metrics"
    rabbitmq_routing_key_error: str = "server.error"

    @property
    def broker_url(self) -> str:
        return (
            f"amqp://{self.rabbitmq_user}:{self.rabbitmq_password}"
            f"@{self.rabbitmq_host}:{self.rabbitmq_port}//"
        )


class SchedulerSettings(WebSettings):
    scheduler_interval_seconds: int = 3600  # 기본 1시간


web_settings       = WebSettings()
consumer_settings  = ConsumerSettings()
scheduler_settings = SchedulerSettings()

