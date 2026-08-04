# ADR 0045 — dev 런타임 경계 확정: libvirt VM 파이프라인·ZDM mock 제거

상태: Accepted (2026-06-26) — Refined by ADR 0047 (pgAdmin 제거).

Supersedes: ADR 0037 (dev libvirt 재전환), ADR 0018 (dev ZDM mock endpoint)
Refines: ADR 0036 (dev/배포 2분류 — dev 메커니즘만 갱신, 분류 자체는 존속)

## Context

본 repo 는 "엔진을 런타임에 띄우는 것"까지만 담당한다 (#A0). 그동안 `dev/` 디렉토리가 그 경계를 넘어 libvirt(KVM) 기반 로컬 VM 시연 파이프라인을 안고 있었다 — Linux 5대 + Windows 1대 VM 프로비저닝(`dev-up.sh`/`dev-down.sh`), agent 크로스빌드(`agent-build/`), Windows autounattend 무인설치(`win/`), dev 한정 ZDM mock endpoint(ADR 0018).

agent 가 붙는 VM 은 앞으로 OpenStack 이 공급한다 (본 repo 범위 밖). 따라서 libvirt VM 프로비저닝·agent 빌드·Windows 무인설치·ZDM mock 은 모두 엔진 런타임 밖 자산이며, 본 repo 에서 제거해 경계를 코드·문서에 선언적으로(#F12) 확정한다.

dev 개발 런타임은 `docker-compose.override.yml` 핫리로드(`./src` bind mount + uvicorn/watchfiles reload)만으로 충분하다.

## Decision

1. `dev/` 디렉토리 제거. `dev/local-ci.sh` 는 엔진 코드 품질 검증(`.github/workflows` 로컬 선재현)이라 `scripts/local-ci.sh` 로 이동·존속한다.

2. dev = `docker-compose.override.yml` 핫리로드만. base(prod) + override(dev) 2파일 구조(ADR 0035)는 그대로다.

3. dev 한정 ZDM mock endpoint 제거 — `web/routers/dev_zdm_mock.py`·`web/main.py` 의 `APP_ENV=="dev"` 등록 분기·`config.py` 의 `zdm_resolver_host_override`. install task 는 dev·prod 모두 실 ZDM 호스트에서 직접 메타를 fetch 한다(prod 와 동일 경로). install 모달 prefill default 인 `zdm_default_ip` 설정은 런타임 기능이라 존속.

4. dev env 카탈로그 = 루트 `env.dev.example` (APP_ENV=dev·weak default 허용·host=compose 서비스명). 루트 `env.example` = prod 배포 템플릿이라는 ADR 0036 결정은 존속하고, dev 카탈로그 위치만 `dev/.env.example` 에서 루트 `env.dev.example` 로 옮긴다.

5. dev pgAdmin 의 `/pgpass` bind mount(구 `dev-up.sh` 가 생성) 제거 — dev·prod 모두 첫 연결 시 DB 비번 1회 입력. `docker/pgadmin/pgpass`·`pgpass.example`·servers.json 의 `PassFile` 제거.

## Consequences

- dev 기동: `cp env.dev.example .env && docker compose up` (base + override 자동 머지, 핫리로드). 별도 스크립트·VM 프로비저닝 없음.
- agent 동반 E2E 검증은 본 repo 범위 밖 — OpenStack 공급 환경에서 수행한다. pytest 단위·통합 테스트는 그대로.
- ADR 0037(libvirt 재전환)·ADR 0018(ZDM mock)·ADR 0026(Lima->OrbStack, 이미 0037 이 supersede)이 다룬 dev 가상화 메커니즘은 역사 기록으로 보존(ADR 불변)하되, 그 자산 자체는 repo 에서 제거된다.
- 폐기 토큰 잔존 0 검증(#F12): `libvirt`(도메인 가상망 용례 제외)·`virsh`·`dev-up`·`dev-down`·`win-server-01`·`dev_zdm_mock`·`zdm_resolver_host_override`·`host.docker.internal`.
