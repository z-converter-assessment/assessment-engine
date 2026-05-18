# dev/

본 디렉토리는 dev 환경 한정 자료 (#A0). prod 운영에는 사용 안 함.

전제: Apple Silicon (arm64) macOS host. Lima 기반 7 VM 매트릭스로 파이프라인 검증.

본 repo는 외부 agent repo와 파일 구조 결합·강제하지 않음 — agent 바이너리(`bin/assessment-agent`)를 사전 commit해두고, pipeline-up.sh는 외부 agent repo 의존 없이 본 repo 단독으로 완결.

## 파일 카탈로그

| 항목 | 역할 | git |
|------|------|-----|
| `lima/*.yaml` | Lima VM 7대 정의 (`docs/development/pipeline.md` 단일 진실) | 커밋 |
| `bin/assessment-agent` | agent 바이너리 (Linux arm64 ELF, static link) | 커밋 |
| `Dockerfile.agent-build` | agent 빌드 Dockerfile (Rocky 9 base, vendored static link) | 커밋 |
| `build-agent.sh` | agent 사용자 편의 빌드 스크립트 (외부 agent repo 필요, 수동 호출) | 커밋 |
| `agent.env.example` | agent 측 환경변수 카탈로그 — RABBITMQ_*·WORKER_*·INSTALL_BUNDLE | 커밋 |
| `agent.env` | agent 실값 — `cp agent.env.example agent.env` 후 운영 값으로 수정 | gitignore |
| `pgadmin-servers.json` | pgadmin GUI 자동 등록 (`docker compose --profile gui up pgadmin` 활성 시) | 커밋 |

## 두 도구의 책임 분리

`scripts/pipeline-up.sh` — 파이프라인 기동
- `dev/bin/assessment-agent` 존재만 검증, 없으면 즉시 fail
- 외부 agent repo·docker 빌드 의존 0
- 클론하면 즉시 동작 (바이너리가 사전 commit되어 있으니)

`dev/build-agent.sh` — agent 바이너리 갱신 (사용자 편의)
- 외부 `../assessment-agent` 소스를 buildx로 빌드
- 산출물을 `dev/bin/assessment-agent`로 export
- 사용자가 명시 호출 — pipeline-up.sh는 본 스크립트 호출 안 함

## agent 바이너리 갱신 워크플로

agent 소스 변경 후에만 본 절차 필요. 한 번 commit하면 그 뒤로는 외부 agent repo 없어도 pipeline-up.sh 동작.

```bash
# 1. 외부 agent repo 준비 (본 repo와 같은 부모 디렉토리에 git clone)
ls ../assessment-agent   # 존재 확인

# 2. 빌드 — 외부 agent repo를 buildx context로 사용, host vendor/ 영향 0
./dev/build-agent.sh        # 멱등 (이미 최신이면 skip)
FORCE=1 ./dev/build-agent.sh  # buildx cache 무시하고 강제 재빌드

# 3. 결과 확인
file dev/bin/assessment-agent   # ELF 64-bit LSB executable, ARM aarch64

# 4. 본 repo에 commit
git add dev/bin/assessment-agent
git commit -m 'chore: agent 바이너리 갱신 (<agent_version>)'
```

내부 동작
- host arch detect — `uname -m` → `linux/arm64` (Apple Silicon) 또는 `linux/amd64`
- `docker buildx build --platform=linux/<arch> --target=export --output=type=local,dest=dev/bin`
- Rocky 9 컨테이너에서 `make vendor-fetch` + `vendor-build` + main build
- BuildKit layer cache로 vendor 단계 재사용 (Makefile·vendor 변경 없을 때)
- `FROM scratch AS export` 최종 stage가 `/assessment-agent`만 host `dev/bin/`로 export

격리 보장
- buildx context = `../assessment-agent` 디렉토리만 컨테이너에 전송
- host의 `../assessment-agent/vendor/*/build/` 산출물(다른 arch에서 빌드된 stale `.a`)은 컨테이너 안 별도 경로에서 fresh build → link 충돌 0

산출물 특성
- static link — cJSON·rabbitmq-c·curl·libarchive 정적
- dynamic 의존성 — OpenSSL·glibc·zlib만 (base distro 기본 포함)
- Lima 매트릭스 7 distro(Ubuntu 24.04·Debian 12·Debian 13·Rocky 9·AlmaLinux 9) 모두 호환 (glibc ≥ 2.34, OpenSSL 3 계열)

## pipeline-up.sh와의 관계

`./scripts/pipeline-up.sh`가 본 디렉토리 활용:
1. `check_prereqs` — `dev/bin/assessment-agent` 존재 + `dev/agent.env` 존재 검증 (없으면 즉시 fail)
2. Lima VM 7대 기동 시 `--set ".param.AgentBinDir=$(pwd)/dev/bin"` 주입
3. 각 VM이 `/mnt/agent-bin/assessment-agent`를 `/usr/local/bin/`으로 cp + systemd unit 적용

VM 내부에서 build·devel 패키지 install 0.

## 왜 바이너리를 git에 commit하나

본 dev 파이프라인은 Apple Silicon arm64 dev macOS host 한정 (#A0). 다른 arch는 본 repo의 dev 파이프라인 범위 밖.
- 단일 arch 가정 — 단일 바이너리만 commit (`bin/assessment-agent`, arm64 ELF)
- 외부 agent repo·빌드 도구 의존 0 — `./scripts/pipeline-up.sh` 한 줄로 dev 환경 시작
- 본 repo 단독 자체 완결 — 클론하면 즉시 동작

prod 운영 환경에서는 외부 인프라가 agent 자체 빌드·배포 (본 repo 무관).

## 왜 `agent.env`는 gitignore인가

agent 측 secret(RABBITMQ_PASSWORD·WORKER_PASSWORD)이 들어가 git 커밋 금지. 카탈로그는 `agent.env.example`로 공유.
