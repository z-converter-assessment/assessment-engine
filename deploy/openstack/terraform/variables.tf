variable "cloud_name" {
  type        = string
  default     = "assessment-engine"
  description = "~/.config/openstack/clouds.yaml의 cloud 키. OS_CLOUD env로도 override 가능."
}

# --- VM 공통 ---

variable "image_name" {
  type        = string
  description = "엔진 VM image 이름. Horizon → Images 메뉴에서 확인 (예: 'Ubuntu-22.04-LTS')."
}

variable "keypair_name" {
  type        = string
  description = "Horizon에서 등록한 keypair 이름. SSH private key는 bastion에 별도 보관."
}

variable "network_id" {
  type        = string
  description = "엔진 VM이 attach할 사설망 id (운영자 발급). 같은 network 안에서 VM 간 사설 IP 통신."
}

# --- flavor (운영자 환경에 맞춰 채움) ---

variable "db_flavor" {
  type        = string
  description = "engine-db VM flavor — 권장 4 vCPU · 8 GB RAM · 100 GB disk"
}

variable "mw_flavor" {
  type        = string
  description = "engine-mw VM flavor — 권장 2 vCPU · 4 GB RAM · 40 GB disk"
}

variable "app_flavor" {
  type        = string
  description = "engine-app VM flavor — 권장 2 vCPU · 4 GB RAM · 40 GB disk"
}

# --- 명명 ---

variable "name_prefix" {
  type        = string
  default     = "assessment-engine"
  description = "VM·SG name prefix. 같은 project에 여러 staging 띄울 때 충돌 방지용."
}
