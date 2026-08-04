# ADR 0046 — prod 비밀번호 file-secret 채널 (compose overlay)

상태: Accepted (2026-06-26) — Refined by ADR 0047·0059. file-secret 채널 단일 결정은 존속하고 overlay 파일명이 `docker-compose.prod.yml` 로 바뀌었다(0059).

Refines: ADR 0035 (compose base/override), ADR 0036 (dev/배포 2분류)

## Context

단일 호스트 non-swarm docker compose 배포에서 prod 비밀번호를 가장 정석적으로 관리한다.
현행(ADR 0035)은 secret 을 `environment:` 에 `${*_PASSWORD:-changeme}` 보간으로 주입한다
(env-channel) — 단순하나 secret 이 컨테이너 env(`docker inspect`·`compose config`·`/proc/PID/environ`)에
평문으로 노출된다. 이를 회피하려면 비번을 파일에서 `/run/secrets/*` 로 주입하는 file-channel 이 필요하다.

엔진 코드는 이미 file-channel 을 받을 준비가 돼 있다 — pydantic-settings 가 `OS env > .env >
secrets_dir(/run/secrets) > default` 순으로 읽고, `_SECRETS_DIR` 은 디렉토리가 존재할 때만 활성
(`config.py`). 따라서 코드 변경 0 이고 compose 와이어링만 남는다.

제약 (탐색·공식 문서 확인):
- compose merge 에서 서비스 레벨 `secrets:` 는 append(target 기준 merge) — override 가 비울 수 없다.
  그래서 "base 에 secrets 정의 + dev override 가 무력화" 는 불가능. `!reset`/`!override` YAML 태그는
  compose 2.24+ 버전 게이트 + YAML 앵커 결합 시 무시되는 버그(docker/compose #11706)가 있어, 앵커를
  다용하는 본 repo compose 에선 보안 민감 기능을 의존할 수 없다.
- rabbitmq:3.13 은 `RABBITMQ_DEFAULT_PASS_FILE`/`_USER_FILE` 을 제거(deprecated 후 삭제)했다.
  postgres(`POSTGRES_PASSWORD_FILE`)·pgadmin(`PGADMIN_DEFAULT_PASSWORD_FILE`)은 `_FILE` 을 지원.

## Decision

prod 전용 overlay `docker-compose.secrets.yml` 을 신설하고, file-secret 와이어링을 이 파일에만 담는다.

- base `docker-compose.yml` 은 password 보간 유지 — base 단독 = env-channel prod(현행). overlay 를
  함께 로드하면 = file-channel prod. 둘 다 #A0 "secret 채널 비강제" 와 정합(채널 옵션).
- dev `docker-compose.override.yml` 은 overlay 를 로드하지 않으므로(명시 `-f` 필요) 영향 0 —
  `cp env.dev.example .env && docker compose up` 그대로. dev 는 secret 파일 생성 불필요.
- overlay 구성:
  - top-level `secrets:` (file source) — `./secrets/{postgres_password,rabbitmq_password,pgadmin_password}`.
    파일명 = pydantic 필드명(`postgres_password`·`rabbitmq_password`) 정확 일치(secrets_dir 자동 매핑).
  - 서비스별 secret 마운트 + password env 를 null 로 중화(base 의 `${*_PASSWORD:-changeme}` 보간 제거).
    app(web/consumer/migrate)은 `secrets_dir` 로, postgres·pgadmin 은 `*_FILE` env 로 읽는다.
  - rabbitmq 는 `_FILE` 미지원이라 entrypoint wrapper(`export RABBITMQ_DEFAULT_PASS="$(cat /run/secrets/...)"`)로 우회.
- 기동: `docker compose -f docker-compose.yml -f docker-compose.secrets.yml up -d` 또는
  `COMPOSE_FILE=docker-compose.yml:docker-compose.secrets.yml docker compose up -d`.
- file-channel 사용 시 `.env`(및 OS env)에서 password 를 제거한다 — `env_file: .env` 가 모든 컨테이너에
  주입하므로 남아 있으면 env 노출 회피가 무의미해지고, postgres 는 `PASSWORD`+`_FILE` 동시 set 시 에러.

## Consequences

- prod 비번이 컨테이너 env(`docker inspect` 정적 Config.Env)에 안 뜬다 — secret 은 `/run/secrets/*` 파일로만.
  단일 호스트 non-swarm 에선 호스트 디스크 평문은 `.env` 와 동일하게 남으므로, 파일 권한 600·디스크 암호화로
  보호한다(file-channel 의 이득은 env 노출 회피이지 디스크 평문 제거가 아님 — swarm 의 tmpfs 와 다름).
- dev 는 overlay 미로드라 변경 0. 사용자 목표 "dev 는 복사만" 충족.
- 코드(`config.py`) 변경 0. `_validate_prod_*` 는 secrets_dir 로 읽은 SecretStr 값도 그대로 weak default 검증.
- 한계: rabbitmq wrapper 는 비번을 컨테이너 내부 프로세스 env(`/proc/PID/environ`, 컨테이너 root 한정)에
  잠시 노출한다(docker inspect 의 정적 Config.Env 엔 미노출). 완전 격리는 rabbitmq.conf hashed
  `load_definitions` 가 필요하나 과복잡이라 미채택 — 단일 호스트 내부망 가정에서 수용.
- overlay 는 릴리즈 에셋으로 첨부(self-contained prod). image 핀 없어(서비스명만 참조) `__ENGINE_VERSION__`
  치환 불요. SHA256SUMS 포함.

## 관계

- ADR 0035 "prod overlay 안 둠" 원칙을 refine — secret 채널 옵션 overlay 는 예외(편의 제공). prod 환경
  전체를 가르는 `docker-compose.prod.yml` 은 여전히 두지 않는다.
- ADR 0036 dev/prod 2분류·루트 env.example=prod 템플릿 결정 존속 — file-channel 은 prod 측 secret 주입 옵션.
- #A0 secret 채널 비강제와 정합 — overlay 는 여러 채널(env·systemd·Vault·file) 중 하나의 1급 구현.
- #F9 동시 갱신: `docker-compose.secrets.yml`(신규)·`.gitignore`·`secrets/{.gitkeep,README.md}`·
  `env.example`·`.dockerignore`·`release.yml`·`docs/operations/{env,deployment,release}.md`·`README.md`·
  CLAUDE.md #A0·ADR 0035 정정 note·ADR 인덱스.

## 정정 (2026-06-26)

초안의 "file-channel opt-in(base 단독=env-channel 존속)"을 file-channel 단일 통합으로 변경.
prod 비번 채널을 file-channel 하나로 통합한다 — `env.example` 에서 평문 password 제거 +
`COMPOSE_FILE=docker-compose.yml:docker-compose.secrets.yml` 추가로 prod 는 `docker compose up -d`
한 줄에 base+secrets 자동 머지. base 단독 env-channel prod 는 폐지(secret 부재 시 fail-fast,
base 단독은 APP_ENV=dev weak 빠른 테스트만). prod = base+secrets, dev = base+override 로 경로 2개가
깔끔히 분리. ADR 0035 "base 단독 pull-and-run" 정정 동반.
