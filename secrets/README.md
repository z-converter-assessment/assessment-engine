# secrets/

prod 기동 시 Docker secrets로 마운트되는 파일들의 호스트 경로.

## 정책

- **dev**: 본 디렉토리의 secret 파일은 **사용하지 않음**. dev는 `.env` 평문이 자격을 주입.
- **prod**: 아래 파일들이 반드시 존재해야 한다. `docker-compose.prod.yml`이 `secrets:` 블록으로 마운트.

## 필수 파일 (prod)

| 파일 | 마운트 경로 | 사용처 |
|------|-----------|------|
| `secrets/postgres_password` | `/run/secrets/postgres_password` | postgres `POSTGRES_PASSWORD_FILE`, web/consumer 컨테이너의 pydantic `secrets_dir` |
| `secrets/rabbitmq_password` | `/run/secrets/rabbitmq_password` | rabbitmq `RABBITMQ_DEFAULT_PASS_FILE`, consumer 컨테이너의 pydantic `secrets_dir` |

## 작성 방법

1. 강한 password 생성:
   ```bash
   openssl rand -base64 32 > secrets/postgres_password
   openssl rand -base64 32 > secrets/rabbitmq_password
   ```
2. 파일 권한 제한:
   ```bash
   chmod 0400 secrets/postgres_password secrets/rabbitmq_password
   ```
3. 파일 끝의 trailing newline 주의 — 일부 클라이언트가 newline을 password 일부로 해석할 수 있음.
   `printf` 사용 권장:
   ```bash
   printf '%s' "$(openssl rand -base64 32)" > secrets/postgres_password
   ```

## .gitignore

본 디렉토리의 모든 secret 파일은 git에 커밋하지 않는다 (`.gitignore`에 `secrets/*` 등록, README/예시만 예외).