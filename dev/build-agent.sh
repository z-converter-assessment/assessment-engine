#!/usr/bin/env bash
# dev/build-agent.sh — assessment-agent 바이너리 사용자 편의 빌드 (수동 호출).
#
# 본 스크립트는 pipeline-up.sh와 독립 — agent 소스 변경 시 사용자가 명시 호출.
# 산출물 `dev/bin/assessment-agent`를 commit하면 그 뒤로는 외부 agent repo 의존 0.
#
# 흐름:
#   1. 외부 ../assessment-agent를 buildx context로 사용 (격리 — host vendor/ 영향 0)
#   2. Rocky 9 컨테이너에서 vendored static link 빌드 (host arch matching)
#   3. dev/bin/assessment-agent로 export
#   4. 사용자가 git add + commit
#
# base = rockylinux:9 — 가장 보수 glibc 2.34 + OpenSSL 3.0. Lima 매트릭스 7 distro 모두 호환.
# static link: cJSON·rabbitmq-c·curl·libarchive 정적. OpenSSL/glibc/zlib만 dynamic.
#
# 사용:
#   ./dev/build-agent.sh         # 멱등 (이미 최신이면 skip)
#   FORCE=1 ./dev/build-agent.sh # buildx cache 무시하고 강제 재빌드

set -euo pipefail

cd "$(dirname "$0")/.."

AGENT_SRC="../assessment-agent"
OUT_BIN="dev/bin/assessment-agent"

# 1. 사전 점검
if [ ! -d "$AGENT_SRC" ]; then
  echo "오류: $AGENT_SRC 없음. 본 repo와 같은 부모 디렉토리에 git clone하라." >&2
  echo "      (본 스크립트는 빌드 시점만 외부 agent repo 필요 — pipeline-up.sh는 의존 안 함)" >&2
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
echo "agent 빌드 ($platform, Rocky 9 + vendored static link)..."
docker buildx build \
  --platform="$platform" \
  --file dev/Dockerfile.agent-build \
  --target export \
  --output "type=local,dest=dev/bin" \
  "$AGENT_SRC"

chmod 755 "$OUT_BIN"

echo ""
echo "빌드 완료 — 본 repo에 commit하면 외부 agent repo 의존 0:"
file "$OUT_BIN"
ls -lh "$OUT_BIN"
echo ""
echo "다음 단계:"
echo "  git add $OUT_BIN"
echo "  git commit -m 'chore: agent 바이너리 갱신 (<version>)'"
