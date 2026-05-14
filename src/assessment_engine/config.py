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

    # SQLAlchemy 엔진 로깅 — dev에서 SQL 디버깅 시 true. 운영 환경은 false 유지 (로그 폭증·secret 노출 위험).
    sqlalchemy_echo: bool = False

    # TTL (seconds)
    redis_ttl_idempotent: int = 86400          # 24h — 재발행 메시지 중복 차단
    redis_ttl_online: int = 90                  # 90s — 마지막 메트릭 수신 후 오프라인 판단
    redis_ttl_token: int = 3600                 # 1h  — 인증 토큰
    redis_ttl_last_agent_start: int = 86400     # 24h — 직전 agent_started_at 캐시 (재시작 감지용)
    redis_ttl_agent_restarts: int = 3600        # 1h  — 슬라이딩 윈도우 카운터
    redis_ttl_time_invariant_warned: int = 3600 # 1h  — 시계 invariant 위반 로그 쿨다운 (스팸 방지)

    # Key prefixes
    redis_key_cache_inventory: str = "cache:inventory:{}"
    redis_key_cache_metrics: str = "cache:metrics:{}"
    redis_key_cache_resolve: str = "cache:resolve:{}"
    redis_key_idempotent: str = "idempotent:{}"
    redis_key_online: str = "online:{}"
    redis_key_token: str = "token:{}"
    redis_key_last_agent_start: str = "last_agent_start:{}"
    redis_key_agent_restarts: str = "agent_restarts:{}"
    redis_key_time_invariant_warned: str = "time_invariant_warned:{}"  # {machine_id} 쿨다운 마커

    # 에이전트 재시작 alert 임계값 (1h 슬라이딩 윈도우 내 횟수). consumer 부가 시그널 + web 신호 카드 공통.
    agent_restart_alert_threshold: int = 3

    # PUB/SUB channels
    redis_channel_metrics: str = "metrics.events"

    # 원격 작업 install bundle endpoint (self-host).
    # task.install 페이로드의 download.url에 그대로 박혀 발행되고, 원격 호스트의
    # WORKER_DOWNLOAD_ALLOWED_HOSTS 화이트리스트와 host가 정확히 일치해야 fetch 허용.
    # HTTPS 강제 — 원격 호스트 worker 측 정책상 https:// 만 fetch 허용 (ADR 0008 임시).
    install_bundle_url: str = "https://host.lima.internal:8443/zconverter.tar.gz"
    install_timeout_sec: int = 600  # install.sh wall-clock timeout (원격 host의 worker가 강제 종료)

    # 2-port 분리 (ADR 0008 임시) — install bundle endpoint 만 HTTPS, 나머지(브라우저·API·healthcheck) plain HTTP.
    # 운영자 편의(브라우저 plain 접근) + agent worker HTTPS-only 정책 동시 충족 위한 dev workaround.
    # 정석은 agent 측 dev http toggle 또는 nginx ingress sidecar — 별도 ADR.
    https_port:   int = 8443
    ssl_certfile: str | None = None
    ssl_keyfile:  str | None = None

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
        # secrets 마운트 누락 fail-fast — 운영자가 `docker-compose.prod.yml` 없이 prod 기동한 사고 방지.
        # /run/secrets는 Docker secrets가 tmpfs로 마운트하는 경로. 디렉토리 자체가 없으면 secret 채널이
        # 끊긴 상태이고, pydantic-settings는 env·default로 fallback해 weak default를 통과시킬 수 있다.
        if _SECRETS_DIR is None:
            raise ValueError(
                "APP_ENV=prod but /run/secrets is not mounted. "
                "Use `docker compose -f docker-compose.yml -f docker-compose.prod.yml up` "
                "to ensure Docker secrets are mounted."
            )
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

    # 원격 작업 토폴로지 (collector exchange와 분리 — 인증·DLX 정책 독립).
    # task.install: engine 발행 / routing_key=task.install.<machine_id> / queue=agent.tasks.<machine_id> (engine 동적 declare)
    # task.result : 원격 호스트 발행 / queue=worker.result
    rabbitmq_task_exchange: str = "assessment.tasks"
    rabbitmq_task_queue_prefix: str = "agent.tasks"
    rabbitmq_task_install_key_prefix: str = "task.install"
    rabbitmq_routing_key_task_result: str = "task.result"
    rabbitmq_queue_worker_result: str = "worker.result"

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


class DiagnosticSettings(ConsumerSettings):
    """AI 진단 워커·스케줄러·웹 공통 설정 (ADR 0004).

    ConsumerSettings 상속 — broker_url·prod secret 검증 그대로 활용. 진단 워크플로 고유 필드만 추가.
    """
    # routing key + TTL (모두 RabbitMQ broker — 큐 인자 변경 시 broker 재선언 의무)
    diagnostic_routing_key: str = "diagnostic.request"
    diagnostic_queue_ttl_ms: int = 24 * 60 * 60 * 1000   # 24h — pending job 처리 못 하면 DLQ
    diagnostic_queue_max_len: int = 100_000

    # retention
    diagnostic_retention_days: int = 90

    # Redis polling 캐시 (워커가 각 단계 후 SET, web polling이 우선 read)
    redis_key_diagnostic_progress: str = "diagnostic:job:{}"   # {job_id}
    redis_ttl_diagnostic_progress: int = 3600

    # 스케줄러
    diagnostic_schedule_cron: str = "0 3 * * *"          # 매일 03시 KST
    diagnostic_active_server_window_hours: int = 24      # last_seen_at 기준 활성 서버 정의

    # LLM — 과금 발생 외부 API 호출 금지 (운영자 정책). 외부 API 도입은 별도 ADR 정정.
    llm_provider: Literal["mock", "ollama"] = "mock"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    llm_timeout_seconds: int = 60
    llm_mock_latency_seconds: float = 2.0                # mock latency 시뮬레이션 (UI progress 단계 확인용)

    # 워커 단계별 timeout cap (단일 진단 1건 전체) — 클라이언트 polling timeout(5분)과 정렬
    worker_job_timeout_seconds: int = 300


web_settings        = WebSettings()
consumer_settings   = ConsumerSettings()
diagnostic_settings = DiagnosticSettings()