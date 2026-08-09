"""컴포넌트별 Settings class 정의 — 인스턴스는 만들지 않는다 (#F4).

module-level 인스턴스를 두면 import 만으로 접속 정보를 요구하게 되고, 그러면 비밀번호를 필수 필드로
둘 수 없다. 인스턴스는 Composition Root 6곳이 사용 시점에 만든다.
"""

import os
from pathlib import Path
from typing import Literal, Self
from urllib.parse import quote

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _secrets_dir() -> str | None:
    path = os.environ.get("SECRETS_DIR", "/run/secrets")
    return path if Path(path).is_dir() else None


# 외부 인프라가 secret 을 어떤 채널로 주입하든(systemd EnvironmentFile·Vault·k8s Secret·Docker secrets)
# 동작해야 해서 env 와 secrets_dir 을 둘 다 연다. 채널 강제는 본 repo 밖 — 결과만 검증한다.
_SECRETS_DIR = _secrets_dir()


_WEAK_VALUES = frozenset({"password", "admin", "root", "changeme"})


def _reject_env_shadowing_secret(field: str) -> None:
    """secret 파일과 같은 이름의 환경변수가 함께 있으면 거부한다.

    우선순위상 환경변수가 이겨서 파일 채널이 조용히 무력화된다 — 노출 회피를 의도했는데 값이
    컨테이너 env 에 그대로 뜨고, 실패도 경고도 없어 운영자가 알아채지 못한다. compose `env_file` 은
    값을 환경변수로 주입하므로 컨테이너 경로가 이 검사에 걸리고, 호스트에서 pydantic 이 `.env` 를
    직접 읽는 경로는 환경변수를 거치지 않아 잡히지 않는다.
    """
    if _SECRETS_DIR is None:
        return
    # pydantic-settings 는 기본이 case_sensitive=False 라 소문자 env 도 secret 파일을 이긴다.
    if not any(key.lower() == field for key in os.environ):
        return
    if (Path(_SECRETS_DIR) / field).is_file():
        raise ValueError(
            f"{field.upper()} is set in the environment while {_SECRETS_DIR}/{field} exists. "
            "The environment value takes precedence, so the secret file is ignored and the value "
            "is exposed in the container env. Remove it from .env (and OS env) to use the file channel."
        )


class WebSettings(BaseSettings):
    # 우선순위: OS env > .env (cwd) > <SECRETS_DIR>/<field> 파일 > 코드 default
    model_config = SettingsConfigDict(
        env_file=".env",
        secrets_dir=_SECRETS_DIR,
        extra="ignore",
    )

    # 정적 자원 캐시 무효화 분기에만 쓴다 (web lifespan) — 비밀번호 검증은 이 값을 보지 않는다.
    app_env: Literal["dev", "staging", "prod"] = "dev"

    log_format: Literal["text", "json"] = "text"

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    postgres_host: str = "postgres"
    postgres_db: str = "assessment"
    postgres_user: str = Field(default="assessment", min_length=1)
    # 기본값을 두지 않는다 — 미설정이 조용히 통과하면 그것을 거르는 검사가 또 필요해진다. 값은 env·secret

    postgres_password: SecretStr = Field(min_length=1)
    postgres_port: int = 5432
    web_port: int = 8000

    web_reload: bool = False

    redis_host: str = "redis"
    redis_port: int = 6379

    sqlalchemy_echo: bool = False

    redis_ttl_idempotent: int = 86400
    redis_ttl_online: int = 300
    redis_ttl_last_agent_start: int = 86400
    redis_ttl_agent_restarts: int = 3600
    redis_ttl_time_invariant_warned: int = 3600
    redis_ttl_cache_metrics: int = 60
    redis_ttl_cache_detail: int = 300

    redis_key_cache_inventory: str = "cache:inventory:{}"
    redis_key_cache_metrics: str = "cache:metrics:{}"
    redis_key_cache_resolve: str = "cache:resolve:{}"
    redis_key_idempotent: str = "idempotent:{}"
    redis_key_online: str = "online:{}"
    redis_key_last_agent_start: str = "last_agent_start:{}"
    redis_key_agent_restarts: str = "agent_restarts:{}"
    redis_key_time_invariant_warned: str = "time_invariant_warned:{}"

    agent_restart_alert_threshold: int = 3

    # install 모달 기본값 — POST body 누락 시 fallback. 오발행 방어는 런타임(resolver 503 차단)과 agent host

    zdm_default_ip: str = ""
    zdm_default_user: str = "admin@zconverter.com"

    zdm_package_path: str = "/download/ZConverter_CloudSource_Setup_Linux.tar.gz"
    zdm_package_script: str = "zconverter_install_source/install.sh"
    # Windows install (install.type=direct_exec). single binary 라 script 없음.
    zdm_package_path_windows: str = "/download/ZConverter_CloudSource_Setup_Windows.exe"
    zdm_meta_connect_timeout_sec: float = 5.0
    zdm_meta_total_timeout_sec: float = 120.0
    redis_key_zdm_package_sha256: str = "cache:zdm_package:sha256:{}:{}"
    redis_ttl_zdm_package_sha256: int = 6 * 60 * 60
    install_timeout_sec: int = 600  # install.sh wall-clock timeout (원격 host worker 강제 종료)

    # (오프라인 호스트 store-and-forward 유예 = 이 창). 픽업 후 스크립트 실행 예산인 install_timeout_sec 과 별개.

    install_task_deadline_sec: int = 3600

    @property
    def database_url(self) -> str:
        # `openssl rand -base64` 로 만든 secret 은 `/`·`+`·`=` 를 포함할 수 있고, 그대로 삽입하면 netloc

        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password.get_secret_value(), safe="")
        return f"postgresql+asyncpg://{user}:{password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}"

    @model_validator(mode="after")
    def _validate_web_secrets(self) -> Self:

        _reject_env_shadowing_secret("postgres_password")
        if self.postgres_password.get_secret_value() in _WEAK_VALUES:
            raise ValueError(
                "POSTGRES_PASSWORD uses an obvious value. "
                "Provide via env var or secret channel (systemd EnvironmentFile·Vault·k8s Secret etc)."
            )
        if self.postgres_user in _WEAK_VALUES:
            raise ValueError("POSTGRES_USER must not be an obvious value (password/admin/root/changeme).")
        return self


