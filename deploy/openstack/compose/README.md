# Compose — 분산 docker-compose 3종

ADR 0006 분산 토폴로지(DB·MW·앱 분리)에 맞춰 각 VM이 자기 역할만 docker compose로 실행.

| 파일 | 배포 위치 | 서비스 |
|---|---|---|
| `docker-compose.db.yml` | `engine-db` VM | postgres |
| `docker-compose.middleware.yml` | `engine-mw` VM | rabbitmq + redis |
| `docker-compose.app.yml` | `engine-app` VM | migrate + web + consumer + diagnostic-worker + diagnostic-scheduler |

루트의 `docker-compose.yml`(로컬 dev 단일 VM)과는 별도 — 같은 컨테이너 이미지·같은 코드 활용, 차이는 토폴로지·환경변수만.

## 환경변수 매트릭스 (앱 VM 기준)

| 변수 | 값 (분산) | 비고 |
|---|---|---|
| `POSTGRES_HOST` | `engine-db` 사설 IP | Ansible playbook이 inventory에서 채워 주입 |
| `RABBITMQ_HOST` | `engine-mw` 사설 IP | 동일 |
| `REDIS_HOST` | `engine-mw` 사설 IP | 동일 |
| `POSTGRES_PASSWORD` | Ansible Vault 복호화 | `/run/secrets/postgres_password` 마운트 |
| `RABBITMQ_PASSWORD` | Ansible Vault 복호화 | 동일 |
| `APP_ENV` | `prod` | model_validator 약한 default 거부 활성 |
| `LLM_PROVIDER` | `mock` | 운영자 정책 — 과금 외부 API 호출 금지 |

## healthcheck 한계

`depends_on: service_healthy`는 같은 docker compose 안에서만 동작. 분산 환경에서는:
- migrate 컨테이너가 외부 DB connect → DB healthy 보장은 Ansible playbook 순서로
- 앱 컨테이너는 자체 재연결 로직 (aio-pika `connect_robust`·asyncpg `pool_pre_ping`·Redis fail-open) 으로 대응

## 사용

각 VM에서:
```bash
docker compose -f docker-compose.<role>.yml up -d
```

`role` = `db` / `middleware` / `app`. Ansible playbook이 자동으로 본 명령 실행.
