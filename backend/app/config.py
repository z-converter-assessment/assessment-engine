from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 메타데이터
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_db: str = "assessment"
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    rabbitmq_user: str = "guest"
    rabbitmq_pass: str = "guest"
    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def broker_url(self) -> str:
        return (
            f"amqp://{self.rabbitmq_user}:{self.rabbitmq_pass}"
            f"@{self.rabbitmq_host}:{self.rabbitmq_port}//"
        )


settings = Settings()