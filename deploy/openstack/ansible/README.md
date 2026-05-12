# Ansible — VM 설정·앱 배포

ADR 0006의 엔진 3 VM에 Docker 설치 + 코드 배포 + docker compose up 자동화.

## 파일

| 파일 | 역할 |
|---|---|
| `inventory.tpl.yml` | inventory 템플릿. Terraform output 기반 `inventory.yml` 자동 생성 (`scripts/deploy.sh`) |
| `playbook-db.yml` | DB VM — Docker 설치 + postgres compose up |
| `playbook-middleware.yml` | MW VM — rabbitmq + redis compose up |
| `playbook-app.yml` | 앱 VM — 코드 sync + alembic upgrade + 앱 compose up |
| `roles/docker/` | Docker 설치 공통 role (모든 VM) |
| `roles/compose-deploy/` | docker compose 파일 배포 + up 공통 role |
| `group_vars/all/vault.yml` | Ansible Vault 암호화 secret (postgres·rabbitmq password 등) |

## 사용

```bash
# Terraform output 기반 inventory.yml 자동 생성 후
ansible-playbook -i inventory.yml playbook-db.yml --vault-password-file ~/.vault-pass
ansible-playbook -i inventory.yml playbook-middleware.yml --vault-password-file ~/.vault-pass
ansible-playbook -i inventory.yml playbook-app.yml --vault-password-file ~/.vault-pass
```

위 순서는 의무 — DB·MW healthy 후 앱이 connect. depends_on이 VM 간 안 되니 playbook 실행 순서로 보장.

`scripts/deploy.sh up`이 자동으로 위 순서대로 호출.

## Vault

secret 편집:
```bash
ansible-vault edit group_vars/all/vault.yml --vault-password-file ~/.vault-pass
```

내용 예시:
```yaml
postgres_password: <강한 random>
rabbitmq_password: <강한 random>
```

playbook이 본 변수를 사용해 VM에 `/run/secrets/*` 파일 생성 후 docker compose secrets 마운트.

## 향후

- 앱 VM 수평 확장 — inventory에 `engine-app-2`·`engine-app-3` 추가 + LB 설정 (별도 ADR)
- 모니터링·로깅 stack playbook 추가 (별도 ADR)
