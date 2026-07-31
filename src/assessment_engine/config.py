import os
from typing import Literal
from urllib.parse import quote

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 외부 인프라가 secret을 어떻게 주입하든(systemd EnvironmentFile·Vault·k8s Secret·Docker secrets 등)
# pydantic-settings는 env 우선·secrets_dir fallback 둘 다 지원. secrets_dir은 디렉토리가 존재할 때만 활성.
# 본 repo는 secret 채널 자체를 강제하지 않음(CLAUDE.md #A0) — 결과만 검증한다.
_SECRETS_DIR = os.environ.get("SECRETS_DIR", "/run/secrets")
_SECRETS_DIR = _SECRETS_DIR if os.path.isdir(_SECRETS_DIR) else None

# 거부할 뻔한 값 (USER·PASSWORD 공용). 미설정·빈값은 필드 제약(min_length)이 먼저 잡는다.
# "assessment"(dev 카탈로그 값)는 허용 — 뻔한 값만 차단한다.
_WEAK_VALUES = frozenset({"password", "admin", "root", "changeme"})


def _reject_env_shadowing_secret(field: str) -> None:
    """secret 파일과 같은 이름의 환경변수가 함께 있으면 거부한다.

    우선순위상 환경변수가 이겨서 파일 채널이 조용히 무력화된다 — 노출 회피를 의도했는데 값이
    컨테이너 env 에 그대로 뜬다. 실패도 경고도 없어 운영자가 알아채지 못한다.

    컨테이너는 compose `env_file` 이 값을 환경변수로 주입하므로 이 검사에 걸린다. 호스트에서
    pydantic 이 `.env` 를 직접 읽는 경로는 환경변수를 거치지 않아 여기서 잡히지 않는다.
    secret 디렉토리가 없으면(dev) 충돌 자체가 성립하지 않아 그대로 통과한다.
    """
    if _SECRETS_DIR is None or field.upper() not in os.environ:
        return
    if os.path.isfile(os.path.join(_SECRETS_DIR, field)):
        raise ValueError(
            f"{field.upper()} is set in the environment while {_SECRETS_DIR}/{field} exists. "
            "The environment value takes precedence, so the secret file is ignored and the value "
            "is exposed in the container env. Remove it from .env (and OS env) to use the file channel."
        )


