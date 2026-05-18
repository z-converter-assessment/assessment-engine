# 정제 Inventory Export

정책: CLAUDE.md #F10 (평가 윈도우 정합). 본 문서는 JSON Export 스키마·정제 원칙·자동화 도구 매핑 단일 진실. 운영자가 web UI "JSON Export"로 선택 서버 N대의 정제 inventory를 표준 JSON으로 다운로드 — 자동화 도구(Terraform / OpenStack Heat / Ansible / CSP SDK)가 그대로 입력해 후속 마이그레이션 실행.

## 1. 사용처 (정제 방향 결정 근거)

본 양식은 4가지 사용처를 동시에 만족하도록 설계.

### A. VM 생성 입력 (Terraform / OpenStack Heat / Ansible / CSP SDK)
자동화 도구가 target 클라우드에 VM을 생성할 때 필요한 최소 명세. vcpus / memory / boot disk / additional disks / network / OS family·version.

### B. Right-sizing 결정 (AWS Compute Optimizer / Azure Advisor / GCP Recommender)
적정 instance type을 결정하려면 현재 사용량 통계가 필수. cpu_p95 / mem_p95 / peak / load 평균 등. 본 프로젝트는 이미 USE Method 보고서로 계산 중 — export에 같이 담아서 자동화 도구가 별도 측정 없이 바로 결정.

### C. Security Group / Firewall Rule 자동 생성
target 클라우드의 SG·방화벽 룰을 자동 생성하려면 listen_ports[] 정보가 필수. proto / port / service 카테고리.

### D. 평가 보고서 첨부 (보조)
컨설턴트가 고객에게 평가 자료로 제출. 운영자가 시점·범위 인지하려면 envelope 메타(exported_at / period_days) 필요.

## 2. 정제 원칙

| 원칙 | 의미 |
|------|------|
| 벤더 중립 | 특정 CSP의 instance type·flavor 식별자 박지 않음. 자동화 도구가 size_class 추천을 받아 자기 도메인 instance type으로 매핑 |
| 최소 정보 | VM 생성·right-sizing·SG 자동화에 꼭 필요한 필드만. 운영 부수 정보(uptime·process 트리 등)는 제외 |
| 측정값 포함 | 통계 기반 right-sizing 데이터(p95/peak)를 export에 같이 담음. 자동화 도구가 별도 측정 안 해도 결정 가능 |
| 표준 명명 | `mount_point` / `vcpu_count` / `addresses[]` 등 Terraform·OpenStack·CSP SDK 표준 어휘에 가깝게 |
| Schema versioning | envelope에 `schema_version` + `exported_at` + `source` + `assessment_period_days` 필수. 양식 진화 시 자동화 도구가 분기 처리 |

## 3. 스키마 정의

### Envelope (최상위 응답)

```json
{
  "inventory_export": {
    "schema_version":   "3",
    "schema_doc":       "docs/architecture/inventory-export.md",
    "engine_id":        "zconverter-assessment-portal",
    "exported_at":      "2026-05-12T03:45:00Z",
    "period_window": {
      "days":  14,
      "start": "2026-04-28T03:45:00Z",
      "end":   "2026-05-12T03:45:00Z"
    },
    "size_class_guide": {
      "under_provisioned": "instance type 상향 (vCPU·RAM 증가)",
      "over_provisioned":  "instance type 축소 (vCPU·RAM 감소)",
      "idle":              "운영 종료 또는 통합 검토 — 사용 거의 없음",
      "shutdown":          "운영 종료 검토 — 사용 0에 근접",
      "optimal":           "변경 불필요 — 적정 사양",
      "insufficient_data": "데이터 부족 — 평가 기간 안 메트릭 부재"
    },
    "servers": [ /* 항목 N개 */ ]
  }
}
```

