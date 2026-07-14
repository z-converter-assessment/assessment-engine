# secrets/ — prod file-secret 채널 (ADR 0046)

`docker-compose.secrets.yml` overlay 가 본 디렉토리의 파일을 컨테이너 `/run/secrets/*` 로 마운트한다.
prod 에서 비밀번호를 env 가 아닌 파일로 주입하기 위한 채널 (env 노출 회피).

## 배치

파일명은 `docker-compose.secrets.yml` 의 secret 이름과 정확히 일치해야 한다 (app 의 pydantic
`secrets_dir=/run/secrets` 자동 매핑 조건이라 `postgres_password`·`rabbitmq_password` 는 필드명 그대로):

```
secrets/postgres_password    # PostgreSQL 비밀번호
secrets/rabbitmq_password    # RabbitMQ 비밀번호
```

생성 예 (강 random + 권한 644, trailing newline 없이):

```bash
printf '%s' "$(openssl rand -base64 32)" > secrets/postgres_password
printf '%s' "$(openssl rand -base64 32)" > secrets/rabbitmq_password
chmod 644 secrets/*
```

권한은 644(world-readable) — 600으로 두면 postgres 공식 이미지가 non-root `postgres` 유저로 전환한
뒤 `POSTGRES_PASSWORD_FILE` 을 읽다가 Permission denied 로 기동 실패한다(Docker Compose file-secret은
Swarm과 달리 호스트 파일 권한을 컨테이너 안에 그대로 반영). 보안 경계는 호스트 계정 격리(secrets/
디렉토리 자체가 0700 root 소유)로 충분 — 파일 자체를 world-readable 로 둬도 root 외 호스트 계정은
디렉토리 진입 자체가 막힌다.

## 기동

`env.example`(= prod `.env`)에 이미 `COMPOSE_FILE=docker-compose.yml:docker-compose.secrets.yml` 이
박혀 있어 base+secrets 가 자동 머지된다 — 위 secret 파일만 두면 한 줄로 기동:

```bash
docker compose up -d
```

`env.example` 에는 평문 password 가 없다(file-channel 단일). OS 환경변수에도 `*_PASSWORD` 를 두지
않는다 — 남아 있으면 컨테이너에 평문 비번이 주입돼 env 노출 회피가 무의미해진다(postgres 는
`POSTGRES_PASSWORD` 와 `_FILE` 동시 set 시 에러).

## 금지

이 디렉토리의 실제 secret 파일은 절대 git 에 commit 하지 않는다 (`.gitignore` 가 `secrets/*` ignore,
`.gitkeep`·`README.md` 만 추적). 호스트 디스크 평문은 `secrets/` 디렉토리 권한 0700(root 소유)·디스크
암호화로 보호한다(파일 자체는 컨테이너 non-root 유저 호환을 위해 644).
