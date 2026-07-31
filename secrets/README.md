# secrets/ — prod file-secret 채널 (ADR 0046)

`docker-compose.prod.yml` overlay 가 본 디렉토리의 파일을 컨테이너 `/run/secrets/*` 로 마운트한다.
prod 에서 비밀번호를 env 가 아닌 파일로 주입하기 위한 채널 (env 노출 회피).

## 배치

배포 VM 에서는 `bootstrap.sh` 가 만든다. `docker-compose.prod.yml` 의 `secrets:` 항목을 읽어 없는
파일만 강 random 으로 채우고 권한을 맞추므로, 다시 돌려도 기존 값은 보존된다.

직접 만들 때(소스 트리 수동 기동 등)는 이렇게 한다. 파일명은 overlay 의 secret 이름과 정확히 일치해야
한다 — app 의 pydantic `secrets_dir=/run/secrets` 가 필드명으로 자동 매핑하기 때문이다.

```bash
printf '%s' "$(openssl rand -base64 32)" > secrets/<항목명>
chmod 644 secrets/*
```

trailing newline 이 붙으면 비밀번호에 그대로 섞이므로 `printf` 를 쓴다(`echo` 는 개행을 붙인다).

권한은 644(world-readable) — 600으로 두면 postgres 공식 이미지가 non-root `postgres` 유저로 전환한
뒤 `POSTGRES_PASSWORD_FILE` 을 읽다가 Permission denied 로 기동 실패한다(Docker Compose file-secret은
Swarm과 달리 호스트 파일 권한을 컨테이너 안에 그대로 반영). 보안 경계는 호스트 계정 격리(secrets/
디렉토리 자체가 0700 root 소유)로 충분 — 파일 자체를 world-readable 로 둬도 root 외 호스트 계정은
디렉토리 진입 자체가 막힌다.

## 기동

`.env.example`(= prod `.env`)에 이미 `COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml` 이
박혀 있어 base+secrets 가 자동 머지된다 — 위 secret 파일만 두면 한 줄로 기동:

```bash
docker compose up -d
```

`.env.example` 에는 평문 password 가 없다(file-channel 단일). OS 환경변수에도 `*_PASSWORD` 를 두지
않는다 — 남아 있으면 컨테이너에 평문 비번이 주입돼 env 노출 회피가 무의미해진다(postgres 는
`POSTGRES_PASSWORD` 와 `_FILE` 동시 set 시 에러).

## 금지

이 디렉토리의 실제 secret 파일은 절대 git 에 commit 하지 않는다 (`.gitignore` 가 `secrets/*` ignore,
`.gitkeep`·`README.md` 만 추적). 호스트 디스크 평문은 `secrets/` 디렉토리 권한 0700(root 소유)·디스크
암호화로 보호한다(파일 자체는 컨테이너 non-root 유저 호환을 위해 644).
