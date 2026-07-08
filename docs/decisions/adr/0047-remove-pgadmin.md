# ADR 0047 — pgAdmin 제거 (repo 에서 완전 삭제)

상태: Accepted (2026-07-01)

Refines: ADR 0046 (compose file-secret overlay), ADR 0045 (dev 런타임 경계)

## Context

pgAdmin4 를 servers.json 내장 GHCR wrapper 이미지로 compose 에 포함해 운영 DB 조회·관리 GUI 를
제공해 왔다 — base `docker-compose.yml` 의 `pgadmin` 서비스, dev override 로컬 빌드, prod file-secret
overlay 의 `PGADMIN_DEFAULT_PASSWORD_FILE`, 릴리즈의 `release-pgadmin-image` job(GHCR
`assessment-pgadmin` 별도 이미지), `docker/pgadmin/{Dockerfile,servers.json}`, env 의 `PGADMIN_*` 키.

배포 범위를 재정의하는 작업(compose 단일 매체 배포 + 엔진 rollout 을 본 repo 로 통합)에 앞서 릴리즈·운영
표면을 정리한다. pgAdmin 은 다음 이유로 그 정리의 선행 대상이다:

- 엔진 핵심 기능이 아닌 운영 편의 GUI 다. 엔진 2 컴포넌트(web·consumer) + 의존 인프라(DB·MQ·Redis)와
  달리 제거해도 수집·저장·분석·보고서 파이프라인에 영향이 없다.
- DB 웹 접근은 민감 표면이다. 내부망 단일 호스트라도 별도 인증·포트(5050)·컨테이너·볼륨을 상시 노출하며,
  idle 메모리(약 250 MiB)와 공격 표면을 더한다. DB 직접 조회는 필요 시 psql 또는 임시 컨테이너로 충분하다.
- 릴리즈 표면을 정석(서명·attestation 된 OCI 이미지 단일)으로 수렴시키려는 방향과 어긋난다.
  `assessment-pgadmin` 은 외부 베이스 wrapper 라 cosign/SBOM 없이 발행되던 별도 이미지 job 이었다.

## Decision

pgAdmin 을 repo 에서 완전히 제거한다 — 코드·설정·문서 잔재 0.

- 런타임/인프라 제거:
  - `docker/pgadmin/`(Dockerfile·servers.json) 디렉토리 삭제(디렉토리 자체 소멸).
  - `docker-compose.yml` — `pgadmin` 서비스·`pgadmin_data` 볼륨 제거. 서비스는 postgres·rabbitmq·
    redis·migrate·web·consumer 6 개.
  - `docker-compose.override.yml` — dev `pgadmin` 로컬 빌드 서비스 제거.
  - `docker-compose.secrets.yml` — `pgadmin_password` secret·`pgadmin` 서비스 마운트 제거.
  - `release.yml` — `release-pgadmin-image` job 삭제(GHCR `assessment-pgadmin` 발행 중단).
  - `env.example`·`env.dev.example` — `PGADMIN_EMAIL`·`PGADMIN_PASSWORD`·`PGADMIN_PORT` 및 관련 주석 제거.
  - `secrets/README.md` — `secrets/pgadmin_password` 배치·생성 항목 제거.
- 영구 문서 현황화(#F12 덮어쓰기): `README.md`·`docs/guides/local-dev.md`·`docs/guides/deploy.md`·
  `docs/reference/contracts/env.md` 의 pgAdmin 서술·`PGADMIN_*` 키·5050 포트 제거.
- 폐기 토큰 `rg` 0 검증: `pgadmin`·`assessment-pgadmin`·`pgadmin_data`·`servers.json`·`5050` 이
  코드·영구 문서에 0 건(docs/adr 불변 기록 제외).

## Consequences

- 배포 토폴로지가 엔진 + 의존 인프라로 좁아진다. compose 두 조합(prod=base+secrets, dev=base+override)
  파싱은 pgAdmin 없이 정상, 서비스 6 개.
- 릴리즈 이미지가 `assessment-engine` 단일로 수렴한다(pgAdmin wrapper 이미지 발행 중단) — 서명·SBOM
  없는 별도 이미지 job 제거로 릴리즈 표면이 정석에 근접.
- 운영자의 DB 웹 GUI 편의는 사라진다. DB 직접 조회는 psql 또는 필요 시 임시 `dpage/pgadmin4` 일회성
  컨테이너로 대체(상시 서비스 아님). 내부망 운영 가정에서 수용.
- prod file-secret 채널(ADR 0046)의 secret 은 `postgres_password`·`rabbitmq_password` 2 개로 축소.

## 관계

- ADR 0046 file-secret overlay 의 pgadmin `_FILE` 와이어링을 refine — overlay secret 은 postgres·
  rabbitmq 2 개로 축소. postgres `_FILE`·rabbitmq entrypoint wrapper 결정은 존속.
- ADR 0045 의 "pgAdmin `/pgpass` mount 제거(첫 연결 1회 입력)" 세부는 pgAdmin 자체 제거로 무의미해진다
  (ADR 0045 의 dev 런타임 경계 본체 결정은 존속). 기존 ADR 본문은 불변(스크럽 금지) — 본 ADR 이 현황을 담는다.
- ADR 0006(Withdrawn)의 jump host web/pgAdmin/Horizon 접속 언급은 역사 기록으로 존속.
- 배포 범위 재정의(compose 단일 매체 배포 + 엔진 rollout 본 repo 통합)의 선행 정리 — 후속 결정은 별도 ADR.
- #F9 동시 갱신: `docker/pgadmin/`(삭제)·`docker-compose.yml`·`docker-compose.override.yml`·
  `docker-compose.secrets.yml`·`release.yml`·`env.example`·`env.dev.example`·`secrets/README.md`·
  `README.md`·`docs/guides/local-dev.md`·`docs/operations/{deployment,env}.md`·ADR 인덱스.
