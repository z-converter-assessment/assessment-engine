# ADR 0006 — OpenStack 분산 Staging 배포 (예상 시나리오)

상태: Withdrawn (2026-05-16) — 본 프로젝트 범위를 "기능 개발에 필요한 환경 구성"으로 한정하기로 결정. IaC(Terraform·Ansible·OpenStack staging)는 본 repo 범위 밖. 코드(`deploy/openstack/`)·연쇄 시나리오 문서(`docs/operations/scenarios/`)는 같은 commit에서 삭제. 추후 OpenStack 도입 결정 시 별도 repo로 새로 시작. 본 ADR 본문은 historical record로 보존 — 당시 검토했던 4 VM 토폴로지·자원 산정·배포 흐름을 참고용으로 남김.

원래 상태: 예상 시나리오 (2026-05-13) — `deploy/openstack/` 디렉토리 전체(terraform·ansible·compose·scripts)가 본 시점 예상 설계. 실 OpenStack tenant 도입 시 토폴로지·자원·구성 모두 변경 가능. 도입 시점에 별도 ADR 정정 의무 — 본 ADR은 그 시점까지 "현재 검토 중인 안" 표시.

본 ADR이 명시한 4 VM 토폴로지(bastion + engine-db + engine-mw + engine-app)와 자원 산정은 사내 폐쇄망 OpenStack 환경 가정 하 1차 설계. 실제 OpenStack tenant의 quota·network·storage·서비스 카탈로그·권한 모델에 따라 토폴로지 자체가 다르게 갈 수 있음 (예: 단일 VM 통합·k8s 기반·Magnum cluster·Trove DB 활용 등). 본 시점 lima 기반 dev 파이프라인(`docs/operations/lima.md`)은 검증 완료, OpenStack 관련 부분은 모두 예상.

## Context

지금까지 dev·테스트 환경은 docker-compose(로컬) + Lima(VM 3대 에이전트 발행 검증)로 충분했다. 다음 단계로 실 운영과 유사한 환경에서 엔진을 분산 배포·검증할 필요가 생겼다.

운영 환경 가정:
- 사내 폐쇄망 OpenStack — Horizon GUI + API 접근 가능
- 사용자 진입점: 사내 Windows Server 1대 (RDP) → Horizon
- 외부 인터넷 가능 (Docker Hub·GitHub·PyPI 직접 사용 가능)
- 외부 노출 불필요 — 사내망 사설 IP로 충분
- 운영 환경 통합 테스트(에이전트 발행 파이프라인)는 본 ADR 영역 밖 — 로컬 Lima로 유지

## Decision

OpenStack staging은 엔진 분산 배포만. 다음 토폴로지로 설치한다.

### VM 토폴로지

| VM | 역할 | 컨테이너 | 권장 spec |
|---|---|---|---|
| `bastion` | Terraform·Ansible 실행 host | (없음 — CLI 도구만) | 1 vCPU·1 GB·20 GB |
| `engine-db` | DB | postgres (TimescaleDB) | 4 vCPU·8 GB·100 GB |
| `engine-mw` | 미들웨어 | rabbitmq + redis | 2 vCPU·4 GB·40 GB |
| `engine-app` | 애플리케이션 (stateless) | migrate + web + consumer + diagnostic-worker + diagnostic-scheduler | 2 vCPU·4 GB·40 GB |

총 4 VM = 9 vCPU·17 GB RAM·200 GB disk.

### 분리 기준

- State 있음 (DB) — 영속 데이터 + 디스크 I/O. 백업·튜닝 별도
- 메모리 중심 미들웨어 (RabbitMQ + Redis) — broker·캐시. 부하 특성 유사라 같은 VM
- Stateless 앱 — 재시작 자유, 향후 수평 확장 가능

### 네트워크

- 같은 OpenStack tenant 사설망(subnet) 안 4 VM 공존
- Security Group:
  - `db`: 5432 inbound from `engine-app` only
  - `mw`: 5672·6379 inbound from `engine-app` only
  - `app`: 8000 inbound from `bastion` + 사내 jump host (실 사용자 접속)
  - `bastion`: 22 inbound from Windows Server jump host
