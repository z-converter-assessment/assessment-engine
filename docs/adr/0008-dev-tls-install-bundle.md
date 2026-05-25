# ADR 0008 — dev 환경 engine HTTPS endpoint (전체 통합) + SAN 동적화

상태: Superseded by ADR 0009 (2026-05-15) → ADR 0016 (2026-05-21) — agent 측 HTTPS-only 정책 한계로 dev plain HTTP 복귀 (0009), 이후 self-host install bundle endpoint 자체 제거로 본 결정 무효 (0016)

## Context

ADR 0007 채택으로 task.install 메시지가 download.url 을 페이로드에 담아 발행되고, 원격 호스트 worker 가 그 URL 을 직접 fetch 한다. worker 의 다운로드 정책은 두 가지로 hard-coded:

- HTTPS only — `download.c::download_url_extract_host` 가 `https://` prefix 외 URL 거부 (`DOWNLOAD_ERR_URL_NOT_ALLOWED`).
- Host whitelist — `WORKER_DOWNLOAD_ALLOWED_HOSTS` 환경변수의 hostname 화이트리스트와 case-insensitive 정확 매치.

엔진 self-host install bundle endpoint (`/zconverter.tar.gz`) 는 plain HTTP 로 노출되어 있었다. 결과: worker 가 다운로드 단계에서 `failure_reason="url_not_allowed"` 로 즉시 거부 — schema 정합화 검증은 통과하지만 success 경로 실증 불가.

초기 임시 결정 (폐기) — install bundle endpoint 만 HTTPS port 8443 + 나머지 endpoint plain HTTP port 8000 의 2-port 분리. uvicorn `asyncio.gather` 로 한 process 안에서 두 port 동시 listen. 운영자 브라우저 plain 접근 + agent worker HTTPS 다운로드 동시 충족 목적.

2-port hack 의 한계 (정정 사유):
- uvicorn 자체가 single-port 도구 — `asyncio.gather` 로 두 instance 실행은 표준 패턴 아닌 ad-hoc hack.
- OpenStack 등 분산 staging/prod 환경에서 cert SAN 이 Lima 한정 (`host.lima.internal` / `localhost` / `127.0.0.1`) 이라 다른 VM 의 agent worker 가 engine VM hostname/IP 로 TLS verify 시 실패.
- 운영 환경 (외부 ingress 종단) 모델과 일관성 깨짐.

## Decision

전체 endpoint 를 단일 port (8000) 에서 HTTPS 로 통합. uvicorn 단일 process 단일 port. `ssl_certfile` / `ssl_keyfile` 미주입 시 plain HTTP 로 fallback — prod 외부 ingress (nginx 등이 TLS 종단, 앱은 plain) 호환.

cert SAN 환경변수 파라미터화 — `infra/tls/gen-cert.sh` 가 `SAN` env 로 콤마 분리된 host 목록 받음. Lima default 외에 OpenStack staging·prod hostname/IP 자유 명시. IP 형식 자동 감지 (정규식 IPv4) -> `IP:` / 그 외 -> `DNS:`. 첫 SAN 이 CN.

운영자 접근 흐름:
- curl: `curl -k https://<engine-host>:8000/health` (`-k` self-signed 무시) 또는 `--cacert infra/tls/ca.pem`
- 브라우저: `https://<engine-host>:8000/servers/` -> self-signed 경고 -> Chrome `thisisunsafe` / Firefox 예외 추가 후 진입. 또는 ca.pem 을 macOS Keychain / OS trust store 에 등록 후 자연 접근
- agent worker: cert SAN 에 engine VM hostname/IP 가 포함되어 있어야 TLS verify 통과. `WORKER_DOWNLOAD_ALLOWED_HOSTS` 도 같은 hostname 명시.

토폴로지:

| 항목 | 값 |
|------|-----|
| Web port | 8000 (HTTPS — cert 주입 시 / plain HTTP — 미주입 시) |
| Cert 생성 | `infra/tls/gen-cert.sh` — openssl req self-signed CA + server cert. SAN 환경변수 파라미터화. 유효기간 10년 |
| Default SAN | `host.lima.internal,localhost,127.0.0.1` (Lima dev) |
| 산출물 | `infra/tls/ca.pem` (원격 호스트 truststore), `infra/tls/server.{pem,key}` (engine uvicorn TLS). 모두 `.gitignore` |
| Engine TLS 활성화 | `web/__main__.py` 가 `ssl_certfile`/`ssl_keyfile` 채워지면 uvicorn 에 그대로 전달 |
| install_bundle_url default | `https://host.lima.internal:8000/zconverter.tar.gz` |
| Lima VM CA inject | `dev-up.sh::post_provision_vm` 이 `ca.pem` 을 heredoc 으로 전달. Debian/Ubuntu 는 `/usr/local/share/ca-certificates/` + `update-ca-certificates`, RHEL 계열은 `/etc/pki/ca-trust/source/anchors/` + `update-ca-trust` |
| OpenStack/staging VM CA inject | 운영자 책임 — Ansible/cloud-init 으로 `ca.pem` 을 원격 VM truststore 에 cp + `update-ca-certificates` (또는 `update-ca-trust`). agent.env 의 `WORKER_DOWNLOAD_ALLOWED_HOSTS` 에 engine VM hostname 명시 |
| Healthcheck | docker-compose.override.yml 이 HTTPS + cafile 검증 (`ssl.create_default_context(cafile=...)`) |

