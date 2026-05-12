# Terraform — OpenStack 인프라

ADR 0006 4 VM 토폴로지(bastion 제외 — 수동 생성) 중 엔진 3 VM(`engine-db`·`engine-mw`·`engine-app`) + 사설망 SG + keypair 등록을 자동화.

## 파일

| 파일 | 역할 |
|---|---|
| `main.tf` | provider 선언 + VM·network·SG resource |
| `variables.tf` | image·flavor·network·keypair 등 input variable |
| `outputs.tf` | 각 VM의 사설 IP — Ansible inventory에 주입 |
| `versions.tf` | Terraform·provider 버전 pin |
| `terraform.tfvars` | (gitignore) 실제 값 |

## 사용

```bash
export OS_CLOUD=assessment-engine   # ~/.config/openstack/clouds.yaml의 cloud name

terraform init
terraform plan
terraform apply -auto-approve
```

## 주요 variable

| 이름 | 의미 |
|---|---|
| `image_name` | Ubuntu 22.04 LTS image (Horizon Images 메뉴 이름 그대로) |
| `db_flavor` | 4 vCPU·8 GB |
| `mw_flavor` | 2 vCPU·4 GB |
| `app_flavor` | 2 vCPU·4 GB |
| `network_id` | 기존 사내 사설망 id (운영자 발급) — 미설정 시 신규 생성 |
| `keypair_name` | Horizon에서 등록한 keypair 이름 |

## 정리

```bash
terraform destroy
```

본 디렉토리는 인프라(VM·network·SG)만 책임. Docker 설치·compose up은 Ansible 영역.
