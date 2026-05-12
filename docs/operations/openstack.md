# OpenStack Staging 배포

ADR 0006 채택 — 사내 폐쇄망 OpenStack tenant에 엔진을 4 VM 분산 배포한다. 로컬 dev(`docker-compose.yml` + Vagrant)와 무관하게 독립 동작.

본 문서는 docs/ 진입점. 실제 절차·디렉토리 구조·트러블슈팅은 `deploy/openstack/README.md` 단일 진실. 배포 결정·옵션 비교·VM 토폴로지 근거는 `docs/adr/0006-openstack-staging.md`.

## VM 토폴로지 요약 (ADR 0006)

| VM | 컨테이너 | 역할 |
|----|----------|------|
| `bastion` (수동 생성) | (없음 — Terraform·Ansible 실행 host) | 운영자 진입점 |
| `engine-db` | postgres (TimescaleDB) | DB |
| `engine-mw` | rabbitmq + redis | 미들웨어 |
| `engine-app` | migrate + web + consumer + diagnostic-worker + diagnostic-scheduler | 애플리케이션 (stateless) |

같은 사내 사설망. 외부 노출 없음 — 사내 jump host에서 사설 IP로 접속.

## 배포 흐름

```bash
# bastion VM에서
cd deploy/openstack

# 사전 준비
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
cp ansible/group_vars/all/vault.yml.example ansible/group_vars/all/vault.yml
ansible-vault encrypt ansible/group_vars/all/vault.yml --vault-password-file ~/.vault-pass

export OS_CLOUD=assessment-engine
export OPENSTACK_KEY_PATH=~/.ssh/openstack-key.pem

./scripts/deploy.sh up           # 인프라 + DB + MW + 앱 전체 배포
./scripts/deploy.sh update-app   # 코드만 재배포
./scripts/deploy.sh down         # 전체 제거
```

## 운영 결정 (ADR 0006 + #A2)

- 운영 secret은 Ansible Vault로 암호화 commit (`group_vars/all/vault.yml`) — 로컬 dev의 `.env` 평문 / prod의 Docker secrets와 다른 secret 채널 (#F12).
- `migrate` 컨테이너는 app VM에서 외부 DB로 connect 후 `alembic upgrade head` 실행 (#C4 ADR 0005). 사설망 latency·Security Group 의존 — VM 간 네트워크 검증 필수.
- pgadmin은 staging 미배포 — 운영자가 사내 GUI 또는 psql 직접 사용 (#A2 컨테이너 구성).
- depends_on service_healthy가 VM 간 안 됨 — Ansible playbook 순서로 보장 (db -> mw -> app).
- VM 4대 OpenStack quota 부담 — 신규 환경 도입 전 quota 확인.

## 영역 밖 (현 ADR 미해결)

- agent VM 배포·파이프라인 검증은 로컬 Vagrant 유지 (`docs/operations/pipeline.md` + `docs/operations/vagrant.md`)
- 외부 노출·LB·DNS·인증서 (사내망이라 불필요)
- HA·수평 확장 자동화 (1차 수직 확장)
- 운영 secret store (Vault·Barbican) (1차 Ansible Vault)
- 모니터링·로깅 stack (별도 ADR 예정)

## 관련 문서

- `deploy/openstack/README.md` — 디렉토리 구조·절차·트러블슈팅 단일 진실
- `docs/adr/0006-openstack-staging.md` — 채택 결정·옵션 비교·VM 토폴로지 근거
- `docs/operations/dev-prod.md` — dev/staging/prod secret 정책 매트릭스
- `docs/adr/0005-db-schema-management.md` — migrate 컨테이너 패턴 (모든 환경 단일 진실)
