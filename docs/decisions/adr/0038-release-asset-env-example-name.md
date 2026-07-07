# ADR 0038 — release 에셋명 env.example (점 prefix 제거, GitHub leading-dot 회피)

상태: Accepted (2026-06-08)

## Context

ADR 0035 는 배포 에셋명을 `.env.example`(점 prefix)로 결정했으나, GitHub Release 는 점으로 시작하는 파일명을 에셋 등록 시 `default.env.example` 로 변환한다 — asset name·browser_download_url 모두 `default.env.example` 이고 `.env.example` 패턴으로는 다운로드되지 않는다(웹 UI 표시만 `.env.example` 로 보일 수 있음).

v0.4.0 release 에서 `.env.example` 첨부가 `default.env.example` 로 등재돼, 운영 문서(`cp .env.example .env`)·인프라 파이프라인 인바운드 요청과 어긋났다(인프라 측이 `env.example` 통일을 요청).

## Decision

release 에셋명·루트 배포 템플릿 파일명을 점 없는 `env.example` 로 한다.

- 루트 파일 `.env.example` -> `env.example` rename. dev compose `env_file`·`cp env.example .env` 흐름은 그대로(파일명만 변경).
- `release.yml` 이 `env.example` 를 그대로 첨부 — GitHub 변환 없이 `env.example` 로 등재. SHA256SUMS·files 도 `env.example`.
- dev 전용 `dev/.env.example`·`agent.env.example` 는 release 에 안 올라가 leading-dot 문제가 없으므로 점을 유지(불필요한 변경 회피). dev 흐름은 `dev-up.sh` 가 처리.

## Consequences

- release 에셋명이 `env.example` 로 안정 — 운영 문서·인프라 파이프라인과 일치, `gh release download` 결과가 문서의 `cp env.example .env` 와 동일.
- 동기화 범위: 루트 파일 rename + `release.yml`·`README.md`·`docs/operations/*`·`docs/development/*`·`.dockerignore`·`PULL_REQUEST_TEMPLATE`·`config.py` 주석·`docker-compose.yml` 주석·`dev/local-ci.sh` 의 루트 `.env.example` 참조를 `env.example` 로. dev/agent 참조는 불변.
- ADR(0035·0036 등) 본문의 `.env.example` 은 당시 명명 기록으로 보존(불변 규약), 정정 note 로 현행 `env.example` 명시.

## 관계

- ADR 0035 "에셋명 `.env.example` 점 prefix"(5절) supersede — base/override·release 첨부 결정 본체는 0035 유지, 에셋명만 정정.
- ADR 0036 루트 배포 템플릿 파일명을 `env.example` 로 갱신(정정 note).