class WebSettings(BaseSettings):
    # 우선순위: OS env > .env (cwd) > <SECRETS_DIR>/<field> 파일 > 코드 default
    # SECRETS_DIR env로 주입 경로 override 가능 (default `/run/secrets`).
    model_config = SettingsConfigDict(
        env_file=".env",
        secrets_dir=_SECRETS_DIR,
        extra="ignore",
    )

    # prod 일 때 model_validator 가 약한 default 거부.
    app_env: Literal["dev", "staging", "prod"] = "dev"

    # dev=text(colorized·grep 친화), prod=json(외부 log aggregator indexing).
    log_format: Literal["text", "json"] = "text"

    postgres_host: str = "postgres"
    postgres_db: str = "assessment"
    postgres_user: str = "assessment"
    # 기본값을 두지 않는다 — 미설정이 조용히 통과하면 그것을 거르는 검사가 또 필요해진다.
    postgres_password: SecretStr = Field(min_length=1)
    postgres_port: int = 5432
    web_port: int = 8000
    # uvicorn auto-reload — dev hot-reload 전용, prod False. 루트 docker-compose.yml 이 WEB_RELOAD 주입.
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
    redis_ttl_cache_metrics: int = 60  # 1min — 최신 대시보드 스냅샷 cache-aside (실시간 폴링 주기)
    redis_ttl_cache_detail: int = 300  # 5min — 서버 상세 ViewModel cache-aside

    # Key prefixes
    redis_key_cache_inventory: str = "cache:inventory:{}"
    redis_key_cache_metrics: str = "cache:metrics:{}"
    redis_key_cache_resolve: str = "cache:resolve:{}"
    redis_key_idempotent: str = "idempotent:{}"
    redis_key_online: str = "online:{}"
    redis_key_token: str = "token:{}"
    redis_key_last_agent_start: str = "last_agent_start:{}"
    redis_key_agent_restarts: str = "agent_restarts:{}"
    # {agent_id} 쿨다운 마커 — agent_id 가 식별 단일 키 (#C1)
    redis_key_time_invariant_warned: str = "time_invariant_warned:{}"

    # 에이전트 재시작 alert 임계값 (1h 슬라이딩 윈도우 내 횟수). consumer 부가 시그널 + web 신호 카드 공통.
    # 운영 alert 튜닝 노브 — env 카탈로그 미수록(env.example·env.md), 필요 시 env override.
    agent_restart_alert_threshold: int = 3

    # ZDM 서버 기본 좌표 — install 모달 default (POST body 누락 시 fallback, 운영자 override 가능).
    # 잘못된 발행 방어는 런타임(resolver 503 차단) + agent host whitelist — startup 거부 없음.
    zdm_default_ip: str = ""
    zdm_default_user: str = "admin@zconverter.com"

    # ZDM 패키지 contract — task.install download 필드에 박혀 agent 가 fetch.
    # sha256·size_bytes 는 publish 직전 ETag 기반 동적 산출 (cache invalidation = ETag 변경).
    zdm_package_path: str = "/download/ZConverter_CloudSource_Setup_Linux.tar.gz"
    zdm_package_script: str = "zconverter_install_source/install.sh"
    # Windows install (ADR 0019 install.type=direct_exec). single binary 라 script 없음.
    zdm_package_path_windows: str = "/download/ZConverter_CloudSource_Setup_Windows.exe"
    zdm_meta_connect_timeout_sec: float = 5.0
    zdm_meta_total_timeout_sec: float = 120.0
    redis_key_zdm_package_sha256: str = "cache:zdm_package:sha256:{}:{}"  # {host}:{etag}
    redis_ttl_zdm_package_sha256: int = 6 * 60 * 60  # 6h
    install_timeout_sec: int = 600  # install.sh wall-clock timeout (원격 host worker 강제 종료)

    # install task 배달/마감 창 — 두 타임아웃을 하나로 정합(F6 관측성).
    # 이 값 하나가 (1) engine 측 task deadline_at (2) broker agent.tasks.{agent_id} 큐 x-message-ttl 를 동시에 정한다.
    # 오프라인 호스트 store-and-forward 유예 = 이 창. 창 안에 재접속하면 큐에서 소비·실행·회신, 넘기면 큐 메시지 만료 +
    # reaper 가 pending -> failure(timeout). install_timeout_sec(600) 는 별개 개념 — agent 가 "픽업 후" 스크립트 실행에
    # 쓰는 wall-clock 예산(payload install.timeout_sec). 기본 3600 = 기존 큐 TTL(1h) 과 동일 -> 기존 큐 재선언 충돌 없음.
    install_task_deadline_sec: int = 3600

    @property
    def database_url(self) -> str:
        # user/password 는 URL-safe 인코딩 필수 — secret 은 `openssl rand -base64` 같은 생성 방식이면
        # `/`·`+`·`=` 를 포함할 수 있고, quote 없이 f-string 삽입 시 netloc 구분자(`:`·`@`)와 충돌해 URL
        # 파싱이 깨진다(safe="" 로 이 문자들도 인코딩 대상에 포함).
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password.get_secret_value(), safe="")
        return f"postgresql+asyncpg://{user}:{password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}"

    @model_validator(mode="after")
    def _validate_web_secrets(self) -> "WebSettings":
        # 환경으로 강도를 가르지 않는다 — dev 카탈로그도 뻔한 값을 쓰지 않으므로 같은 기준이 통한다.
        # 채널 자체는 본 repo 책임 밖(CLAUDE.md #A0). 결과만 본다.
        _reject_env_shadowing_secret("postgres_password")
        if self.postgres_password.get_secret_value() in _WEAK_VALUES:
            raise ValueError(
                "POSTGRES_PASSWORD uses an obvious value. "
                "Provide via env var or secret channel (systemd EnvironmentFile·Vault·k8s Secret 등)."
            )
        if self.postgres_user in _WEAK_VALUES:
            raise ValueError("POSTGRES_USER must not be an obvious value (password/admin/root/changeme).")
        return self


class WorkerSettings(WebSettings):
    """전용 백그라운드 워커 프로세스 설정 — 비동기 보고서 생성 + install task reaper.

    web 이 HTTP 만 담당하도록 분리한 별도 컨테이너(assessment_engine.worker). DB layer(WebSettings) 상속,
    broker 는 미사용(보고서·reaper 는 DB job-claim 만).
    """

    # 보고서 생성 루프. poll: pending job 점검 주기. stale_seconds: running 잔류 job 회수 임계(생성이 이 안에
    # 끝난다는 가정, 초과 = 크래시로 간주해 재집음). shutdown_timeout: graceful 시 진행 중 1건 완료 대기(초과 시
    # cancel -> running 잔류 -> 다음 기동 recover_stale 회수, in-flight 손실 0).
    report_worker_poll_interval_sec: float = 2.0
    report_worker_stale_seconds: int = 600
    report_worker_shutdown_timeout_sec: float = 10.0

    # install task reaper — deadline 지난 pending 을 다음 emit 없이 능동 timeout 전이.
    # interval: 점검 주기. shutdown_timeout: graceful 시 진행 중 1회 완료 대기(UPDATE 1건이라 짧게).
    install_reaper_interval_sec: float = 60.0
    install_reaper_shutdown_timeout_sec: float = 5.0


