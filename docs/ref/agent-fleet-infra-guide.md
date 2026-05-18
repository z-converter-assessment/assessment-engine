# Agent Fleet Infra Repo 가이드라인

본 문서는 본 repo와 별개로 agent 운영 VM들(N대 워커 fleet)을 Terraform + Ansible로 구성하는 외부 인프라 repo를 신설할 때의 가이드라인. 본 repo의 Lima 7 VM dev 매트릭스(`dev/lima/*.yaml` + `scripts/pipeline-up.sh`)와 동일 패턴을 prod에서 재현.

본 문서가 위치한 `docs/ref/`는 본 repo 안 다른 문서·코드에서 인용하지 않는다 — 외부 통합 시 참고만.

## 1. 책임 범위 — 3 repo 관계

| repo | 책임 |
|------|------|
| `assessment-engine` (본 repo) | 엔진 자체 (Python wheel + contract 문서). agent와는 메시지 schema·routing 계약만 |
| `assessment-agent` (별도) | agent C 소스 + agent CI = Linux ELF 바이너리 (GitHub Release 또는 artifact mirror) |
| `cd-assessment-engine` (별도, `cd-repo-guide.md`) | 엔진 prod 운영 — wheel install·systemd unit·env inject |
| `infra-agent-fleet` (본 가이드 대상) | agent 워커 VM N대 provisioning + 바이너리 배포 + systemd unit + 엔진 broker 연결 |

본 가이드 대상 repo는 엔진 운영과 분리. 두 repo는 RabbitMQ broker(엔진 측)·`/zconverter.tar.gz` endpoint(엔진 측) ↔ agent VM(본 repo 측) 네트워크 도달성만 공유.

## 2. dev (Lima) ↔ prod (Terraform+Ansible) 매핑

본 repo의 Lima 7 VM 매트릭스가 시연하는 패턴을 prod에서 동일 재현:

| 본 repo dev (Lima) | prod (infra-agent-fleet) |
|-------------------|------------------------|
| `dev/lima/*.yaml` (VM 정의) | Terraform `*.tf` (VM provisioning) |
| `dev/bin/assessment-agent` (commit 바이너리) | Agent CI artifact 다운로드 |
| `scripts/pipeline-up.sh::start_or_resume_vm` | Terraform `apply` |
| `scripts/pipeline-up.sh::post_provision_vm` | Ansible playbook (서비스 패키지·바이너리·systemd) |
| `dev/agent.env` → `/etc/assessment-agent.env` heredoc 치환 | Ansible Vault + group_vars/host_vars → `/etc/assessment-agent.env` template |
| `host.lima.internal:5672` (broker 호스트) | engine VM의 실제 IP·hostname |

본 repo `docs/development/pipeline.md`가 dev 패턴 단일 진실. prod는 동일 로직을 Terraform·Ansible로 옮김.

## 3. infra-agent-fleet 디렉토리 구조 (추천)

```
infra-agent-fleet/
├── README.md
├── terraform/                              ← VM provisioning (인프라 layer)
│   ├── main.tf                             ← provider·network·SG·VM 정의
│   ├── variables.tf
│   ├── outputs.tf                          ← VM IP·hostname → ansible inventory 입력
│   ├── modules/
│   │   ├── network/                        ← VPC/서브넷/SG
│   │   └── vm/                             ← VM instance 모듈 (재사용)
│   └── environments/
│       ├── staging.tfvars
│       └── prod.tfvars
├── ansible/                                ← OS 측 install·운영 (configuration layer)
│   ├── ansible.cfg
│   ├── inventory/
│   │   ├── staging.yml                     ← Terraform output에서 자동 생성
│   │   └── prod.yml
│   ├── group_vars/
│   │   └── all/
│   │       ├── common.yml                  ← RABBITMQ_*·WORKER_*·INSTALL_BUNDLE_URL 공통
│   │       └── vault.yml                   ← Ansible Vault 암호화 secret
│   ├── host_vars/
│   │   ├── web-server-01.yml               ← 노드별 override (AGENT_EXTERNAL_IP 등)
│   │   └── ...
│   ├── playbooks/
│   │   ├── deploy.yml                      ← 전체 fleet 배포
│   │   ├── update-agent.yml                ← 바이너리 갱신 only
│   │   ├── rotate-secrets.yml              ← env 갱신 only
│   │   └── health-check.yml                ← agent → broker 도달 검증
│   ├── roles/
│   │   ├── prereq/                         ← OS 패키지 install (curl·ca-certificates·OpenSSL runtime)
│   │   ├── agent-fetch/                    ← Agent CI artifact 다운로드 + 서명·sha256 검증
│   │   ├── env-inject/                     ← /etc/assessment-agent.env 작성
│   │   ├── systemd-unit/                   ← assessment-agent.service template + enable·start
│   │   └── service-install/                ← (선택) 시연용 서비스 패키지 install (nginx·redis 등 매트릭스 분배)
│   └── templates/
│       ├── assessment-agent.service.j2
│       └── assessment-agent.env.j2
└── docs/
    ├── runbook.md                          ← 운영 절차·인시던트 대응
    ├── matrix.md                           ← prod 워커 VM 매트릭스 (OS 분포·서비스 매트릭스)
    └── secret-policy.md                    ← Vault 키 회전 정책
```