prod 분기:
- prod 는 외부 ingress (nginx 등) 가 TLS 종단 — engine uvicorn 은 `SSL_CERTFILE`/`SSL_KEYFILE` 미주입 시 plain HTTP 동작. ingress 에 운영 cert 주입.
- prod 의 cert 분배·rotation 정책은 별도 ADR (Let's Encrypt 또는 운영 CA).

broker AMQP TLS 와의 차이 (rabbitmq.md 3절):
- broker dev plain: 디버깅 비용 > 보안 이득. `rabbitmqadmin` / 관리 UI TLS 핸드셰이크가 매번 가로막음.
- engine HTTPS dev (본 ADR): agent worker HTTPS-only 정책 정합 + 분산 staging/prod 호환. self-signed CA 부담은 gen-cert.sh + truststore inject 자동화로 최소화.

## Consequences

### 긍정

- agent worker HTTPS-only 정책 정합 — success 경로 실증 가능 + dev/staging 환경 모두 작동.
- OpenStack 등 분산 환경 호환 — SAN 환경변수로 engine VM hostname/IP 명시 시 다른 VM agent 가 TLS verify 통과.
- 운영자 접근 - curl `-k` 또는 `--cacert` / 브라우저 self-signed 경고 후 진입 / CA truststore 등록 후 자연 접근 — 모두 가능.
- 단일 port 단일 process — uvicorn 표준 패턴, prod 외부 ingress 모델과 일관.

### 부정·한계

- self-signed CA 분배 부담 — 원격 호스트 VM 마다 CA truststore inject 필요. Lima 는 dev-up.sh 자동화, OpenStack 등은 운영자 책임 (Ansible/cloud-init).
- 호스트 브라우저는 별개 — macOS Keychain 등록 안 하면 self-signed 경고. dev curl 은 `-k` 또는 `--cacert` flag 명시 의무.
- INSTALL_BUNDLE_SHA256 가 컨테이너 process 재기동 시 변경 가능 — `tarfile.open(mode="w:gz")` 의 gzip header mtime 자동 박힘. 같은 process 안에서는 일관 (모듈 상수 1회 계산), 재기동 시 변경. 결정적 빌드는 future ADR.

### 운영 절차

dev (Lima):
1. `cp infra/agent.env.example infra/agent.env` (최초 1회)
2. `./dev-up.sh` — cert 자동 생성 (Lima default SAN) + Lima VM CA inject 자동

dev/staging (OpenStack 등 비-Lima):
1. engine VM 에서 cert 생성 (SAN 환경변수로 engine VM hostname/IP 명시):
   ```bash
   SAN="engine.example,192.168.1.10,localhost" bash infra/tls/gen-cert.sh
   docker compose up -d
   ```
2. agent VM 마다 `infra/tls/ca.pem` cp + truststore inject (Ansible/cloud-init):
   - Debian/Ubuntu: `cp ca.pem /usr/local/share/ca-certificates/engine-ca.crt && update-ca-certificates`
   - RHEL: `cp ca.pem /etc/pki/ca-trust/source/anchors/engine-ca.crt && update-ca-trust`
3. agent VM 의 agent.env 정합:
   - `WORKER_DOWNLOAD_ALLOWED_HOSTS=engine.example` (engine VM hostname)
   - install bundle URL 도 같은 hostname (`https://engine.example:8000/zconverter.tar.gz`)
4. 운영자 브라우저: `https://engine.example:8000/servers/` -> self-signed 경고 후 진입 (또는 CA 등록)

prod:
- 외부 ingress 가 TLS 종단 (nginx + Let's Encrypt 등). engine `SSL_CERTFILE` 미주입 -> plain HTTP. 별도 ADR.

### 영향도 (CLAUDE.md F9)

- 코드: `web/__main__.py` (단일 process 단일 port, cert 옵셔널), `config.py::WebSettings` (`install_bundle_url` default port 8000, `https_port` 필드 폐기, `ssl_certfile`/`ssl_keyfile` 옵셔널 필드).
- 인프라: `infra/tls/gen-cert.sh` SAN 환경변수 파라미터화. `docker-compose.yml` web 서비스 expose 8000 만 (8443 폐기). `docker-compose.override.yml` web 서비스 ports 8000 + healthcheck HTTPS + cafile override. `dev-up.sh::check_prereqs` (cert 자동 생성, Lima default SAN) + `post_provision_vm` (Lima VM CA inject).
- 문서: `docs/operations/dev-prod.md` 매트릭스 갱신, 본 ADR.

## 관련 문서

- ADR 0007 — task.install / task.result 별도 큐 모델 (본 ADR 의 전제)
- ADR 0006 — OpenStack 분산 staging (본 ADR 의 분산 환경 적용 시나리오)
- `docs/architecture/rabbitmq.md` "dev/prod 분기" 절 — broker AMQP 의 반대 결정 (dev plain) 과 대조
- `infra/tls/gen-cert.sh` — cert 생성 절차 (SAN 환경변수)
- CLAUDE.md #F8 (시크릿·PII) — cert key 노출 금지
