# ADR 0009 — dev 환경 plain HTTP 복귀 (ADR 0008 supersede)

상태: 채택 (2026-05-15) — Supersedes ADR 0008

## Context

ADR 0008 (engine 단일 port HTTPS 통합 + SAN 동적화) 도입 후 다음 운영 부담 누적:

1. self-signed CA 분배 — ZConverter Install 대상 VM 마다 ca.pem cp + `update-ca-certificates` (또는 `update-ca-trust`) 수동/Ansible 의무.
2. 운영자 브라우저 self-signed 경고 — macOS Keychain 등록 안 하면 매번 `thisisunsafe` / 예외 추가.
3. cert SAN 환경변수 파라미터화로 OpenStack 등 분산 환경 호환은 됐으나 운영 절차 복잡도 증가.
4. agent 측 HTTPS-only 정책 (`download.c:49` `https://` prefix 강제) 의 변경 권한이 본 프로젝트 작업 범위 외 — engine 측 cert 정합이 유일한 우회.

ADR 0008 본문에 명시한 정석 후속 (agent 측 dev http toggle 또는 nginx ingress sidecar) 은 모두 본 PR 범위 외 작업이 필요. 그 사이 기간에 dev/staging 운영자 부담 누적.

핵심 trade-off — broker AMQP 정책(`rabbitmq.md §3`) 와의 일관성. broker 는 dev plain AMQP / prod AMQPS. engine HTTPS 도입 시 install bundle endpoint 한 곳만 보안 강화하는 비대칭 — 폐쇄망 가정에서 의미 약함.

## Decision

dev 환경 engine 을 plain HTTP 로 복귀. ADR 0008 의 모든 변경 (uvicorn TLS · cert mount · Lima VM CA inject · gen-cert.sh) 폐기 또는 비활성.

토폴로지:

| 항목 | 값 |
|------|-----|
| Web port | 8000 plain HTTP (모든 endpoint) |
| Engine self-host install bundle endpoint | `http://<engine-host>:8000/zconverter.tar.gz` |
| TLS cert | 없음 (gen-cert.sh + infra/tls/ 폐기) |
| Lima VM CA inject | 폐기 |
| broker AMQP | dev plain (그대로) |

ADR 0008 도입 코드/인프라 변경 revert:
- `web/__main__.py` — uvicorn plain HTTP, ssl 인자 제거.
- `config.py::WebSettings` — `ssl_certfile` / `ssl_keyfile` 필드 제거. `install_bundle_url` default `http://host.lima.internal:8000/zconverter.tar.gz`.
- `docker-compose.override.yml` web — cert mount + `SSL_CERTFILE` env + HTTPS healthcheck override 제거. base 의 plain HTTP healthcheck 그대로.
- `dev-up.sh` — `check_prereqs` cert auto-gen hook 제거. `post_provision_vm` Lima VM CA inject 절 제거.
- `infra/tls/` — `gen-cert.sh` 폐기 (필요 시 future agent 작업 후 재활성). `.gitignore` cert 패턴은 유지 (휴먼 에러 방지).

## ZConverter Install 기능의 한계

agent worker 의 `download.c:49` 가 `https://` prefix 외 URL 거부 — engine plain HTTP install bundle 은 agent worker 가 `failure_reason="url_not_allowed"` 로 즉시 reject.

영향:
- task.install / task.result schema 정합화 (ADR 0007 D1·D2·D4·D5 + D6 6 필드) 는 여전히 검증됨 (failure 경로로 wire format 통과).
- task.install / task.result success 경로 (install.sh 실제 실행 + exit_code=0 + stdout_tail 캡처) 는 dev 에서 검증 불가. 단 그 경로 자체는 코드상 존재하고 agent 측 호환성 작업 후 자동 활성화.

운영자 가시성 3 layer (list "최근 작업" column + detail timeline + Web API) 는 그대로 작동 — `failure_reason="url_not_allowed"` 도 정상 row 로 표시됨.

## 정석 후속 (ADR 0009 supersede 후보)

ZConverter Install success 경로 활성화는 다음 셋 중 하나가 필요:

1. agent 측 dev http toggle 도입 — `WORKER_ALLOW_HTTP=1` env. agent 측 download.c L49 분기. agent 호환성 작업.
2. agent USE_VENDORED=1 + vendor curl/cert pinning — agent 빌드 시스템 변경.
3. nginx ingress sidecar (engine 측) — TLS 종단을 ingress 가 담당, agent 는 ingress 의 도메인으로 fetch. 회사 PKI 또는 Let's Encrypt 의 운영 cert 사용 시 self-signed 부담 0.

옵션 3 이 prod 모델과 일관 — 본격 도입 시 별도 ADR.

## Consequences

### 긍정

- 운영자 부담 0 — cert 생성·분배·truststore inject·브라우저 self-signed 경고 없음.
- broker AMQP dev plain 정책과 일관 — dev 환경 전반 plain.
- engine 코드/인프라 단순화 — uvicorn standard 호출.
- 본 PR 본래 목표 (task schema 정합화) 는 그대로 달성 — failure 경로로 wire format 검증.

### 부정·한계

- ZConverter Install success 경로 dev 검증 불가 — agent 측 호환성 작업 후 활성화.
- prod 도입 시 TLS 모델 (외부 ingress 종단 또는 회사 PKI) 별도 ADR — 본 ADR 은 dev 한정.

### 영향도 (CLAUDE.md F9)

- 코드: `web/__main__.py` plain. `config.py` ssl 필드 제거 + install_bundle_url default 변경.
- 인프라: `docker-compose.override.yml` cert mount + healthcheck override 제거. `dev-up.sh` cert auto-gen + Lima CA inject 제거. `infra/tls/gen-cert.sh` 폐기 (파일 보존 vs 삭제는 별도 결정).
- 문서: README 접속 표 plain HTTP. CLAUDE.md ADR 0008 supersede 표기 + 0009 추가. dev-prod.md 매트릭스 dev plain 복귀. agent.md task.result success 경로 미가용 명시.

## 관련 문서

- ADR 0007 — task.install / task.result 별도 큐 모델 (본 ADR 은 그 dev TLS 정책만 정정)
- ADR 0008 — engine HTTPS 통합 (본 ADR 이 supersede)
- `docs/architecture/rabbitmq.md` "dev/prod 분기" 절 — broker AMQP dev plain 과 동일 정책
- CLAUDE.md #F8 (시크릿·PII) — agent 측 cert 정책 변경 시 재검토 의무