## 4. 핵심 워크플로

### 4.1. VM provisioning (Terraform)

```hcl
# terraform/main.tf (OpenStack 예시)
resource "openstack_compute_instance_v2" "agent_worker" {
  for_each = var.agent_workers

  name        = each.key
  image_name  = each.value.image
  flavor_name = each.value.flavor

  network {
    name = openstack_networking_network_v2.agent_net.name
  }

  user_data = file("${path.module}/cloud-init/agent-vm.yaml")
}

# terraform/variables.tf
variable "agent_workers" {
  type = map(object({
    image  = string
    flavor = string
    role   = string   # web·db·cache·mq·monitor·app·offline 분류
  }))
}

# environments/prod.tfvars (dev 매트릭스 7 VM 재현)
agent_workers = {
  "web-server-01"     = { image = "Debian-12",   flavor = "m1.tiny",  role = "web" }
  "offline-server-01" = { image = "Debian-13",   flavor = "m1.tiny",  role = "offline" }
  "app-server-01"     = { image = "Ubuntu-24.04", flavor = "m1.small", role = "container" }
  "monitor-server-01" = { image = "Rocky-9",     flavor = "m1.small", role = "monitor" }
  "mq-server-01"      = { image = "Debian-12",   flavor = "m1.tiny",  role = "mq" }
  "cache-server-01"   = { image = "Rocky-9",     flavor = "m1.small", role = "cache" }
  "db-server-01"      = { image = "AlmaLinux-9", flavor = "m1.small", role = "db" }
}
```

`terraform output`이 VM IP·hostname을 Ansible inventory로 입력. dynamic inventory script(`terraform-inventory`·`opentofu-inventory`) 또는 정적 `inventory.yml` 자동 생성.

### 4.2. Agent CI artifact 다운로드 (Ansible)

본 repo `dev/bin/assessment-agent`(commit 바이너리)는 dev 한정. prod는 agent repo의 CI 산출물 활용.

```yaml
# ansible/roles/agent-fetch/tasks/main.yml
- name: Download agent binary from agent repo Release
  ansible.builtin.get_url:
    url: "{{ agent_release_url }}/v{{ agent_version }}/assessment-agent-linux-arm64"
    dest: /tmp/assessment-agent
    checksum: "sha256:{{ agent_sha256 }}"
    mode: '0755'

# 또는 gh CLI
- name: Download via gh CLI
  ansible.builtin.command:
    cmd: >
      gh release download v{{ agent_version }}
      --repo <owner>/assessment-agent
      --pattern 'assessment-agent-linux-{{ arch }}'
      --output /tmp/assessment-agent
```

agent repo의 CI 산출물 형식(arch별 바이너리·sha256·sigstore 등)에 따라 다운로드 절차 달라짐. agent repo의 release contract 별도 정의 의무 (agent repo `docs/operations/release.md` 또는 등가 문서).

### 4.3. env inject (Ansible)

본 repo `dev/agent.env.example` 카탈로그가 키 단일 진실. prod도 동일 키 + 운영 값.

