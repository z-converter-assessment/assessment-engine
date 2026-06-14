import os
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 외부 인프라가 secret을 어떻게 주입하든(systemd EnvironmentFile·Vault·k8s Secret·Docker secrets 등)
# pydantic-settings는 env 우선·secrets_dir fallback 둘 다 지원. secrets_dir은 디렉토리가 존재할 때만 활성.
# 본 repo는 secret 채널 자체를 강제하지 않음(CLAUDE.md #A0) — _validate_prod_*가 결과(weak default 거부)만 검증.
_SECRETS_DIR = os.environ.get("SECRETS_DIR", "/run/secrets")
_SECRETS_DIR = _SECRETS_DIR if os.path.isdir(_SECRETS_DIR) else None

# prod에서 거부할 약한 default 값 (dev/PoC 표준 자격을 prod에 그대로 흘리는 사고 방지).
# prod 기동 거부할 약한 값 (USER·PASSWORD 공용). "assessment"(dev default)는 운영 편의로 허용 제외 —
# 빈값·뻔한 값만 차단. USER/PASSWORD 동일 정책.
_WEAK_VALUES = frozenset({"", "password", "admin", "root", "changeme"})


class WebSettings(BaseSettings):
    # 우선순위: OS env > .env (cwd) > <SECRETS_DIR>/<field> 파일 > 코드 default
    # SECRETS_DIR env로 주입 경로 override 가능 (default `/run/secrets`).
    model_config = SettingsConfigDict(
        env_file=".env",
        secrets_dir=_SECRETS_DIR,
        extra="ignore",
    )

    # 환경 마커. prod일 때 model_validator가 약한 default를 거부.
    app_env: Literal["dev", "staging", "prod"] = "dev"

    # 로그 출력 format. dev=text(colorized·grep 친화), prod=json(외부 log aggregator indexing).
    # JSON 출력 시 loguru `serialize=True` — record를 JSON으로 변환 후 stdout.
    log_format: Literal["text", "json"] = "text"

    postgres_host: str = "postgres"
    postgres_db: str = "assessment"
    postgres_user: str = "assessment"
    # default 는 weak(changeme) — 미설정 시 prod 거부 강제 (명시 assessment 는 허용). USER 는 식별자라 default 허용.
    postgres_password: SecretStr = SecretStr("changeme")
    postgres_port: int = 5432
    web_port: int = 8000
    # uvicorn auto-reload — dev hot-reload 전용. prod 는 False (코드 변경 감시 프로세스가 prod 에서 불필요,
    # bind mount 없는 wheel/image 배포에선 무의미). 루트 docker-compose.yml 이 WEB_RELOAD=${WEB_RELOAD:-true} 주입
    # (web/__main__.py 가 reload_dirs=bind mount 패키지 경로 지정 — WORKDIR /app 밖이라 cwd watch 불가).
    web_reload: bool = False

    redis_host: str = "redis"
    redis_port: int = 6379

    # SQLAlchemy 엔진 로깅 — dev에서 SQL 디버깅 시 true. 운영 환경은 false 유지 (로그 폭증·secret 노출 위험).
    sqlalchemy_echo: bool = False

    # TTL (seconds)
    redis_ttl_idempotent: int = 86400  # 24h — 재발행 메시지 중복 차단
    redis_ttl_online: int = 300  # 5min — 오프라인 판단. 운영 신호 "통신 끊김" 임계(gap_minutes=5) 와 단일 진실.
    redis_ttl_token: int = 3600  # 1h  — 인증 토큰
    redis_ttl_last_agent_start: int = 86400  # 24h — 직전 agent_started_at 캐시 (재시작 감지용)
    redis_ttl_agent_restarts: int = 3600  # 1h  — 슬라이딩 윈도우 카운터
    redis_ttl_time_invariant_warned: int = 3600  # 1h  — 시계 invariant 위반 로그 쿨다운 (스팸 방지)

    # Key prefixes
    redis_key_cache_inventory: str = "cache:inventory:{}"
    redis_key_cache_metrics: str = "cache:metrics:{}"
    redis_key_cache_resolve: str = "cache:resolve:{}"
    redis_key_idempotent: str = "idempotent:{}"
    redis_key_online: str = "online:{}"
    redis_key_token: str = "token:{}"
    redis_key_last_agent_start: str = "last_agent_start:{}"
    redis_key_agent_restarts: str = "agent_restarts:{}"
    # {composite_id}:{hostname} 쿨다운 마커 (#C1)
    redis_key_time_invariant_warned: str = "time_invariant_warned:{}:{}"

    # 에이전트 재시작 alert 임계값 (1h 슬라이딩 윈도우 내 횟수). consumer 부가 시그널 + web 신호 카드 공통.
    # 운영 alert 튜닝 노브 — env 카탈로그 미수록(env.example·env.md), 필요 시 env override.
    agent_restart_alert_threshold: int = 3

    # ZConverter Cloud Source Setup (ZDM) 서버 기본 좌표 — install 모달 default 값.
    # 운영자가 모달에서 매 발행마다 override 가능. POST body 누락 시 본 값으로 fallback.
    # dev 한정 ZDM mock (ADR 0018) 으로 libvirt VM agent worker 가 host web 8000 도달 (dev/.env 가 host IP 주입).
    # 잘못된 ZDM 발행 방어는 런타임 (HttpZdmPackageResolver 메타 도달 실패 시 503 차단) + agent host
    # whitelist (WORKER_DOWNLOAD_ALLOWED_HOSTS) 가 담당. startup 거부 없음 — 빈값 default 정상 동작.
    zdm_default_ip: str = ""
    zdm_default_user: str = "admin@zconverter.com"
    # resolver(엔진) 가 sha256/size 산출용으로 fetch 하는 호스트 override. download.url·install args 는
    # zdm_default_ip 유지. dev 한정 — mock 이 web 컨테이너 자기 자신이라 host publish 포트 hairpin 불가하면
    # localhost 로 fetch. prod 빈값(엔진이 real ZDM 직접 도달).
    zdm_resolver_host_override: str = ""

    # ZDM 본체 패키지 contract — task.install download 필드에 박혀 agent 가 fetch.
    # ZDM 제품 layout 상수라 거의 안 바뀜 — env 카탈로그 미수록(env.example·env.md), 필요 시 env override.
    # sha256·size_bytes 는 publish 직전 engine 이 ZDM 에서 HEAD + GET 으로 동적 산출
    # (ETag 기반 Redis cache). ZDM 측이 패키지 갱신하면 ETag 변경 → cache miss → 자동 재계산.
    zdm_package_path: str = "/download/ZConverter_CloudSource_Setup_Linux.tar.gz"
    zdm_package_script: str = "zconverter_install_source/install.sh"
    # Windows install (ADR 0019 install.type=direct_exec). single binary 라 script 없음.
    zdm_package_path_windows: str = "/download/ZConverter_CloudSource_Setup_Windows.exe"
    # 메타 조회 HTTP 옵션 — connect 5s, total 120s (44MB GET 가정, 동일 LAN 이면 1~2s).
    zdm_meta_connect_timeout_sec: float = 5.0
    zdm_meta_total_timeout_sec: float = 120.0
    # ETag 기반 cache TTL — ETag 자체가 invalidation 이라 길게 잡아도 안전.
    redis_key_zdm_package_sha256: str = "cache:zdm_package:sha256:{}:{}"  # {host}:{etag}
    redis_ttl_zdm_package_sha256: int = 6 * 60 * 60  # 6h
    install_timeout_sec: int = 600  # install.sh wall-clock timeout (원격 host worker 강제 종료)

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
        # 외부 인프라가 secret을 어떻게 주입하든(env·secrets_dir·EnvironmentFile·Vault 등) 결과만 검증.
        # 채널 자체는 본 repo 책임 밖 (CLAUDE.md #A0). weak default 통과 차단이 핵심.
        if self.postgres_password.get_secret_value() in _WEAK_VALUES:
            raise ValueError(
                "POSTGRES_PASSWORD is unset or uses a dev default in prod. "
                "Provide via env var or secret channel (systemd EnvironmentFile·Vault·k8s Secret 등)."
            )
        if self.postgres_user in _WEAK_VALUES:
            raise ValueError("POSTGRES_USER must not be a weak value (empty/password/admin/root/changeme) in prod.")
        return self


