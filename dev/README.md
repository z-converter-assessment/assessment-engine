# dev/

본 디렉토리는 dev 환경 한정 자료 (#A0). prod 운영에는 사용 안 함 (ADR 0012).

전제: Apple Silicon (arm64) macOS host. Lima 기반 4 VM 매트릭스로 파이프라인 검증.

본 repo 는 외부 agent repo (`assessment-agent`) 와 파일 구조 결합·강제하지 않음 — agent 바이너리는
pipeline-up.sh 가 자동 확보 (sibling repo cross-build 또는 release artifact fetch).

## 파일 카탈로그

| 항목 | 역할 | git |
|------|------|-----|
| `docker-compose.yml` | 엔진 dev compose (web + consumer + diagnostic + DB + MQ + Redis + pgadmin) | 커밋 |
| `.env.example` | dev compose 기준 환경변수 카탈로그 (`cp dev/.env.example dev/.env`) | 커밋 |
| `.env` | dev compose 실값 | gitignore |
| `lima/*.yaml` | Lima VM 4대 정의 (`docs/development/pipeline.md` 단일 진실) | 커밋 |
| `agent-build/Dockerfile` | agent 빌드 Dockerfile (debian:bookworm-slim base, vendored static link) | 커밋 |
| `agent-build/build.sh` | agent cross-build 스크립트 (sibling repo buildx 호출) | 커밋 |
| `bin/assessment-agent` | agent 바이너리 산출물 (Linux arm64 ELF, static link) | gitignore — pipeline-up.sh 가 자동 산출 |
| `agent.env.example` | agent 측 환경변수 카탈로그 — RABBITMQ_*·WORKER_* | 커밋 |
| `agent.env` | agent 실값 — `cp agent.env.example agent.env` 후 운영 값으로 수정 | gitignore |
| `pgadmin-servers.json` | pgadmin GUI 자동 등록 (`docker compose --profile gui up pgadmin` 활성 시) | 커밋 |

## agent 바이너리 확보 흐름

`scripts/pipeline-up.sh` 의 `ensure_agent_binary` 단계 — 두 분기:

1. `AGENT_BINARY_URL` env set 시 — curl 로 fetch (향후 agent CI release artifact 자동화 분기).
2. 미설정 시 — `dev/agent-build/build.sh` 호출 → sibling repo (`AGENT_REPO_PATH`, default `../assessment-agent`) 를 buildx context 로 cross-build.

```bash
# A. sibling repo cross-build (default)
git clone <agent-repo> ../assessment-agent
./scripts/pipeline-up.sh

# B. release artifact fetch (확장 분기)
AGENT_BINARY_URL=https://example.com/assessment-agent ./scripts/pipeline-up.sh

# C. sibling repo 위치 override
AGENT_REPO_PATH=/elsewhere/assessment-agent ./scripts/pipeline-up.sh

# D. cross-build 만 (pipeline 안 띄움)
./dev/agent-build/build.sh
FORCE=1 ./dev/agent-build/build.sh   # buildx cache 무시
```

### build 내부 동작

- host arch detect — `uname -m` → `linux/arm64` (Apple Silicon) 또는 `linux/amd64`
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
- Lima 매트릭스 distro (Debian 12·Debian 13·Rocky 9·AlmaLinux 9) 모두 호환 (glibc ≥ 2.34, OpenSSL 3 계열)

## pipeline-up.sh 와의 관계

`./scripts/pipeline-up.sh` 가 본 디렉토리 활용:
1. `check_prereqs` — `dev/.env` + `dev/agent.env` 자동 cp (없으면 example 에서).
2. `ensure_agent_binary` — agent 바이너리 확보 (위 흐름).
3. Lima VM 4대 기동 시 `--set ".param.AgentBinDir=$(pwd)/dev/bin"` 주입.
4. 각 VM 이 `/mnt/agent-bin/assessment-agent` 를 `/usr/local/bin/` 으로 cp + systemd unit 적용.

VM 내부에서 build·devel 패키지 install 0.

## 왜 바이너리를 git 에 commit 안 하나

- agent repo 가 별도 — 산출물은 sibling repo build 또는 release artifact 채널에서 확보가 정공.
- 본 repo 가 외부 산출물 (다른 repo 빌드 결과) 을 안고 있는 것 은 책임 혼합.
- pipeline-up.sh 가 자동 확보 흐름이라 사용자는 명시 호출 불필요. cache 동작도 idempotent.

prod 운영 환경에서는 외부 인프라가 agent 자체 빌드·배포 (본 repo 무관).

## 왜 `agent.env`·`.env` 는 gitignore 인가

agent 측 secret (RABBITMQ_PASSWORD·WORKER_PASSWORD) 또는 dev compose secret 이 들어가 git 커밋 금지.
카탈로그는 `agent.env.example`·`.env.example` 로 공유.