```jinja2
# ansible/templates/assessment-agent.env.j2
RABBITMQ_HOST={{ engine_broker_host }}
RABBITMQ_PORT={{ engine_broker_port | default(5672) }}
RABBITMQ_VHOST={{ rabbitmq_vhost }}
RABBITMQ_USER={{ rabbitmq_user }}
RABBITMQ_PASS={{ vault_rabbitmq_password }}   # Ansible Vault
RABBITMQ_EXCHANGE=assessment
RABBITMQ_ROUTING_KEY_INVENTORY=server.inventory
RABBITMQ_ROUTING_KEY_METRICS=server.metrics
RABBITMQ_ROUTING_KEY_ERROR=server.error
RABBITMQ_WORKER_USER={{ rabbitmq_worker_user }}
RABBITMQ_WORKER_PASS={{ vault_rabbitmq_worker_password }}
WORKER_TASK_EXCHANGE=assessment.tasks
WORKER_TASK_QUEUE_PREFIX=agent.tasks
WORKER_TASK_RESULT_KEY=task.result
WORKER_DOWNLOAD_ALLOWED_HOSTS={{ engine_install_bundle_host }}
AGENT_HOSTNAME_OVERRIDE={{ inventory_hostname }}
AGENT_INTERVAL_SEC=60
{% if agent_external_ip is defined %}
AGENT_EXTERNAL_IP={{ agent_external_ip }}
{% endif %}
```

본 repo dev 측 `RABBITMQ_HOST=host.lima.internal`가 prod에서는 실제 engine VM IP·hostname으로 교체. `engine_broker_host`·`engine_install_bundle_host`는 engine CD repo와 합의된 endpoint.

### 4.4. systemd unit + start (Ansible)

```jinja2
# ansible/templates/assessment-agent.service.j2
[Unit]
Description=Assessment Agent
After=network-online.target

[Service]
User=root
EnvironmentFile=/etc/assessment-agent.env
ExecStart=/usr/local/bin/assessment-agent
Restart=on-failure
RestartSec=10
KillSignal=SIGTERM
TimeoutStopSec=30s

[Install]
WantedBy=multi-user.target
```

본 repo `scripts/pipeline-up.sh`의 systemd unit 정의와 동일. prod에서 user는 `root` 대신 `assessment-agent` system user 의무 (security hardening).

### 4.5. 헬스 검증

```yaml
# ansible/playbooks/health-check.yml
- name: agent → broker 메시지 도달 검증
  hosts: agents
  tasks:
    - name: agent systemd active 확인
      ansible.builtin.systemd_service:
        name: assessment-agent
        state: started
      register: agent_status
      failed_when: agent_status.status.ActiveState != 'active'

    - name: engine 측 RabbitMQ에 메시지 도착 확인 (delegate)
      delegate_to: localhost
      ansible.builtin.uri:
        url: "http://{{ engine_management_host }}:15672/api/queues/%2Fassessment/server.inventory"
        user: "{{ rabbitmq_user }}"
        password: "{{ vault_rabbitmq_password }}"
        force_basic_auth: true
      register: queue_stats
      failed_when: queue_stats.json.messages_published == 0
```

또는 engine web UI `/api/v1/servers`에서 본 fleet의 VM hostname이 등록됐는지 검증.

## 5. 매트릭스 — dev vs prod 차이

| 항목 | dev (본 repo Lima) | prod (infra-agent-fleet) |
|------|-------------------|-------------------------|
| VM 개수 | 7 (시연 매트릭스) | 운영 fleet 크기 (수 ~ 수십~수백) |
| OS 분포 | 5 distro (Debian 12·13·Ubuntu 24.04·Rocky 9·AlmaLinux 9) | 운영 환경에 맞춤 (단일 OS or 다양) |
| 합성 부하 | yaml provision의 synthetic-load.timer | 실 워크로드 (운영자 환경에서 자연 발생) |
| swap-trigger | app-server-01 강제 (under_provisioned 시연) | 운영 시 자연 swap 사용 패턴 |
| agent-restart-demo | web-server-01 3분 주기 restart | 운영 시 정상 운영 (restart 없음 default) |
| offline-once | offline-server-01 1회 발행 후 stop | 운영 시 의도적 fleet 종료 시점에 자연 발생 |
| broker host | `host.lima.internal` | engine VM 실제 IP·hostname |

dev 매트릭스의 시연용 인공 패턴(swap-trigger·restart-demo·offline-once)은 prod에서 제거. 정상 운영 default.

## 6. 본 repo와의 인터페이스 (계약)

infra-agent-fleet repo가 본 repo에서 받는 것:

| 자산 | 위치 | 용도 |
|------|------|------|
| agent.env 키 카탈로그 | 본 repo `dev/agent.env.example` | RABBITMQ_*·WORKER_*·AGENT_* 키 단일 진실 |
| 메시지 schema | 본 repo `docs/architecture/agent.md` | agent → engine routing key·payload 형식 |
| RabbitMQ topology | 본 repo `docs/architecture/rabbitmq.md` | vhost·exchange·queue 정의 |
| systemd unit 패턴 | 본 repo `scripts/pipeline-up.sh::post_provision_vm` 안 inline 예시 | prod template ref |
| install bundle endpoint | 본 repo web의 `/zconverter.tar.gz` | task.install 시점 fetch URL |