class ConsumerSettings(WebSettings):
    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672
    rabbitmq_vhost: str = "/assessment"  # 에이전트가 발행하는 전용 vhost
    rabbitmq_user: str = "assessment"
    # default 는 weak(changeme) — 미설정 시 prod 거부 강제 (명시 assessment 는 허용). USER 는 식별자라 default 허용.
    rabbitmq_password: SecretStr = SecretStr("changeme")
    rabbitmq_exchange: str = "assessment"
    rabbitmq_routing_key_inventory: str = "server.inventory"
    rabbitmq_routing_key_metrics: str = "server.metrics"
    rabbitmq_routing_key_error: str = "server.error"

    # 원격 작업 토폴로지 (collector exchange와 분리 — 인증·DLX 정책 독립).
    # task.install: engine 발행
    #   routing_key=task.install.<composite_id> / queue=agent.tasks.<composite_id> (engine 동적 declare)
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

    # 호스트별 task 큐·routing key 합성 단일 진실 — prefix(config) + composite_id(런타임).
    # agent 와 합의된 형식이라 양쪽이 동일 규칙으로 합성해야 함 (#B). 발행 측·소비 측 모두 본 메서드 경유.
    def agent_task_queue(self, composite_id: str) -> str:
        """task.install 발행 대상 호스트별 큐 이름 — `{prefix}.{composite_id}`."""
        return f"{self.rabbitmq_task_queue_prefix}.{composite_id}"

    def task_install_routing_key(self, composite_id: str) -> str:
        """task.install 호스트별 routing key — `{prefix}.{composite_id}`."""
        return f"{self.rabbitmq_task_install_key_prefix}.{composite_id}"

    @model_validator(mode="after")
    def _validate_prod_consumer_secrets(self) -> "ConsumerSettings":
        if self.app_env != "prod":
            return self
        if self.rabbitmq_password.get_secret_value() in _WEAK_VALUES:
            raise ValueError(
                "RABBITMQ_PASSWORD is unset or uses a dev default in prod. "
                "Provide via env var or secret channel (systemd EnvironmentFile·Vault·k8s Secret 등)."
            )
        if self.rabbitmq_user in _WEAK_VALUES:
            raise ValueError("RABBITMQ_USER must not be a weak value (empty/password/admin/root/changeme) in prod.")
        return self


class DiagnosticSettings(ConsumerSettings):
    """web 의 task.install 발행용 설정.

    ConsumerSettings 상속 — broker_url·task exchange·agent_task_queue·prod secret 검증 그대로 활용.
    고유 필드 없음 (보고서 발행은 DB enqueue 로 완결, broker 미경유).
    """


# Settings 인스턴스는 컴포넌트별 sub-module에서 단일 진실로 생성 (Composition Root 패턴, CLAUDE.md #F4).
# - web 컴포넌트: src/assessment_engine/web/settings.py
# - consumer 컴포넌트: src/assessment_engine/consumer/settings.py
# - db layer(session·redis)는 모든 컴포넌트 공통 — 자체 WebSettings 인스턴스화로 circular import 회피.
#
# multi-node 분리 배포 시 web 노드는 ConsumerSettings 인스턴스화 안 함 →
# 해당 컴포넌트 한정 키 검증 skip — 최소 권한 원칙 정합.
