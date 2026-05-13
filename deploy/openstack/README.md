# OpenStack Staging 배포 (예상 시나리오)

본 디렉토리 전체(terraform·ansible·compose·scripts·README)는 OpenStack 분산 staging 배포 예상 설계 — 실제 OpenStack tenant 도입 시점에 토폴로지·자원·구성·도구 모두 변경 가능 (ADR 0006 — "예상 시나리오" 상태). 본 시점에는 lima 기반 dev 파이프라인(`docs/operations/lima.md`)만 검증됨.

엔진을 OpenStack 분산 환경에 배포한다는 가정 (ADR 0006). 로컬 dev는 루트의 `docker-compose.yml`·`dev-up.sh` 그대로 사용 — 본 디렉토리는 그것과 무관하게 독립 동작 의도.

OpenStack tenant 실 도입 시 의무: ADR 0006 정정 + 본 디렉토리 구조·코드 새 환경에 맞게 갱신.

## 구성

```
deploy/openstack/
├── terraform/       # IaC — VM 3대(DB·MW·앱) + network·SG·keypair
├── ansible/         # 설정·배포 — VM 안 Docker 설치 + compose up
├── compose/         # 분산 docker-compose 3종 (db / middleware / app)
├── scripts/         # deploy.sh / teardown.sh / update-app.sh
└── README.md        # 본 문서
```

## VM 토폴로지

| VM | 컨테이너 | 권장 spec |
|---|---|---|
| `bastion` (수동 생성) | Terraform·Ansible 실행 host | 1 vCPU · 1 GB · 20 GB |
| `engine-db` | postgres (TimescaleDB) | 4 vCPU · 8 GB · 100 GB |
| `engine-mw` | rabbitmq + redis | 2 vCPU · 4 GB · 40 GB |
| `engine-app` | migrate + web + consumer + diagnostic-worker + diagnostic-scheduler | 2 vCPU · 4 GB · 40 GB |

같은 사내 사설망 안. 외부 노출 없음 (사내 jump host에서 사설 IP로 접속).

## 사전 준비 (1회)

1. Windows Server jump host에서 OpenStack `bastion` VM 1대 수동 생성 (Horizon GUI)
   - image: Ubuntu 22.04 LTS, flavor: 가장 작은 size, keypair 등록
2. bastion에 SSH 접속 후 도구 설치
   ```bash
   sudo apt update && sudo apt install -y git python3-pip docker.io
   pip install --user ansible openstacksdk
   wget https://releases.hashicorp.com/terraform/<버전>/terraform_<버전>_linux_amd64.zip
   unzip terraform_*.zip && sudo mv terraform /usr/local/bin/
   ```
3. 본 repo clone
   ```bash
   git clone <repo-url> && cd <repo>/deploy/openstack
   ```
4. Application Credential 발급 (Horizon → Identity → Application Credentials)
   `~/.config/openstack/clouds.yaml`:
   ```yaml
   clouds:
     assessment-engine:
       auth:
         auth_url: https://<horizon>:5000/v3
         application_credential_id: <id>
         application_credential_secret: <secret>
       region_name: <region>
       interface: public
       identity_api_version: 3
       auth_type: v3applicationcredential
   ```
5. Terraform 변수 설정 — `terraform/terraform.tfvars` (gitignore):
   ```hcl
   image_name        = "Ubuntu-22.04-LTS"        # Horizon Images 메뉴에서 확인
   network_id        = "<existing-network-id>"   # 또는 create_new_network=true
   keypair_name      = "<horizon-에서-등록한-keypair>"
   ```
6. Ansible Vault 비밀번호 설정 — `~/.vault-pass` (chmod 0400, gitignore)
7. secret 파일 작성 (gitignore) — `ansible/group_vars/all/vault.yml` 안 :
   ```yaml
   postgres_password: <강한 random>
   rabbitmq_password: <강한 random>
   ```
   ```bash
   ansible-vault encrypt ansible/group_vars/all/vault.yml --vault-password-file ~/.vault-pass
   ```

## 배포

```bash
export OS_CLOUD=assessment-engine
./scripts/deploy.sh up
```

내부 동작:
1. `terraform apply` — VM 3대 + network·SG·keypair 등록
2. Terraform output → `ansible/inventory.yml` 자동 채움
3. `ansible-playbook playbook-db.yml` — DB VM Docker 설치 + postgres compose up
4. `ansible-playbook playbook-middleware.yml` — MW VM 동일
5. `ansible-playbook playbook-app.yml` — 앱 VM에 코드 sync + alembic upgrade + 앱 compose up

## 검증

bastion에서:
```bash
APP_IP=$(cd terraform && terraform output -raw app_private_ip)
curl http://${APP_IP}:8000/health      # {"status":"ok"}
```

사내 jump host(Windows Server)의 브라우저에서:
- Web UI: `http://${APP_IP}:8000/servers/`
- RabbitMQ 관리: `http://${MW_IP}:15672`

(사내망 사설 IP 라우팅이 jump host에서 가능해야 함 — 운영자 영역)

## 운영

| 작업 | 명령 |
|---|---|
| 전체 기동 | `./scripts/deploy.sh up` |
| 전체 제거 | `./scripts/deploy.sh down` (Terraform destroy) |
| 코드만 재배포 (schema 변경 X) | `./scripts/deploy.sh update-app` |
| 상태 확인 | `./scripts/deploy.sh status` |
| 개별 playbook | `ansible-playbook -i ansible/inventory.yml ansible/playbook-app.yml` |

schema 변경 시: app VM에서 migrate 컨테이너가 `alembic upgrade head` 자동 실행 (ADR 0005). 큰 변경 사전 검토는 `docker compose run --rm migrate alembic upgrade head --sql`로 dry-run.

## 트러블슈팅

| 증상 | 원인 / 대처 |
|---|---|
| `terraform apply` 실패 — quota 초과 | Horizon → Project Limit Summary 확인. 운영자에게 증액 요청 |
| Ansible SSH 연결 실패 | Security Group이 bastion → 엔진 VM 22 허용 확인. keypair private key 경로·권한(0600) 확인 |
| migrate 실패 — `DuplicateTableError` | 기존 schema 있는 DB 위 처음 적용. `docker compose run --rm migrate alembic stamp head`로 transitional |
| 앱 VM에서 DB connect refused | DB VM Security Group이 앱 VM IP 허용 확인. DB compose `ports`가 사설 IP에 노출되어야 |

## 디렉토리별 README

- `terraform/README.md` — variable·output 카탈로그
- `ansible/README.md` — playbook 단위 설명
- `compose/README.md` — 3종 분산 compose의 환경변수 매트릭스
