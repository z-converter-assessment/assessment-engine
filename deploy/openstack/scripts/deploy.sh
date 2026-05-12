#!/usr/bin/env bash
# OpenStack staging 배포 entrypoint (ADR 0006).
# 사용: ./scripts/deploy.sh {up|down|update-app|status}
#
# 사전 조건:
#   export OS_CLOUD=assessment-engine                    # ~/.config/openstack/clouds.yaml의 cloud 키
#   export OPENSTACK_KEY_PATH=~/.ssh/openstack-key.pem   # Horizon keypair private key
#   ~/.vault-pass 파일 존재 (chmod 0400) — Ansible Vault 비밀번호
#   terraform/terraform.tfvars 작성
#   ansible/group_vars/all/vault.yml 작성 + 암호화

set -euo pipefail

cd "$(dirname "$0")/.."   # deploy/openstack/ 위치

cmd="${1:-up}"

case "$cmd" in
  up)
    echo "[1/4] terraform apply (VM·SG 생성)..."
    (cd terraform && terraform init -upgrade && terraform apply -auto-approve)

    echo "[2/4] inventory.yml 생성 (Terraform output 치환)..."
    DB_IP=$(cd terraform && terraform output -raw engine_db_ip)
    MW_IP=$(cd terraform && terraform output -raw engine_mw_ip)
    APP_IP=$(cd terraform && terraform output -raw engine_app_ip)
    sed -e "s|__DB_IP__|${DB_IP}|g" \
        -e "s|__MW_IP__|${MW_IP}|g" \
        -e "s|__APP_IP__|${APP_IP}|g" \
        ansible/inventory.tpl.yml > ansible/inventory.yml
    echo "  engine-db=${DB_IP} engine-mw=${MW_IP} engine-app=${APP_IP}"

    echo "[3/4] DB·MW 배포 (병렬 불가 — depends_on이 VM 간 안 되므로 순차)..."
    ansible-playbook -i ansible/inventory.yml ansible/playbook-db.yml --vault-password-file ~/.vault-pass
    ansible-playbook -i ansible/inventory.yml ansible/playbook-middleware.yml --vault-password-file ~/.vault-pass

    echo "[4/4] 앱 배포..."
    ansible-playbook -i ansible/inventory.yml ansible/playbook-app.yml --vault-password-file ~/.vault-pass

    echo ""
    echo "완료. Web UI: http://${APP_IP}:8000/servers/"
    echo "      pgAdmin은 OpenStack staging에는 미배포 (psql 직접 또는 사내 GUI 사용)"
    echo "      RabbitMQ 관리: http://${MW_IP}:15672 (assessment / Vault 안 password)"
    ;;

  down)
    echo "VM·SG 제거..."
    (cd terraform && terraform destroy -auto-approve)
    rm -f ansible/inventory.yml
    echo "완료. (postgres_data 볼륨은 OpenStack에 남을 수 있음 — Horizon에서 직접 제거)"
    ;;

  update-app)
    if [ ! -f ansible/inventory.yml ]; then
      echo "오류: ansible/inventory.yml 없음. 먼저 'deploy.sh up'으로 인프라 구성."
      exit 1
    fi
    echo "앱 VM 코드 재배포 (DB·MW 영향 없음)..."
    ansible-playbook -i ansible/inventory.yml ansible/playbook-app.yml --vault-password-file ~/.vault-pass
    ;;

  status)
    (cd terraform && terraform output)
    ;;

  *)
    echo "사용: $0 {up|down|update-app|status}"
    exit 1
    ;;
esac
