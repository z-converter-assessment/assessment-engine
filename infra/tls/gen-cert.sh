#!/usr/bin/env bash
# dev self-signed TLS cert 생성 — install bundle endpoint (HTTPS) + agent 측 다운로드 검증용.
#
# 산출물 (모두 본 디렉토리, .gitignore):
#   ca.pem       — self-signed CA 인증서 (Lima VM truststore 에 inject)
#   ca.key       — CA 개인 키 (server cert 서명용, 본 디렉토리 외 노출 금지)
#   server.pem   — server 인증서 (CA 서명, SAN 포함)
#   server.key   — server 개인 키 (engine uvicorn TLS 용, 컨테이너에 mount)
#
# SAN 호스트:
#   host.lima.internal  — Lima VM 에이전트가 다운로드할 때 사용
#   localhost           — 호스트 브라우저·curl 디버깅용
#   127.0.0.1           — IP 직접 접근
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

echo "infra/tls: self-signed CA + server cert 생성 중..."

# 1) CA 키 + 자체 서명 인증서 (CN=Assessment Dev CA, 10년 유효)
openssl genrsa -out ca.key 4096 2>/dev/null
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
  -subj "/C=KR/O=Assessment Engine/CN=Assessment Dev CA" \
  -out ca.pem 2>/dev/null

# 2) Server 키 + CSR + CA 서명 (SAN 포함)
openssl genrsa -out server.key 4096 2>/dev/null
openssl req -new -key server.key \
  -subj "/C=KR/O=Assessment Engine/CN=host.lima.internal" \
  -out server.csr 2>/dev/null

# SAN extension config — openssl req 의 -addext 는 OpenSSL 1.1.1+ 만, 호환성 위해 config 파일 방식.
cat > san.cnf <<'CNF'
subjectAltName = DNS:host.lima.internal, DNS:localhost, IP:127.0.0.1
extendedKeyUsage = serverAuth
CNF

openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial \
  -out server.pem -days 3650 -sha256 -extfile san.cnf 2>/dev/null

# 3) 정리 (CSR / SAN config 는 1회용)
rm -f server.csr san.cnf

# 4) 권한 — 키는 0600 (개인 키 보호), 인증서는 0644
chmod 0600 ca.key server.key
chmod 0644 ca.pem server.pem

echo "infra/tls: 생성 완료"
echo "  CA       : $SCRIPT_DIR/ca.pem (Lima VM truststore inject)"
echo "  Server   : $SCRIPT_DIR/server.pem + server.key (engine uvicorn TLS)"
echo "  유효기간: 10년"