- 외부 Floating IP 없음 — 사용자는 사내 jump host에서 사설 IP로 web/pgAdmin·Horizon 접속
- VM 간 통신은 사설 IP·hostname (DNS는 사용 안 함, Terraform output → Ansible inventory 변수 주입)

### 인증

- OpenStack: Application Credential (`~/.config/openstack/clouds.yaml`, `OS_CLOUD=assessment-engine`). `member` role 보유
- VM SSH: Horizon에서 keypair 생성 → bastion에 private key 저장, 모든 엔진 VM에 동일 keypair 등록
- 운영 secret(PostgreSQL·RabbitMQ password 등): Ansible Vault로 암호화 commit (`group_vars/all/vault.yml`)

### 배포 흐름

1. Windows Server → bastion VM(수동 생성) → SSH
2. bastion에서 git clone + Terraform·Ansible 설치
3. `cd deploy/openstack && ./scripts/deploy.sh up`:
   - `terraform apply` — 엔진 3 VM + network·SG + keypair 등록
   - `ansible-playbook playbook-db.yml` — db VM에 Docker·postgres compose up
   - `ansible-playbook playbook-middleware.yml` — mw VM 동일
   - `ansible-playbook playbook-app.yml` — app VM에 코드 sync + alembic upgrade + 앱 compose up
4. 검증: bastion에서 `curl http://<app-vm-ip>:8000/health`

### 디렉토리 구조

```
deploy/openstack/
├── terraform/         # IaC — VM·network·SG·keypair
├── ansible/           # 설정·코드 배포
│   ├── inventory.tpl.yml
│   ├── playbook-*.yml
│   └── roles/
├── compose/           # 분산 compose 3종
│   ├── docker-compose.db.yml
│   ├── docker-compose.middleware.yml
│   └── docker-compose.app.yml
├── scripts/
│   ├── deploy.sh
│   ├── teardown.sh
│   └── update-app.sh
└── README.md
```

루트의 dev·prod compose 파일은 그대로 유지 — 로컬 dev iteration 영향 0.

## Consequences

### 긍정

- 실 운영 패턴 시뮬레이션 — 분산·secret·SG가 prod와 동일 구조
- 한 명령(`deploy.sh up`)으로 인프라 + 설정 + 배포 전체 자동화
- 운영자 책임 분리 (DB·미들웨어·앱 별도)
- 향후 수평 확장(`engine-app` N대) 자연 — Ansible inventory에 추가만
- 로컬 dev는 docker compose 그대로 — 둘 다 같은 컨테이너 이미지·같은 코드

### 부정·한계

- VM 4대 OpenStack quota 부담
- `depends_on: service_healthy`가 VM 간 안 됨 — Ansible playbook 순서로 보장 (db → mw → app)
- migrate 컨테이너는 app VM에서 외부 DB connect → alembic upgrade. 사설망 latency·SG 의존
- 코드 변경 시 `update-app.sh` 한 번이면 되지만, schema 변경 시 migration race 주의 (운영자 의무)

### 미해결 / 영역 밖

- agent VM 배포·파이프라인 검증 (로컬 Lima로 유지)
- 외부 노출·LB·DNS·인증서 (사내망이라 불필요)
- HA·수평 확장 자동화 (현 단계 1차에는 수직 확장)
- 운영 secret store (Vault·Barbican) — 1차 Ansible Vault
- 모니터링·로깅 stack — 별도 ADR

## 관련 문서

- ADR 0005 (DB Schema Alembic 표준화) — migrate 컨테이너 패턴 그대로 활용
- ADR 0002·0004 — broker 토폴로지·진단 워커
- `deploy/openstack/README.md` (운영 절차)
- 로컬 dev: 루트 `README.md` "실행" 섹션·`docs/operations/dev-prod.md`

## 정정 이력

- 2026-05-13: Proposed → 채택. `deploy/openstack/` 디렉토리 구성 완료 (Terraform·Ansible·분산 compose·deploy.sh). 실 OpenStack tenant에서 `./scripts/deploy.sh up` 1회 검증은 운영자 인계 단계.
