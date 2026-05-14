# ADR 0008 — dev install bundle HTTPS endpoint (임시 dev workaround)

상태: 채택 (2026-05-14, 임시)

## Context

ADR 0007 채택으로 task.install 메시지가 download.url 을 페이로드에 담아 발행되고, 원격 호스트 worker 가 그 URL 을 직접 fetch 한다. worker 의 다운로드 정책은 두 가지로 hard-coded:

- HTTPS only — `download.c::download_url_extract_host` 가 `https://` prefix 외 URL 거부 (`DOWNLOAD_ERR_URL_NOT_ALLOWED`).
- Host whitelist — `WORKER_DOWNLOAD_ALLOWED_HOSTS` 환경변수의 hostname 화이트리스트와 case-insensitive 정확 매치.

엔진 self-host install bundle endpoint (`/zconverter.tar.gz`) 는 plain HTTP 로 노출되어 있었다. 결과: worker 가 다운로드 단계에서 `failure_reason="url_not_allowed"` 로 즉시 거부. 스키마 정합화 검증은 통과하지만 success 경로 실증 불가.

dev/prod 정책 비대칭:
- broker AMQP: dev plain, prod TLS (rabbitmq.md §3) — self-signed CA 분배 부담이 broker 디버깅 가치를 깎는다.
- install bundle: dev/prod 모두 plain 가 broker 정책과 일관. 그러나 agent worker HTTPS-only 가 prod 정책으로 박혀 있어 dev 에서도 동일 정책 적용 시 endpoint TLS 필요.

worker 정책 우회 toggle (`WORKER_ALLOW_HTTP=1` 같은) 을 agent 측에 도입하려면 agent 코드 변경 필요. 본 작업 범위는 "엔진 측만 정합화" 이므로 agent 정책에 맞춰 엔진을 변경.

## Decision

dev install bundle endpoint 만 HTTPS 로 노출하고, 나머지 endpoint (운영자 브라우저·API·healthcheck) 는 plain HTTP 유지. 한 process 안에서 uvicorn.Server 2 instance + `asyncio.gather` 로 두 port 동시 listen.

본 결정은 임시 dev workaround. 정석 후속:
- agent 측 dev http toggle (예: `WORKER_ALLOW_HTTP=1`) 도입 시 dev 전체 plain HTTP 로 통일 — broker 정책과 일관
- prod 는 외부 ingress (nginx 등) 가 TLS 종단 — 별도 ADR

토폴로지:

| 항목 | 값 |
|------|-----|
| Plain HTTP port | 8000 (`WebSettings.web_port`) — 운영자 브라우저·API·healthcheck |
| HTTPS port | 8443 (`WebSettings.https_port`) — install bundle endpoint 한정 |
| Cert 생성 | `infra/tls/gen-cert.sh` — openssl req self-signed CA + server cert (SAN: `host.lima.internal`, `localhost`, `127.0.0.1`). 유효기간 10년 |
| 산출물 | `infra/tls/ca.pem` (Lima VM truststore), `infra/tls/server.{pem,key}` (engine uvicorn TLS). 모두 `.gitignore` |
| Engine 2-port 활성화 | `web/__main__.py` 가 uvicorn.Server 2 instance + asyncio.gather. `ssl_certfile`/`ssl_keyfile` 미주입 시 HTTPS port skip (prod ingress 호환) |
| install_bundle_url default | `https://host.lima.internal:8443/zconverter.tar.gz` |
| Lima VM CA inject | `dev-up.sh::post_provision_vm` 이 `ca.pem` 을 heredoc 으로 전달. Debian/Ubuntu 는 `/usr/local/share/ca-certificates/` + `update-ca-certificates`, RHEL 계열은 `/etc/pki/ca-trust/source/anchors/` + `update-ca-trust` |
| Healthcheck | base `docker-compose.yml` 의 plain HTTP urlopen 그대로 (port 8000) |

## Consequences

### 긍정

- agent worker HTTPS-only 정책과 정합 — `failure_reason="url_not_allowed"` 회피, success 경로 실증 가능.
- 운영자 브라우저 plain HTTP 자연 접근 — macOS Keychain CA 등록 부담 0.
- sha256 외 layer of defense — TLS handshake 가 MITM 차단 (install bundle 다운로드 경로 한정).

### 부정·한계 (임시 결정 사유)

- uvicorn 2-port + asyncio.gather 는 표준 패턴 아님 — uvicorn 자체가 single-port 도구. ad-hoc hack 에 가까움.
- prod 모델 (외부 ingress TLS 종단 + 앱 plain) 과 다른 dev 흐름 — dev/prod 분기 매트릭스에 새 항목 추가됨.
- self-signed CA Lima VM 분배 부담 — dev-up.sh 자동화로 운영자 수동 작업 0 이지만, 신규 OS 추가 시 분기 추가 의무.
- INSTALL_BUNDLE_SHA256 가 컨테이너 process 재기동 시 변경 가능 — `tarfile.open(mode="w:gz")` 의 gzip header mtime 이 자동 박힘. 같은 process 안에서는 일관 (모듈 상수 1회 계산), 재기동 시 변경. 운영 영향: 컨테이너 재기동 사이에 발행된 task.install 페이로드의 sha256 이 재기동 후 새 bundle 의 sha256 과 불일치. 결정적 빌드는 future ADR.

### 정석 후속 옵션 (본 ADR supersede 후보)

1. agent 측 dev http toggle 도입 — `WORKER_ALLOW_HTTP=1` env. dev 전체 plain HTTP 로 통일, broker 정책 일관. agent 측 호환성 작업 필요.
2. nginx ingress sidecar — prod 모델 (외부 ingress TLS 종단) dev 에서도 동일. install bundle endpoint 만 HTTPS forward, 나머지 plain. 컨테이너 +1.
3. 본 PR 데이터 정합화 목표만 보면 — dev plain HTTP 복귀 + agent worker `url_not_allowed` 정상 동작 인지. success 경로 검증은 prod 또는 별도 작업.

### 영향도 (CLAUDE.md F9)

- 코드: `web/__main__.py` (uvicorn.Server 2 instance + asyncio.gather), `config.py::WebSettings` (`web_port` plain / `https_port` HTTPS / `install_bundle_url` default 8443 / `ssl_certfile` / `ssl_keyfile` 옵셔널).
- 인프라: `infra/tls/gen-cert.sh` 신설 + `.gitignore` cert 패턴 추가. `docker-compose.yml` web 서비스 expose 8443 추가. `docker-compose.override.yml` web 서비스 volume mount + 8443 ports + healthcheck base 유지. `dev-up.sh::check_prereqs` (cert 자동 생성) + `post_provision_vm` (Lima VM CA inject).
- 문서: `docs/operations/dev-prod.md` 정책 매트릭스 갱신, 본 ADR.

## 관련 문서

- ADR 0007 — task.install / task.result 별도 큐 모델 (본 ADR 의 전제)
- `docs/architecture/rabbitmq.md` "dev/prod 분기" 절 — broker AMQP 의 반대 결정 (dev plain) 과 대조
- `infra/tls/gen-cert.sh` — cert 생성 절차
- CLAUDE.md #F8 (시크릿·PII) — cert key 노출 금지