class WorkerSettings(WebSettings):
    """전용 백그라운드 워커 프로세스 설정 — 비동기 보고서 생성 + install task reaper.

    두 루프 다 DB job claim 이라 broker 를 쓰지 않는다 — ConsumerSettings 가 아니라 WebSettings 를
    상속해 워커 노드에 broker 자격증명을 요구하지 않는다.
    """

    # shutdown_timeout: graceful 시 진행 중 1건을 여기까지 기다리고, 초과분은 running 잔류라 다음 기동이 회수한다.
    report_worker_poll_interval_sec: float = 2.0
    report_worker_stale_seconds: int = 600
    report_worker_shutdown_timeout_sec: float = 10.0

    # reaper 는 deadline 지난 pending 을 다음 emit 없이 능동 전이한다. shutdown_timeout 이 짧은 건 tick 이 UPDATE 1건이라서.
    install_reaper_interval_sec: float = 60.0
    install_reaper_shutdown_timeout_sec: float = 5.0


class ConsumerSettings(WebSettings):
    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672
    rabbitmq_vhost: str = "assessment"
    rabbitmq_user: str = Field(default="assessment", min_length=1)

    rabbitmq_password: SecretStr = Field(min_length=1)
    rabbitmq_exchange: str = "assessment"
    rabbitmq_routing_key_inventory: str = "server.inventory"
    rabbitmq_routing_key_metrics: str = "server.metrics"
    rabbitmq_routing_key_error: str = "server.error"

    rabbitmq_task_exchange: str = "assessment.tasks"
    rabbitmq_task_queue_prefix: str = "agent.tasks"
    rabbitmq_task_install_key_prefix: str = "task.install"
    rabbitmq_routing_key_task_result: str = "task.result"
    rabbitmq_queue_worker_result: str = "worker.result"

    # docs/reference/contracts/env.md TASK_INSTALL_SUCCESS_EXIT_CODES 단일 진실.
    # Windows 는 빌드번호별 키를 두면 누락에 취약해(예 2008R2=7601) family 한 키로 둔다. 설치 성공은 해당 호스트

    task_install_success_exit_codes: dict[str, list[int]] = {
        "windows": [2],
        "rocky:9": [3],
        "almalinux:9": [3],
        "ol:9": [3],
        "centos:9": [3],
    }

    @property
    def broker_url(self) -> str:
        # secret 에 `/`·`+`·`=` 가 있으면 yarl(aio-pika 내부 파서)이 netloc 을 못 갈라 "port can't be converted

        user = quote(self.rabbitmq_user, safe="")
        password = quote(self.rabbitmq_password.get_secret_value(), safe="")
        encoded_vhost = self.rabbitmq_vhost.replace("/", "%2F")
        return f"amqp://{user}:{password}@{self.rabbitmq_host}:{self.rabbitmq_port}/{encoded_vhost}"

    def agent_task_queue(self, agent_id: str) -> str:
        return f"{self.rabbitmq_task_queue_prefix}.{agent_id}"

    def task_install_routing_key(self, agent_id: str) -> str:
        return f"{self.rabbitmq_task_install_key_prefix}.{agent_id}"

    @model_validator(mode="after")
    def _validate_consumer_secrets(self) -> Self:
        _reject_env_shadowing_secret("rabbitmq_password")
        if self.rabbitmq_password.get_secret_value() in _WEAK_VALUES:
            raise ValueError(
                "RABBITMQ_PASSWORD uses an obvious value. "
                "Provide via env var or secret channel (systemd EnvironmentFile·Vault·k8s Secret 등)."
            )
        if self.rabbitmq_user in _WEAK_VALUES:
            raise ValueError("RABBITMQ_USER must not be an obvious value (password/admin/root/changeme).")
        return self


class DiagnosticSettings(ConsumerSettings):
    """web 의 task.install 발행용 설정 — 고유 필드 없이 ConsumerSettings 를 그대로 쓴다."""