class ConsumerSettings(WebSettings):
    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672
    rabbitmq_vhost: str = "assessment"  # 에이전트가 발행하는 전용 vhost (무슬래시 — 앞 슬래시 없는 이름)
    rabbitmq_user: str = "assessment"
    # default 는 weak(changeme) — 미설정 시 prod 거부 강제 (명시 assessment 는 허용). USER 는 식별자라 default 허용.
    rabbitmq_password: SecretStr = Field(min_length=1)
    rabbitmq_exchange: str = "assessment"
    rabbitmq_routing_key_inventory: str = "server.inventory"
    rabbitmq_routing_key_metrics: str = "server.metrics"
    rabbitmq_routing_key_error: str = "server.error"

    # 원격 작업 토폴로지 (collector exchange와 분리 — 인증·DLX 정책 독립).
    # task.install: engine 발행
    #   routing_key=task.install.<agent_id> / queue=agent.tasks.<agent_id> (engine 동적 declare)
    # task.result : 원격 호스트 발행 / queue=worker.result
    rabbitmq_task_exchange: str = "assessment.tasks"
    rabbitmq_task_queue_prefix: str = "agent.tasks"
    rabbitmq_task_install_key_prefix: str = "task.install"
    rabbitmq_routing_key_task_result: str = "task.result"
    rabbitmq_queue_worker_result: str = "worker.result"

    # task.result 성공 보정 정책 (assessment_engine.task_policy). 매칭 키 -> 성공으로 취급할 추가 exit code 목록.
    # status=failure + failure_reason=script_failed + 매칭 키 일치 + exit_code 포함일 때만 success 로 보정.
    # 키 규약 (os_family 로 분기, task_policy.effective_task_result):
    #   - Windows: os_version = CurrentBuildNumber (예 "20348"). 메시지에서 발행.
    #   - Linux:   "os_id:major" (예 "rocky:9"). task.result 가 os 미발행이라 엔진이 inventory 에서 조회.
    # 기본값:
    #   - Windows(family-level "windows" 키): ZConverter installer 가 설치 성공임에도 exit 2 로 종료(전
    #     세대 공통 동작). 빌드번호별 키를 일일이 유지하는 건 취약(예 2008R2=7601 누락)하므로 family 한 키로
    #     일괄. 설치 성공 검증 = 해당 호스트 services 에 ZConCloudAgent(RUNNING) 등장으로 확인됨.
    #     (특정 빌드만 다르게 두려면 CurrentBuildNumber 키를 추가 — effective_task_result 가 빌드 키를 우선 매칭.)
    #   - rocky/almalinux/ol/centos major 9(EL9): installer 가 새 systemd start-limit 로 exit 3 을 내나
    #     설치·ZDM 등록은 성공 (rhel9 는 미해당이라 제외, centos-stream8 은 centos8 과 os_id 구분 불가라 보류).
    # env(JSON)로 override — 예: '{"windows":[2],"rocky:9":[3]}'.
    task_install_success_exit_codes: dict[str, list[int]] = {
        "windows": [2],
        "rocky:9": [3],
        "almalinux:9": [3],
        "ol:9": [3],
        "centos:9": [3],
    }

    @property
    def broker_url(self) -> str:
        # user/password 는 URL-safe 인코딩 필수 — secret 이 `openssl rand -base64` 생성값이면 `/`·`+`·`=`
        # 를 포함할 수 있고, quote 없이 삽입 시 yarl(aio-pika 내부 파서)이 netloc 을 못 갈라 "port can't be
        # converted to integer" 로 기동 크래시(운영 실사고). safe="" 로 이 문자들도 인코딩 대상에 포함.
        # vhost 는 별도로 "/" -> "%2F"(AMQP 표준 인코딩, quote 의 "/" 처리와 동일 결과) 유지.
        user = quote(self.rabbitmq_user, safe="")
        password = quote(self.rabbitmq_password.get_secret_value(), safe="")
        encoded_vhost = self.rabbitmq_vhost.replace("/", "%2F")
        return f"amqp://{user}:{password}@{self.rabbitmq_host}:{self.rabbitmq_port}/{encoded_vhost}"

    # 호스트별 task 큐·routing key 합성 단일 진실 — prefix(config) + agent_id(런타임).
    # agent 와 합의된 형식이라 양쪽이 동일 규칙으로 합성해야 함 (#B). 발행 측·소비 측 모두 본 메서드 경유.
    def agent_task_queue(self, agent_id: str) -> str:
        """task.install 발행 대상 호스트별 큐 이름 — `{prefix}.{agent_id}`."""
        return f"{self.rabbitmq_task_queue_prefix}.{agent_id}"

    def task_install_routing_key(self, agent_id: str) -> str:
        """task.install 호스트별 routing key — `{prefix}.{agent_id}`."""
        return f"{self.rabbitmq_task_install_key_prefix}.{agent_id}"

    @model_validator(mode="after")
    def _validate_consumer_secrets(self) -> "ConsumerSettings":
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
