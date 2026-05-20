#!/usr/bin/env bash
# dev/agent-build/build.sh — assessment-agent 바이너리 cross-build.
#
# pipeline-up.sh 가 본 스크립트를 자동 호출 — agent 소스 변경 시 산출물 dev/bin/assessment-agent
# 갱신. AGENT_REPO_PATH env 로 sibling repo 위치 override (default `../assessment-agent`).
#
# 향후 agent CI release artifact 자동화 후에는 pipeline-up.sh 가 AGENT_BINARY_URL env 로
# 분기 — release 가 있으면 curl fetch, 없으면 본 스크립트 호출. 확장 전제 분기.
#
# 흐름:
#   1. AGENT_REPO_PATH (sibling repo) 를 buildx context 로 사용 (격리 — host vendor/ 영향 0)
#   2. debian:bookworm-slim 컨테이너에서 vendored static link 빌드 (host arch matching)
#   3. dev/bin/assessment-agent 로 export
#
# base = debian:bookworm-slim — OpenSSL 3.0.11. Lima 매트릭스 최저 (Debian 12) 와 ABI 일치.
# static link: cJSON·rabbitmq-c·curl·libarchive 정적. OpenSSL/glibc/zlib 만 dynamic.
#
# 사용:
#   ./dev/agent-build/build.sh                              # 멱등 (이미 최신이면 skip)
#   FORCE=1 ./dev/agent-build/build.sh                      # buildx cache 무시하고 강제 재빌드
#   AGENT_REPO_PATH=/path/to/agent ./dev/agent-build/build.sh  # sibling repo 위치 override

set -euo pipefail

# repo root (assessment-engine) 로 cwd 고정 — 본 스크립트가 dev/agent-build/ 안에 있어 2 단계 상위.
cd "$(dirname "$0")/../.."

AGENT_SRC="${AGENT_REPO_PATH:-../assessment-agent}"
OUT_BIN="dev/bin/assessment-agent"

# 1. 사전 점검
if [ ! -d "$AGENT_SRC" ]; then
  echo "오류: $AGENT_SRC 없음. AGENT_REPO_PATH env 로 sibling repo 위치 지정 또는 본 repo 와" >&2
  echo "      같은 부모 디렉토리에 git clone 하라 (default: ../assessment-agent)." >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "오류: docker 미설치. Docker Desktop 또는 colima 기동 후 재시도." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "오류: docker daemon 미가동." >&2
  exit 1
fi
if ! docker buildx version >/dev/null 2>&1; then
  echo "오류: docker buildx 미설치. Docker Desktop 24+ 또는 buildx plugin install." >&2
  exit 1
fi

# 2. host arch detect → docker --platform 매핑 (Lima VM이 host arch와 동일하다고 가정)
host_arch="$(uname -m)"
case "$host_arch" in
  arm64|aarch64) platform="linux/arm64" ;;
  x86_64|amd64)  platform="linux/amd64" ;;
  *) echo "오류: 지원 안 하는 arch: $host_arch" >&2; exit 1 ;;
esac

# 3. 멱등 — 산출물이 src보다 최신이면 skip (FORCE=1로 우회). buildx cache도 별도 layer 단위 캐시.
if [ "${FORCE:-0}" != "1" ] && [ -f "$OUT_BIN" ]; then
  newest_src="$(find "$AGENT_SRC/src" "$AGENT_SRC/include" "$AGENT_SRC/Makefile" -type f -newer "$OUT_BIN" 2>/dev/null | head -1)"
  if [ -z "$newest_src" ]; then
    echo "  $OUT_BIN 이미 최신 — 빌드 skip (FORCE=1로 강제 재빌드)"
    exit 0
  fi
fi

mkdir -p dev/bin

# 4. docker buildx — 외부 agent repo를 build context로 사용. host vendor/와 격리.
#    --output type=local,dest=dev/bin/ → 최종 stage(scratch)의 /assessment-agent를 host로 copy.
echo "agent 빌드 ($platform, debian:bookworm-slim + vendored static link)..."
docker buildx build \
  --platform="$platform" \
  --file dev/agent-build/Dockerfile \
  --target export \
  --output "type=local,dest=dev/bin" \
  "$AGENT_SRC"

chmod 755 "$OUT_BIN"

echo ""
echo "빌드 완료:"
file "$OUT_BIN"
ls -lh "$OUT_BIN"