| 필드 | 타입 | 의미 |
|------|------|------|
| `schema_version` | string | 본 양식 버전 식별자. 양식 진화 시 자동화 도구가 분기 처리. 값은 코드 단일 진실 (`src/assessment_engine/web/routers/exports.py`) |
| `schema_doc` | string | 본 문서 경로 — 자동화 도구가 매핑 규약 참조 시 |
| `engine_id` | string | 본 프로젝트 식별자 (`"zconverter-assessment-portal"`) |
| `exported_at` | ISO 8601 UTC | export 시점 — reproducibility 기준 |
| `period_window.days` | int | right-sizing 통계 기준 기간 (대시보드와 동일 — `recommendation.WINDOW_DAYS` 참조) |
| `period_window.start`/`end` | ISO 8601 UTC | 통계 윈도우 양 끝 |
| `size_class_guide` | object | `recommended_size_class.key` -> 한국어 조치 가이드. 자동화 도구가 매핑 시 참조 |

### Server 항목

```json
{
  "machine_id":  "a1b2c3...",
  "hostname":    "web-server-01",
  "role":        "web",
  "last_seen_at": "2026-05-12T03:42:15Z",

  "services": [
    {
      "category": "web",
      "unit": "nginx.service",
      "listeners": [
        {"port": 80,  "proto": "tcp", "address": "0.0.0.0"},
        {"port": 443, "proto": "tcp", "address": "0.0.0.0"}
      ]
    }
  ],

  "os": {
    "family":  "ubuntu",
    "version": "22.04",
    "kernel":  "5.15.0-101-generic"
  },

  "compute": {
    "vcpu_count":              4,
    "memory_mb":               16384,
    "cpu_p95_pct":             35.2,
    "cpu_peak_pct":            72.1,
    "mem_p95_pct":             68.4,
    "mem_peak_pct":            81.0,
    "load_15m_max":            2.3,
    "swap_used":               false,
    "recommended_size_class": {
      "key":   "optimal",
      "label": "적정"
    }
  },

  "storage": {
    "boot_disk_gb":              30,
    "iops_baseline":             120,
    "iops_p95":                  280.0,
    "iops_peak":                 540.0,
    "throughput_kbps_baseline":  850.0,
    "throughput_kbps_p95":       2100.0,
    "throughput_kbps_peak":      4800.0,
    "additional_disks": [
      {"mount_point": "/data", "size_gb": 100, "fstype": "xfs"}
    ]
  },

  "network": {
    "addresses": [
      {"scope": "internal", "family": "v4", "address": "10.0.1.15"},
      {"scope": "external", "family": "v4", "address": "54.123.45.67"}
    ],
    "rx_kbps_baseline":  1200.0,
    "rx_kbps_p95":       2800.0,
    "rx_kbps_peak":      5400.0,
    "tx_kbps_baseline":  2400.0,
    "tx_kbps_p95":       4900.0,
    "tx_kbps_peak":      9200.0
  }
}
```

### 사용처별 필드 매핑

| 사용처 | 필수 필드 |
|--------|----------|
| A. VM 생성 | `os.*`, `compute.{vcpu_count, memory_mb}`, `storage.{boot_disk_gb, additional_disks[*]}`, `network.addresses[]` |
| B. Right-sizing | `compute.{cpu_p95_pct, cpu_peak_pct, mem_p95_pct, mem_peak_pct, load_15m_max, swap_used, recommended_size_class}`, `storage.iops_*`, `storage.throughput_kbps_*`, `network.rx_kbps_*`, `network.tx_kbps_*` (baseline·p95·peak) |
| C. Security Group | `services[].{category, listeners[].{port, proto, address}}` |
| D. 보고서·감사 | envelope 전체 + `machine_id`, `hostname`, `role`, `last_seen_at` + `period_window`로 reproducibility |

## 4. 자동화 도구별 매핑 가이드

벤더 중립 export → 도구별 매핑은 도구 측 책임. 본 절은 주요 export 필드 → 도구 리소스 신호용.

