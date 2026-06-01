#!/usr/bin/env bash
# dev/agent-build/build.sh — assessment-agent 바이너리 빌드 (Linux/amd64).
#
# dev-up.sh 의 ensure_agent_binary 가 호출 — agent 소스 변경 시 산출물 dev/bin/assessment-agent 갱신.
# AGENT_REPO_PATH env 로 sibling repo 위치 override (default ../assessment-agent).
# (AGENT_BINARY_URL set 시엔 dev-up 이 본 스크립트 대신 release artifact 를 curl fetch.)
#
# 흐름:
#   1. AGENT_REPO_PATH(sibling repo)를 buildx context 로 사용 (host vendor/ 영향 0)
#   2. debian:bookworm-slim 컨테이너에서 vendored static link 빌드 — base OpenSSL 3.0.11,
#      VM 매트릭스 최저(Debian 12)와 ABI 일치. cJSON·rabbitmq-c·curl·libarchive 정적, OpenSSL/glibc/zlib dynamic.
#   3. dev/bin/assessment-agent 로 export
#
# 사용:
#   ./dev/agent-build/build.sh                                 # 멱등 (이미 최신이면 skip)
#   FORCE=1 ./dev/agent-build/build.sh                         # buildx cache 무시 강제 재빌드
#   AGENT_REPO_PATH=/path/to/agent ./dev/agent-build/build.sh  # sibling repo 위치 override

set -euo pipefail

# repo root (assessment-engine) 로 cwd 고정 — 본 스크립트가 dev/agent-build/ 안이라 2 단계 상위.
cd "$(dirname "$0")/../.."

AGENT_SRC="${AGENT_REPO_PATH:-../assessment-agent}"
OUT_BIN="dev/bin/assessment-agent"
PLATFORM="linux/amd64"  # dev 호스트 x86_64 확정 (#A0)

# 1. 사전 점검
if [ ! -d "$AGENT_SRC" ]; then
  echo "오류: $AGENT_SRC 없음. AGENT_REPO_PATH env 로 sibling repo 위치 지정 또는 본 repo 와" >&2
  echo "      같은 부모 디렉토리에 git clone 하라 (default: ../assessment-agent)." >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "오류: docker 미설치." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "오류: docker daemon 미가동." >&2
  exit 1
fi
if ! docker buildx version >/dev/null 2>&1; then
  echo "오류: docker buildx 미설치 (apt: docker-buildx-plugin)." >&2
  exit 1
fi

# 2. 멱등 — 산출물이 src 보다 최신이면 skip (FORCE=1 우회). buildx 도 layer 단위 캐시.
if [ "${FORCE:-0}" != "1" ] && [ -f "$OUT_BIN" ]; then
  newest_src="$(find "$AGENT_SRC/src" "$AGENT_SRC/include" "$AGENT_SRC/Makefile" -type f -newer "$OUT_BIN" 2>/dev/null | head -1)"
  if [ -z "$newest_src" ]; then
    echo "  $OUT_BIN 이미 최신 — 빌드 skip (FORCE=1 로 강제 재빌드)"
    exit 0
  fi
fi

mkdir -p dev/bin

# 3. docker buildx — 외부 agent repo 를 build context 로. host vendor/ 와 격리.
#    --output type=local,dest=dev/bin -> 최종 stage(scratch)의 /assessment-agent 를 host 로 copy.
echo "agent 빌드 ($PLATFORM, debian:bookworm-slim + vendored static link)..."
docker buildx build \
  --platform="$PLATFORM" \
  --file dev/agent-build/Dockerfile \
  --target export \
  --output "type=local,dest=dev/bin" \
  "$AGENT_SRC"

chmod 755 "$OUT_BIN"

echo ""
echo "빌드 완료:"
file "$OUT_BIN"
ls -lh "$OUT_BIN"
