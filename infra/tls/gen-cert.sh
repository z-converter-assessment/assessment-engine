#!/usr/bin/env bash
# dev self-signed TLS cert 생성 — engine HTTPS endpoint + agent worker 다운로드 TLS verify 용.
#
# 산출물 (모두 본 디렉토리, .gitignore):
#   ca.pem      — self-signed CA 인증서 (원격 호스트 VM truststore 에 inject)
#   ca.key      — CA 개인 키 (server cert 서명용)
#   server.pem  — server 인증서 (CA 서명, SAN 포함)
#   server.key  — server 개인 키 (engine uvicorn TLS 용, 컨테이너에 mount)
#
# SAN 파라미터화 (ADR 0008) — 환경변수 SAN 으로 콤마 분리된 host 목록:
#   bash infra/tls/gen-cert.sh                                              # default (Lima)
#   SAN="engine.example,192.168.1.10" bash infra/tls/gen-cert.sh            # OpenStack 등
#   SAN="host.lima.internal,engine.internal,10.0.0.5" bash gen-cert.sh      # 복합
#
# default SAN (SAN env 미지정 시): host.lima.internal,localhost,127.0.0.1
# 첫 SAN 이 CN. IP 형식은 자동 감지 (`IP:`), 그 외는 DNS 로 분류.
#
# 호출 패턴:
#   bash infra/tls/gen-cert.sh           # 이미 있으면 skip (idempotent)
#   FORCE=1 bash infra/tls/gen-cert.sh   # 강제 재생성
#
# 갱신 주기: cert 유효기간 10년 default — dev 환경에서 reissue 부담 0.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f ca.pem ] && [ -f server.pem ] && [ -f server.key ] && [ "${FORCE:-0}" != "1" ]; then
  echo "infra/tls: cert 존재 — skip (FORCE=1 로 재생성)"
  exit 0
fi

SAN_INPUT="${SAN:-host.lima.internal,localhost,127.0.0.1}"
echo "infra/tls: self-signed CA + server cert 생성 (SAN=$SAN_INPUT)..."

# IP·DNS 분기 자동 (간단 정규식 — IPv4 만, IPv6 는 dev 외).
san_lines=""
cn=""
IFS=',' read -ra hosts <<< "$SAN_INPUT"
for h in "${hosts[@]}"; do
  h="$(echo "$h" | tr -d '[:space:]')"
  [ -z "$h" ] && continue
  [ -z "$cn" ] && cn="$h"
  if [[ "$h" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
    san_lines+=", IP:$h"
  else
    san_lines+=", DNS:$h"
  fi
done
san_lines="${san_lines#, }"  # 앞 ", " 제거

# 1) CA 키 + 자체 서명 인증서 (10년 유효)
openssl genrsa -out ca.key 4096 2>/dev/null
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
  -subj "/C=KR/O=Assessment Engine/CN=Assessment Dev CA" \
  -out ca.pem 2>/dev/null

# 2) Server 키 + CSR + CA 서명 (SAN 포함)
openssl genrsa -out server.key 4096 2>/dev/null
openssl req -new -key server.key \
  -subj "/C=KR/O=Assessment Engine/CN=$cn" \
  -out server.csr 2>/dev/null

cat > san.cnf <<CNF
subjectAltName = $san_lines
extendedKeyUsage = serverAuth
CNF

openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial \
  -out server.pem -days 3650 -sha256 -extfile san.cnf 2>/dev/null

rm -f server.csr san.cnf

chmod 0600 ca.key server.key
chmod 0644 ca.pem server.pem

echo "infra/tls: 생성 완료"
echo "  CN       : $cn"
echo "  SAN      : $san_lines"
echo "  CA       : $SCRIPT_DIR/ca.pem (원격 호스트 truststore inject)"
echo "  Server   : $SCRIPT_DIR/server.pem + server.key (engine uvicorn TLS)"
echo "  유효기간: 10년"
