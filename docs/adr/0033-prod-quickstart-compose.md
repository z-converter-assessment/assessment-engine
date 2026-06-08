# ADR 0033 — 루트 docker-compose 단일 파일 (dev + 퀵스타트), ADR 0012 5절 supersede

상태: Accepted (2026-06-01)

## Context

ADR 0012 5절은 `docker-compose.prod.yml`을 제거하고 "본 repo는 prod 운영 contract를 docker compose 형식으로 강제하지 않는다"로 결정했다 — prod 가정이 VM + Linux + systemd였고 그 모델에서 compose 가치가 작다고 봤다. 그에 따라 compose는 `dev/docker-compose.yml`(dev 전용)만 존재했다.

그러나 본 제품은 고객사 내부망 B2B 포털이고, "단일 호스트 all-in-one을 한 줄로 띄우는 퀵스타트"의 가치가 크다 — 평가(PoC)·신뢰된 내부망 소규모 운영·데모. 동시에 dev compose와 prod 퀵스타트 compose를 둘로 나눠 중복 관리(또는 override 계층)하는 것은 현 단계에 불필요한 복잡도다.

## Decision

루트에 `docker-compose.yml` 단일 파일을 둔다. dev 파이프라인과 퀵스타트가 같은 한 파일을 쓴다. `dev/docker-compose.yml`은 제거(중복 0, override 파일 0).

- 용도 두 가지, 파일 하나:
  - 퀵스타트: repo clone 후 `docker compose up -d`
  - dev: `dev/dev-up.sh`가 본 파일을 `COMPOSE_FILE`로 사용(+ libvirt VM 등 host 구성). `dev-down.sh`는 dev-up.sh를 source해 동일 `COMPOSE_FILE` 사용.
- 이미지·환경변수 단일화: compose는 루트 `Dockerfile`(wheel install·non-root 운영 이미지)로 build — CI/release·systemd·k8s 와 같은 이미지. source bind mount·hot reload 없음. 환경변수는 루트 `.env`(`cp .env.example .env`) 단일 관리(dev/.env·dev/Dockerfile 제거). `APP_ENV` 토글(기본 `dev`)로 dev 편의(ZDM mock 등) on/off.
- dev 코드 반복: bind mount 없음 -> `docker compose up --build` 또는 venv(host 실행, README "개발 환경 셋업"). docker = 스택 기동, venv = 코드 iteration 분리.
- ADR 0012 1-4절(wheel + GitHub Release artifact 정책)은 유지. compose는 그 위에 얹는 추가 경로이지 release artifact 대체가 아니다.

부수 수정 (본 작업 중 발견): wheel 기반 prod migrate 경로 버그. force-include가 migrations를 `assessment_engine/_migrations`로 동봉했으나 번들 `_alembic.ini`의 `script_location`은 `%(here)s/migrations`라 불일치 — 문서화된 `alembic -c _alembic.ini upgrade head`가 실제론 실패했다(CI smoke test가 import만 검증해 미검출, dev 시절 compose는 bind mount라 wheel 경로 미해당). force-include를 `assessment_engine/migrations`로 정정. 본 ADR에서 compose가 wheel 이미지로 전환돼 이제 wheel migrate를 그대로 탄다 — wheel install 후 migrate 실제 동작 검증 완료.

## 추후 분리 (deferred)

본 ADR은 "일단 단순하게"를 의식적으로 택했다. 다음은 후속 결정:
- dev / hardened-prod 분리: 현재 한 파일·`APP_ENV=dev` 기본(weak secret 허용·plain HTTP). hardened prod는 `APP_ENV=prod`(강 secret weak-default 거부 통과)·`LOG_FORMAT=json`·HTTPS ingress·외부 secret 채널. 분리 방식(별도 파일 vs override vs profile)은 그때 결정.
- 환경변수 분리: 현재 `dev/.env` 한 파일 공유(엔진 기준). dev/prod 키 분기·secret 채널 분리는 별도.

## Consequences

장점
- compose 정의가 한 곳 — 중복·override 계층 없음. dev와 퀵스타트가 같은 파일이라 drift 0.
- repo clone 후 `docker compose up` 한 줄로 평가·내부망 배포 진입 장벽 제거.
- wheel migrate 버그 정정으로 wheel+systemd 정석 경로(release artifact)의 migrate도 비로소 동작.

한계 / 트레이드오프
- 현 단계 compose는 `APP_ENV=dev` 기본(weak secret 허용·plain HTTP)이라 hardened prod 아님 — 인터넷 노출 운영은 위 "추후 분리" 선결.
- 퀵스타트는 `dev/.env` 필요(없으면 `cp dev/.env.example dev/.env`). install(ZDM)·AI 진단(LLM)은 외부 좌표 주입 전까지 비활성 — 수집·조회·보고서는 정상.

## supersede 관계

- ADR 0012 5절("docker-compose.prod 미제공 / prod를 compose로 강제 안 함") -> 본 ADR이 supersede. 루트 `docker-compose.yml`을 dev + 퀵스타트 단일 파일로 제공.
- ADR 0012 3절(force-include 경로)은 본 ADR에서 `_migrations` -> `migrations`로 정정 — ADR 0012 정정 note에 동반 기록.

정정 note (2026-06-08, ADR 0035): 본 ADR의 "단일 파일·override 파일 0·정의 1곳" 결정은 ADR 0035가 supersede. infra B안(GHCR 선빌드 이미지 pull-and-run)이 확정되며 본 ADR "추후 분리(dev/hardened-prod)"가 트리거됐고, 루트 `docker-compose.yml`을 prod-safe base(build 키 없는 이미지 pull)로 역할 전환 + dev 편의(build·bind mount·hot reload)를 `docker-compose.override.yml`로 분리했다. 단일 이미지(Dockerfile)·퀵스타트 진입 가치·`docker-compose.prod.yml` 미존재 원칙은 본 ADR 그대로 존속(base 자체가 prod). 상세는 ADR 0035.
