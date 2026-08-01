# ADR 0059 — compose 3파일을 표준 배치(base 공통 + override dev + prod)로 정렬

상태: Accepted (2026-07-31)

Refines: ADR 0035 (compose base/override 분리), ADR 0046 (file-secret overlay)

## Context

ADR 0035 는 루트 `docker-compose.yml` 을 prod-safe base 로 두고 dev 편의를 override 로 얹었다. 근거는 릴리즈가 그 base 파일을 에셋으로 첨부하므로 배포 대상이 파일 하나만 받으면 되게 하려는 것이었다.

그 근거가 소멸했다. ADR 0048 이 에셋 첨부를 폐기했고, `deploy.sh` 는 배포 버전 태그에서 base 와 overlay 두 파일을 함께 fetch 한다. "파일 하나만" 이 성립하지 않는다.

남은 것은 base 가 prod 색을 띤다는 사실뿐이다. `APP_ENV` 기본값이 prod 이고, 비밀번호가 `${*_PASSWORD:-changeme}` 로 base 에 박혀 있어 dev override 가 그것을 덮는 구조였다. Compose 생태계의 표준 배치(base = 공통, override = dev, prod = 운영)와 어긋난다.

## Decision

표준 배치로 정렬한다.

- `docker-compose.yml` — 공통 정의만. 환경 색을 담지 않는다. 비밀번호 설정이 없고 `APP_ENV` 기본값도 두지 않는다(`env_file` 이 준다).
- `docker-compose.override.yml` — dev. 로컬 빌드·bind mount·핫리로드에 더해, base 에서 빠진 비밀번호 몫을 env 채널로 채운다. 앱 컨테이너는 `env_file` 로 값을 받고 rabbitmq 만 키 이름이 달라 매핑한다.
- `docker-compose.secrets.yml` -> `docker-compose.prod.yml` 로 개명. 하는 일은 그대로 file-secret 채널이다. base 에 비밀번호 설정이 없어졌으므로 기존의 env null 중화 항목을 제거했다.

파일 3개 구성 자체는 유지한다. 서비스 레벨 `secrets:` 가 append 병합이라 override 가 비울 수 없고(ADR 0046), base 에 secret 을 넣으면 dev 도 secret 파일을 요구하게 된다. 2 파일로 줄이는 것은 compose merge 규칙이 막는다.

## Consequences

base 를 읽는 사람이 환경별 설정을 그 파일에서 찾지 않게 된다. 어느 값이 dev 인지 prod 인지 헷갈리던 `${APP_ENV:-prod}` 류가 사라졌다.

base 단독 기동은 더 이상 prod 로 성립하지 않는다. 비밀번호 설정이 없어 앱은 `env_file` 값에 의존하고, postgres·rabbitmq 는 채널 파일이 있어야 한다. `deploy.sh` 는 두 파일을 함께 받으므로 영향이 없고, 수동 기동은 `-f` 두 개 또는 `.env` 의 `COMPOSE_FILE` 을 쓴다.

`COMPOSE_FILE` 값과 `deploy.sh`·`bootstrap.sh` 가 받는 파일명이 바뀐다. 아직 릴리즈 전이라 배포된 VM 이 없다.

## 관계

- ADR 0035 의 "base = prod 진입점" 을 refine — 에셋 첨부 폐기(ADR 0048)로 근거가 사라졌다. base·override 2 파일 분리 자체는 존속.
- ADR 0046 refine — overlay 파일명이 `docker-compose.prod.yml` 로 바뀐다. file-secret 채널 결정과 3 파일이 필요한 근거(secrets append 병합)는 그대로다.
