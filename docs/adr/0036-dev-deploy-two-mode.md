# ADR 0036 — 퀵스타트 카테고리 폐기, dev/배포 2분류 + 루트 .env.example 배포 템플릿화

상태: Accepted (2026-06-08)

## Context

ADR 0033(dev+퀵스타트 단일 compose)·ADR 0035(base prod / override dev)를 거치며 "퀵스타트"가 환경도 배포 채널도 아닌 모호한 제3 카테고리로 남아 dev / prod / 퀵스타트 3원 구조가 됐다. 환경 모델의 정석은 dev / prod 다 (12-factor — 환경은 deploy 단위). "퀵스타트"는 환경이 아니라 "빠르게 띄우는 방법"이었다.

부수 문제: 루트 `.env.example` 이 "퀵스타트(dev-grade) + 배포" 겸용이라 weak secret(`POSTGRES_PASSWORD=assessment`)이 릴리즈 배포물(release.yml 첨부)에 실려 나갔다 — 배포 받아 그대로 기동하면 weak secret 운영 위험.

## Decision

환경 모델을 dev / 배포(prod) 2분류로 고정한다. "퀵스타트" 카테고리를 폐기한다.

- dev — `dev/` 디렉토리: `dev/dev-up.sh`(libvirt VM 파이프라인) + `dev/.env.example`. 검증 전용.
- 배포(prod) — 릴리즈 base `docker-compose.yml`(또는 wheel+systemd·k8s) + 루트 `.env.example`(배포 템플릿).
- "평가·데모·단일 호스트 소규모"는 별도 환경이 아니라 prod 의 단일 호스트 토폴로지 — 배포로 흡수(secret 채워 prod 로 기동).

루트 `.env.example` = 배포 템플릿:
- `APP_ENV=prod`, `LOG_FORMAT=json`.
- secret(`POSTGRES_USER/PASSWORD`·`RABBITMQ_USER/PASSWORD`)은 placeholder `changeme` — `_WEAK_VALUES` 라 미교체 시 prod fail-fast 거부(`_validate_prod_*`). 운영자가 진짜 secret 입력 강제.
- 배포 키(`ENGINE_IMAGE`·`PGDATA_HOST`·`MQ_DATA_HOST`) 중심, dev 편의 값(RAG·OLLAMA active 등) 제거.
- dev 검증 카탈로그는 `dev/.env.example` 단일.

compose 구조(base/override, ADR 0035)는 불변. "소스 clone 후 `docker compose up`"은 여전히 dev 로 동작(override 자동 머지) — 기능 손실 0, 카테고리·명명·.env 초점만 정리. dev 로 띄우려면 `dev/.env.example` 경유(dev-up.sh 가 처리), 배포는 루트 `.env.example` 채워서.

## Consequences

- 환경 모델이 dev / prod 정석 2분류. 루트 `.env.example` 이 배포에 정직(weak secret 미배포, 미교체 시 fail-fast).
- 동작 변화: `cp .env.example .env && docker compose up` 이 더는 바로 안 뜬다 — secret 채우고 prod 로 기동(의도된 fail-fast). dev 즉시 기동은 dev-up.sh.
- 트레이드오프: "소스 clone 한 줄 평가" 진입장벽 0 가치 상실. 평가는 dev 로 띄우거나 prod 배포(secret 채움)로 한다.

## 관계

- ADR 0033(dev+퀵스타트)·0035(base 퀵스타트 겸용)의 "퀵스타트" 개념을 폐기 — 정정. compose base/override 구조(0035)는 존속, 의미만 "prod base / dev override"로 명확.
- #F9 동시 갱신: 루트 `.env.example`·`dev/.env.example` 헤더·`README.md`·`docs/operations/deployment.md`·`docs/development/docker.md`·`docs/README.md`·CLAUDE.md #A0·`docker-compose.yml`·`dev/dev-up.sh` 주석의 퀵스타트 용어.