| export 필드 | Terraform | OpenStack Heat | Ansible |
|------------|-----------|----------------|---------|
| `compute.recommended_size_class` | `aws_instance.instance_type` 매핑 테이블 | `OS::Nova::Server.flavor` 매핑 | `when: size_class != 'idle'` 조건 분기 |
| `storage.boot_disk_gb` | `root_block_device.volume_size` | `OS::Cinder::Volume`(boot) | — |
| `storage.additional_disks[]` | `aws_ebs_volume` + attachment per item | `OS::Cinder::Volume` + attachment per item | — |
| `services[].listeners[]` | `aws_security_group.ingress` per port | `OS::Neutron::SecurityGroupRule` | apt/yum 패키지 설치 (`os.family`별) |
| `network.addresses[]` | `aws_network_interface.private_ips` | `OS::Neutron::Port.fixed_ips` | dynamic inventory 그룹 |
| `os.family`/`os.version` | AMI selection 보조 | image selection 보조 | inventory 그룹 분류 |

CSP SDK 직접 호출(boto3·azure-mgmt·google-cloud-compute)은 자동화 도구 미사용 시 컨설턴트가 SDK 스크립트로 N대 일괄 생성 — JSON을 dict load 후 SDK 인자 직접 매핑.

## 5. 정제 원칙별 결정 근거

### 벤더 중립 vs CSP 특정 instance type
- 결정: 벤더 중립 유지. `recommended_size_class` 5종(idle/shutdown/over_provisioned/under_provisioned/optimal)만 노출
- 근거: 같은 export JSON을 AWS / Azure / GCP / OpenStack 어디든 입력 가능해야 함. 도구·CSP별 매핑은 도구 측 책임 (단순 dict lookup)
- 트레이드오프: 도구마다 매핑 테이블 필요 — `m5.large` 같은 직접 값보다 한 단계 변환 비용

### 측정값 포함 — period 기준
- 결정: envelope `period_window.days` 기본 7. 운영자가 endpoint에서 변경 가능 (1~30일). 보고서 라우터 기본 14는 별도 (F11 단일 진실 — `recommendation.WINDOW_DAYS`).
- 근거: AWS Compute Optimizer 기본 14일 / Azure Advisor 7일. 단기 export는 7일도 의미 있음
- 데이터 부족: 윈도우 안 metrics가 적은 신규 서버 — p95·peak 필드가 null. 자동화 도구는 null이면 size_class 추천만 사용

### 멀티 NIC·multi-IP
- 결정: `network.addresses[]` 배열. scope(internal/external) + family(v4/v6) 명시
- 근거: 실제 운영 환경에 NIC 2개 이상 또는 v4+v6 dual stack 있음. 단일 `internal_ip`만 노출하면 손실

### Schema versioning
- 결정: envelope에 `schema_version` 필수. 현재 값은 코드 단일 진실 (`src/assessment_engine/web/routers/exports.py`). 외부 자동화 도구는 본 값을 비교해 분기 처리.
- 근거: 양식 진화 시 옛 도구가 silent break 안 되도록 분기 진입점 명시.

## 6. 추후 추가 권고 필드 (현재 contract 미수집)

다음 필드는 자동화 가치 분명하나 현재 agent contract 부재. contract 확장 후 별도 라운드 도입.

| 필드 | 사용처 | 도입 조건 |
|------|--------|----------|
| `os.arch` | x86_64 vs arm64 — 다른 instance family 결정 (AWS Graviton 등) | 에이전트가 `uname -m` 수집·발행 |
| `compute.cpu_model_family` | "Skylake" / "Graviton" 등 generation 매칭 | 에이전트가 `/proc/cpuinfo` family·model 발행 |
| `storage.io_pattern` | "random" / "sequential" — SSD vs HDD 결정 | sectors_read/written + io_time 활용. 엔진 측 계산 가능 — 별도 라운드 |

도입 절차:
1. 에이전트 의존(`os.arch`, `cpu_model_family`)은 `assessment-agent` contract 확장 합의 + `agent_version` major bump.
2. 엔진 자체 계산(`io_pattern`)은 본 프로젝트 SQL·service 추가만으로 가능 — 별도 결정.
3. 도입 시 3절(스키마 본문)에 승격 + 본 절에서 제거.

## 관련 문서

- CLAUDE.md #E1·#E3 — Web 표시 계층 원칙·mapper 단일 변환
- USE Method 보고서: `docs/architecture/web/services.md` `recommendation.py` (right-sizing 분류 단일 진실)
- 에이전트 contract: `docs/architecture/agent.md` (export 입력이 되는 inventory 스키마)