infra-agent-fleet repo가 agent repo에서 받는 것:

| 자산 | 용도 |
|------|------|
| Linux ELF 바이너리 (arch별) | `/usr/local/bin/assessment-agent`로 install |
| 바이너리 sha256·signature | 무결성 검증 |
| agent semver tag | Ansible variable `agent_version` |

본 repo와 agent repo 둘 다 read-only로 활용. infra-agent-fleet repo가 두 repo source clone할 필요 0.

## 7. 도구 선택 가이드

| 도구 조합 | 적합 환경 |
|----------|---------|
| Terraform + Ansible (추천) | 멀티 클라우드·온프레미스 혼합. IaC + 구성 분리 |
| OpenTofu + Ansible | Terraform fork. 라이선스 자유도 필요 시 |
| Pulumi + Ansible | Python/TypeScript로 IaC 작성 선호 시 |
| SaltStack 단독 | 대규모 fleet, master·minion 구조 적합 |
| 단순 cloud-init + systemd | 5~10 VM 이하 작은 fleet, IaC 없이 cloud provider UI 직접 |

본 repo dev와 동일 패턴(Lima yaml 단일 진실 + post-provision shell)을 prod에서 가장 직접 재현하는 게 Terraform + Ansible. 따라서 추천.

## 8. 운영 체크리스트 (prod 배포 직전)

- [ ] engine repo prod 환경 가동 — broker(:5672)·web(:8000) 도달 가능
- [ ] engine VM의 RABBITMQ broker user·vhost·exchange 사전 생성
- [ ] agent repo의 release tag 결정 + Ansible variable `agent_version` 명시
- [ ] Terraform `apply` — VM provisioning + 네트워크
- [ ] Terraform output → Ansible inventory 자동 생성
- [ ] Ansible Vault에 RABBITMQ_PASSWORD·WORKER_PASSWORD 저장
- [ ] `ansible-playbook deploy.yml --check` (dry-run)
- [ ] staging 환경에서 헬스 검증 통과
- [ ] prod 적용
- [ ] engine web UI `/servers/`에서 본 fleet의 VM 모두 등록 확인
- [ ] 60초 주기 메트릭 갱신 확인
- [ ] `journalctl -u assessment-agent`에서 broker 연결·publish 정상 확인

## 9. 시작 시 참고할 본 repo 문서

순서대로:
1. 본 repo `docs/development/pipeline.md` — dev 7 VM 매트릭스 단일 진실 (prod 패턴의 dev 시연)
2. 본 repo `dev/agent.env.example` — agent env 키 카탈로그
3. 본 repo `docs/architecture/agent.md` — 메시지 schema·routing
4. 본 repo `docs/architecture/rabbitmq.md` — broker topology
5. 본 repo `scripts/pipeline-up.sh` `post_provision_vm` 함수 — systemd unit·env inject reference
6. agent repo의 release contract (별도 정의 의무)
7. engine CD repo의 broker·endpoint contract (`cd-repo-guide.md`)

본 repo·agent repo와 별개 lifecycle. infra-agent-fleet repo가 source clone할 필요 0 — 위 ref 문서만으로 충분.

## 10. 책임 분리 한눈

```
engine repo (assessment-engine)
   │
   │ release artifact (wheel·sdist·SBOM·signature)
   │ + contract 문서 (env.md·prod-contract.md·deployment.md)
   v
engine CD repo (cd-assessment-engine, 별도)
   │
   │ engine prod 가동 — broker·web·etc. endpoint 제공
   v
agent repo (assessment-agent, 별도)
   │
   │ Linux ELF 바이너리 (arch별, sha256·signature)
   │ + agent.env.example 카탈로그 (engine repo가 보유)
   v
infra-agent-fleet (본 가이드 대상)
   │
   │ Terraform: VM provisioning
   │ Ansible: 바이너리 install + systemd + env inject
   v
N개 워커 VM (운영자 환경)
   │
   │ agent publish → engine broker
   v
engine이 수집·진단·시각화
```

본 가이드는 위 그림의 가장 아래 layer 책임. 다른 layer와의 인터페이스는 release artifact + contract 문서만.
