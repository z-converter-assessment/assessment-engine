# agent 사전 빌드용 Dockerfile — `dev/agent-build/build.sh` 가 buildx context 로 사용.
#
# 컨텍스트: 외부 `../assessment-agent` (read-only). host vendor/ 산출물에 영향 안 받음 (격리).
# base = debian:bookworm-slim — OpenSSL 3.0.11 (VM 매트릭스 최저 = Debian 12 와 ABI 일치).
# Rocky 9 base는 dnf stream이 OpenSSL을 3.5+로 업데이트해 ABI 불일치 발생 — bookworm이 정합.
# 산출물: /assessment-agent (static-linked, OpenSSL/glibc/zlib만 dynamic).
#
# buildx layer cache: vendor-fetch·vendor-build는 별도 layer라 source 변경 시에도 재사용.

FROM debian:bookworm-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      gcc g++ make cmake git pkg-config \
      libssl-dev zlib1g-dev ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# 1) Makefile·vendor metadata만 먼저 cp — vendor-fetch는 source 변경과 무관하니 layer 분리.
COPY Makefile /build/Makefile
RUN make USE_VENDORED=1 vendor-fetch

# 2) vendor-build (cmake로 cJSON·rabbitmq-c·curl·libarchive 정적 lib). source 변경 무관 layer.
RUN make USE_VENDORED=1 vendor-build

# 3) source 전체 cp + main build.
COPY include /build/include
COPY src /build/src
RUN make USE_VENDORED=1

# 최종 stage — 바이너리만 export.
FROM scratch AS export
COPY --from=builder /build/assessment-agent /assessment-agent
