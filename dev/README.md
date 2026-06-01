# dev/

본 디렉토리는 dev 환경 한정 자료 (#A0). prod 운영에는 사용 안 함 (ADR 0012).

전제: Linux x86_64 host. Docker + Compose v2 + libvirt(KVM, qemu-kvm·virsh·cloud-image-utils).
VM 매트릭스로 파이프라인 검증 — Linux 5대 + Windows 1대(기본 포함) 모두 libvirt. 단일 진실 `docs/development/pipeline.md`·`windows-vm.md`.

본 repo 는 외부 agent repo (`assessment-agent`) 와 파일 구조 결합·강제하지 않음 — agent 바이너리는
dev-up.sh 가 자동 확보 (sibling repo cross-build 또는 release artifact fetch).

## Quick Start — 시연 흐름

본 절은 외부 인프라 코드 작성 전에 본 엔진의 동작·산출물을 1회 확인하기 위한 용도. 실제 prod 배포는
본 절 영역 밖.

### 1. 엔진만 기동 (가장 단순)

```bash
cp dev/.env.example dev/.env
docker compose -f dev/docker-compose.yml up --build -d   # web + consumer + diagnostic + DB + MQ + Redis 한 번에
docker compose -f dev/docker-compose.yml down -v         # 종료 (데이터 삭제)
```

`migrate` 컨테이너가 `alembic upgrade head` 를 자동 실행. 그 후 web 컨테이너가 헬스체크 통과하면
"## 접속" 표의 endpoint 모두 동작.

### 2. 엔진 + libvirt VM 매트릭스 전체 시연

```bash
./dev/dev-up.sh                                # 전체 기동 — Docker + libvirt Linux 5 VM + Windows 모두 (인자 0)
VMS_FILTER=app-server-01 WIN_ENABLE=0 ./dev/dev-up.sh  # 약식 (Linux 1 VM, Windows 제외)
WIN_ENABLE=0 ./dev/dev-up.sh                   # Linux 전용 (Windows opt-out — 무인설치 ~20min 생략)
./dev/dev-down.sh                              # 전체 정리 — 모든 VM(Linux+Windows) 삭제 + Docker 볼륨 삭제
```

dev-up.sh 는 인자 없이 실행하면 의존성 자동 설치(sudo apt) + env 자동 복사 + Linux 5 VM + Windows 까지
"모든 환경"을 구성한다(개입 0). Windows 는 기본 포함이며 `WIN_ENABLE=0` 으로만 opt-out(Linux 전용 dev).
dev-down.sh 는 모든 VM(Windows 포함)과 Docker 볼륨을 삭제 — 다음 dev-up 이 Linux provisioning 을 매번
다시 수행해 "설치 과정"을 검증한다(빈 DB + VM 등록 + 메트릭 적재 구조 유지). 다만 Windows OS 무인설치
(~20min)는 최초 1회만 골든 이미지로 캐시하고 이후 clone 재사용해 시간을 줄인다(agent 는 매번 갱신).
캐시(로직 무관)는 보존: base cloud image / Windows ISO + 골든(OS 설치본) / redis MSI / vendor static libs.
Windows VM(win-server-01)은 Server 2022 autounattend 무인 설치. 절차 단일 진실: `docs/development/windows-vm.md`.

agent 바이너리는 `ensure_agent_binary` 가 자동 확보 — `AGENT_BINARY_URL` set 시 fetch, 미설정 시
sibling repo (`AGENT_REPO_PATH`, default `../assessment-agent`) cross-build. 상세는 "## agent
바이너리 확보 흐름" 절.

VM 매트릭스 · 합성 부하 프로파일 · attention 발화 매핑 단일 진실: `docs/development/pipeline.md`.

## 접속

dev 전체 endpoint 가 plain HTTP port 8000. prod 외부 ingress 종단은 외부 인프라 책임.

대시보드 외 SSR 페이지 (보고서·이력·참고자료 등) 는 진입점 (`/servers/`) 의 button / link 따라 도달 — 본 표는 외부 도구·자동화·운영 모니터링이 직접 도달 의무한 endpoint 만.

| 주소 | 설명 |
|------|------|
| http://localhost:8000/servers/ | 대시보드 Web UI — 모든 SSR 페이지의 진입점 |
| http://localhost:8000/health | 헬스체크 (외부 monitoring 용) |
| http://localhost:8000/metrics | Prometheus scrape target — prod 외부 노출 금지 (reverse proxy internal-only) |
| http://localhost:8000/docs | FastAPI Swagger UI (REST API contract) |
| http://localhost:8000/download/ZConverter_CloudSource_Setup_Linux.tar.gz | dev 한정 ZDM mock endpoint (ADR 0018). agent worker 가 fetch. prod 등록 안 됨 |
| http://localhost:15672 | RabbitMQ 관리 콘솔 (외부 GUI) |
| localhost:5432 | PostgreSQL (host port, DB client 직접 접속) |

## 파일 카탈로그

| 항목 | 역할 | git |
|------|------|-----|
| `docker-compose.yml` | 엔진 dev compose (web + consumer + diagnostic + DB + MQ + Redis) | 커밋 |
| `.env.example` | dev compose 기준 환경변수 카탈로그 (`cp dev/.env.example dev/.env`) | 커밋 |
| `.env` | dev compose 실값 | gitignore |
| `agent-build/Dockerfile` | agent 빌드 Dockerfile (debian:bookworm-slim base, vendored static link) | 커밋 |
| `agent-build/build.sh` | agent cross-build 스크립트 (sibling repo buildx 호출) | 커밋 |
| `bin/assessment-agent` | agent 바이너리 산출물 (Linux amd64 ELF, static link) | gitignore — dev-up.sh 가 자동 산출 |
| `agent.env.example` | agent 측 환경변수 카탈로그 — RABBITMQ_*·WORKER_* | 커밋 |
| `agent.env` | agent 실값 — `cp agent.env.example agent.env` 후 운영 값으로 수정 | gitignore |
| `dev-up.sh` / `dev-down.sh` | 전체 dev 환경 단일 진입점 — 의존성 자동설치 + Docker + libvirt Linux 5 VM(app/data/edge/offline-01/02) + Windows(win-server-01, IIS/redis, 기본 포함·`WIN_ENABLE=0` opt-out) 일괄 기동/정리. down 은 모든 VM·볼륨 삭제(캐시 보존). Windows 절차 단일 진실 `docs/development/windows-vm.md` | 커밋 |
| `win/autounattend.xml.tmpl` | Windows Server 2022 무인 설치 응답 파일 템플릿 (dev-up.sh 가 placeholder 치환) | 커밋 |

> OS EOL 카탈로그 갱신 도구는 dev 아닌 `scripts/snapshot_os_eol.py` (빌드·릴리스 maintenance, ADR 0031).

## agent 바이너리 확보 흐름

`dev/dev-up.sh` 의 `ensure_agent_binary` 단계 — 두 분기:

1. `AGENT_BINARY_URL` env set 시 — curl 로 fetch (향후 agent CI release artifact 자동화 분기).
2. 미설정 시 — `dev/agent-build/build.sh` 호출 → sibling repo (`AGENT_REPO_PATH`, default `../assessment-agent`) 를 buildx context 로 cross-build.

```bash
# A. sibling repo cross-build (default)
git clone <agent-repo> ../assessment-agent
./dev/dev-up.sh

# B. release artifact fetch (확장 분기)
AGENT_BINARY_URL=https://example.com/assessment-agent ./dev/dev-up.sh

# C. sibling repo 위치 override
AGENT_REPO_PATH=/elsewhere/assessment-agent ./dev/dev-up.sh

# D. cross-build 만 (pipeline 안 띄움)
./dev/agent-build/build.sh
FORCE=1 ./dev/agent-build/build.sh   # buildx cache 무시
```

### build 내부 동작

- host arch detect — `uname -m` → `linux/amd64` (x86_64 host)
- `docker buildx build --platform=linux/<arch> --target=export --output=type=local,dest=dev/bin`
- debian:bookworm-slim 컨테이너에서 `make vendor-fetch` + `vendor-build` + main build
- BuildKit layer cache 로 vendor 단계 재사용 (Makefile·vendor 변경 없을 때)
- `FROM scratch AS export` 최종 stage 가 `/assessment-agent` 만 host `dev/bin/` 로 export

### 격리 보장

- buildx context = `AGENT_REPO_PATH` (default `../assessment-agent`) 디렉토리만 컨테이너에 전송
- host 의 `vendor/*/build/` 산출물(다른 arch 에서 빌드된 stale `.a`) 은 컨테이너 안 별도 경로에서 fresh build → link 충돌 0

### 산출물 특성

- static link — cJSON·rabbitmq-c·curl·libarchive 정적
- dynamic 의존성 — OpenSSL·glibc·zlib 만 (base distro 기본 포함)
- libvirt 매트릭스 distro (Debian 12·Rocky 9) 호환 (glibc >= 2.34, OpenSSL 3 계열)

## dev-up.sh 와의 관계

`./dev/dev-up.sh` 가 본 디렉토리 활용:
1. `check_prereqs` — `dev/.env` + `dev/agent.env` 자동 cp (없으면 example 에서).
2. `ensure_agent_binary` — agent 바이너리 확보 (위 흐름).
3. libvirt Linux VM 5대 생성 (base cloud image vol-clone + cloud-init seed + `virsh define`). Windows 1대는 autounattend(`WIN_ENABLE=1`).
4. 각 VM 에 `dev/bin/assessment-agent` 를 ssh stdin 으로 전송 → `/usr/local/bin/` 설치 + systemd unit 적용.

VM 내부에서 build·devel 패키지 install 0.

## 왜 바이너리를 git 에 commit 안 하나

- agent repo 가 별도 — 산출물은 sibling repo build 또는 release artifact 채널에서 확보가 정공.
- 본 repo 가 외부 산출물 (다른 repo 빌드 결과) 을 안고 있는 것 은 책임 혼합.
- dev-up.sh 가 자동 확보 흐름이라 사용자는 명시 호출 불필요. cache 동작도 idempotent.

prod 운영 환경에서는 외부 인프라가 agent 자체 빌드·배포 (본 repo 무관).

## 왜 `agent.env`·`.env` 는 gitignore 인가

agent 측 secret (RABBITMQ_PASSWORD·WORKER_PASSWORD) 또는 dev compose secret 이 들어가 git 커밋 금지.
카탈로그는 `agent.env.example`·`.env.example` 로 공유.
