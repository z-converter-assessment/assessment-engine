# Assessment 산출물 정의

본 portal의 출력단 — 수집된 메트릭·인벤토리를 **사람이 의사결정 가능한 보고서** + **자동화 도구가 입력으로 받을 수 있는 JSON**으로 변환한다.

원형 예시: `docs/zconverter_agent_dashboard_example.html` "보고서 산출물 예시" 절.

---

## 1. 맥락 — 왜 필요한가

### Portal의 lifecycle 위치

```
1. 발견 + 에이전트 배포 (Discovery)
2. 메트릭·인벤토리 수집 (Collection)         ← 현재 구현 핵심
3. 산출물 생성 (Deliverables)                ← 본 문서 정의
   - 보고서: 사람 검토·의사결정용
   - 정제 Inventory JSON: 자동화 입력용
4. 의사결정 (고객·컨설턴트 합의)
5. 변환 실행 (ZConverter Install 등)         ← 별도 문서
```

산출물이 없으면 portal은 "데이터 수집기"에 머무름. Assessment의 deliverable이 곧 마이그레이션 의사결정의 input.

### 청자가 다른 두 보고서

| 양식 | 청자 | 목적 | 정확도·detail |
|------|------|------|---------------|
| 고객 제출용 요약 | 비기술 담당자, 의사결정자 | "마이그레이션 할 것인가, 어디부터" | 위험도 분류·요약 — 정확한 수치보다 판단 보조 |
| 엔지니어 검토용 상세 | 기술 담당자, 컨설턴트 | 병목 분석·이상 패턴 식별 | 정확한 수치·OS/Kernel·I/O·Load 등 raw |

같은 데이터셋, 다른 표현 — 청자별 detail level과 의도가 갈리므로 양식 분리.

### 정제 Inventory JSON의 위치

보고서로 의사결정 → "이 N대를 클라우드로" 결정 → 그 N대를 자동화 도구(OpenStack VM 생성·Terraform·벤더 SDK·ZConverter 변환 도구)에 투입할 표준 입력이 정제 Inventory JSON.

벤더 중립 — OpenStack flavor 같은 특정 vendor 형식 아니라 "VM 생성에 필요한 최소 추상 정보" 수준. 추후 벤더별 어댑터(예: → Terraform HCL, → OpenStack heat)로 변환.

---

## 2. 보고서 — 모양

### 공통 구성

- **출력 포맷**: HTML (브라우저 인쇄·PDF·PPT 캡처 가능). PDF·PPT 직접 export는 추후
- **산출 단위**: "전체 서버" 또는 "선택 서버 N대" (체크박스 다중 선택)
- **데이터 소스**: `server_inventory` (정적) + 최근 N일(기본 7일) `server_metrics` 집계 (avg / max / p95)
- **메타**: 생성 시각, 데이터 기준 기간, 대상 서버 수
- **트리거**: web UI에서 "보고서 생성" 버튼 → 새 페이지 또는 다운로드

### 양식 A — 고객 제출용 요약

**구성 블록 (위→아래)**:
1. 헤더: 보고 일자, 대상 서버 수, 데이터 기준 기간
2. KPI 카드 4개:
   - 대상 서버 수
   - 온라인 (마지막 N분 내 metrics 수신)
   - 주의 필요 (CPU/MEM 평균 ≥ 75%)
   - 고위험 (CPU/MEM 평균 ≥ 90%)
3. 요약 테이블:
   | SERVER | ROLE | OS | CPU AVG | MEM AVG | RISK | 상태 |
4. 한 줄 평가 리스트 (자동 생성 또는 컨설턴트 수기):
   - 패턴 인사이트 (예: "DB 계열 서버 사용률 높음")
   - 권장 액션 힌트 (예: "장기 저사용 서버 — 프로비저닝 조정 검토")
5. 분석 영역 태그 (현황·리소스·위험도·프로비저닝 등)

**RISK 분류 기준** (잠정):
- `low`: CPU avg < 30% AND MEM avg < 50%
- `normal`: 그 외
- `mid`: CPU avg ≥ 75% OR MEM avg ≥ 75%
- `high`: CPU avg ≥ 90% OR MEM avg ≥ 90%

임계값은 운영자가 조정 가능하게 — 향후 설정 화면에서 노출 또는 환경변수.

**ROLE 추론**: 현재 portal에 명시 컬럼 없음. 추론 후보:
- 서비스 분류 (`service_classifier`) 결과 기반 (db/web/cache 등)
- 또는 운영자가 수동 태그 (서버 detail에 role 입력 필드 추가 필요)
- 단기: 서비스 분류 → role 매핑 (DB 서비스 있으면 "database" 등)

### 양식 B — 엔지니어 검토용 상세

**구성 블록**:
1. 동일 헤더 + KPI (또는 생략 — 양식 A로 충분)
2. 상세 테이블:
   | SERVER | ROLE | INTERNAL IP | OS / KERNEL | CPU | MEM | LOAD | I/O WAIT | DISK I/O | 판단 |
   - CPU/MEM은 max값 기준 (병목 식별용)
   - LOAD: load_15m max
   - I/O WAIT: cpu_iowait avg
   - DISK I/O: 합산 read+write KB/s
3. 판단 코멘트 (자동 휴리스틱 또는 수기):
   - "DB 병목 검토" — DB 서비스 + 높은 I/O Wait
   - "저사용률" — 모든 메트릭 낮음
   - "정상" — 임계값 이하
4. 추가 노트 영역 (운영자 free text)

**확장 여지** (향후):
- Hypervisor 정보 (Vagrant/KVM/VMware 식별)
- RAID·multipath
- Container runtime (Docker/Containerd 감지)
- Mount 정보 정규화 (/data·/var/log 분리 분석)

### 데이터 집계 — backend 요구사항

새 service 또는 query_repository 메서드:
- `get_report_aggregate(server_ids, period_days) -> ReportData` — 서버별 CPU/MEM avg·max·p95, load_15m max, iowait avg, disk i/o 합계, online 여부, RISK 분류, role 추론
- 집계는 SQL `time_bucket` + `avg/max/percentile_cont`. server_metrics·server_disk_io에서 N일 범위.
- 캐시 가능 — Redis `report:{hash(ids+period)}` TTL 1h. 운영자가 같은 보고서 여러 번 미리보기 시 hot path.

---

## 3. 정제 Inventory JSON — 모양

### 표준 스키마 (v1)

```json
{
  "inventory_export": {
    "generated_at": "2026-05-10T11:07:20Z",
    "source": "zconverter-assessment-portal",
    "schema_version": "1",
    "servers": [
      {
        "name": "db-server-01",
        "machine_id": "...",
        "role": "database",
        "os": {
          "family": "rocky",
          "version": "9.6",
          "kernel": "5.14"
        },
        "compute": {
          "vcpus": 2,
          "memory_mb": 1024
        },
        "storage": {
          "boot_disk_gb": 64,
          "additional_disks": [
            {
              "mount_hint": "/data",
              "size_gb": 500,
              "fstype": "ext4"
            }
          ]
        },
        "network": {
          "hostname": "db-server-01",
          "internal_ip": "10.0.2.15",
          "external_ip": null
        }
      }
    ]
  }
}
```

### 필드 설계 원칙

- **벤더 중립** — OpenStack flavor·AWS instance type 등 특정 vendor 식별자 없음. 추후 어댑터가 vendor 형식으로 변환
- **VM 생성 최소 정보** — vcpus / memory_mb / boot_disk_gb / additional_disks가 컴퓨팅 인프라 생성에 충분한 4요소
- **서버 식별 — name + machine_id 둘 다** — name은 사람용, machine_id는 자동화·중복 방지
- **schema_version 명시** — 자동화 도구가 호환성 분기 가능
- **null 허용** — 수집 실패 / 미보유 정보는 명시적 null (필드 누락 X — 자동화 도구 파싱 단순화)

### 매핑 — 어디서 가져오는가

| JSON 필드 | source |
|-----------|--------|
| name | `server_inventory.hostname` |
| machine_id | `server_inventory.machine_id` |
| role | service 분류 추론 또는 수동 태그 (보고서와 동일) |
| os.family / os.version / os.kernel | `server_inventory.os_id` / `os_version` / `kernel_version` |
| compute.vcpus | `server_inventory.cpu_cores` |
| compute.memory_mb | `server_inventory.mem_total_kb` ÷ 1024 (반올림) |
| storage.boot_disk_gb | `server_inventory.disks[]` 중 size 가장 큰 disk → bytes ÷ 10⁹ |
| storage.additional_disks[] | 나머지 디스크들 → mount_hint는 `inventory.mounts[]`에서 device 매칭 후 mount path |
| network.hostname | `server_inventory.hostname` |
| network.internal_ip | `server_inventory.ip_internal[0]` |
| network.external_ip | `server_inventory.ip_external[0]` 또는 null |

### 산출 단위·트리거

- 산출 단위: 선택 서버 N대 또는 전체
- 트리거: web UI "JSON Export" 버튼 → 다운로드
- 파일명: `inventory-export-{YYYYMMDD-HHMMSS}.json`
- API: `POST /api/v1/exports/inventory` body `{target_public_ids: [...]}` → `application/json` 다운로드

### 확장 여지 (v2 후보)

- `compute.architecture` (x86_64 / arm64) — `kernel_version` 또는 `uname -m`에서 추출. 현재 미수집 — 에이전트 계약 확장 필요
- `storage.additional_disks[].iops_baseline` — 최근 N일 평균 IOPS. 자동화 도구가 적정 disk type 결정 시 활용
- `services` — 감지된 서비스 목록 (DB·web 등). 자동화 도구가 마이그레이션 후 동일 서비스 자동 설치 가능
- `network.listen_ports[]` — 보안 그룹 자동 설정용

---

## 4. 데이터 신선도·정확성 가정

- **인벤토리**: 1시간 주기 자동 재발행 + 변경 시 즉시. 보고서·JSON 생성 시점 기준 최대 1h stale
- **메트릭 통계**: 최근 N일 (기본 7일). 인벤토리 변경 직후엔 변경 전 데이터 포함 가능 — 보고서 헤더에 "데이터 기준 기간" 명시
- **오프라인 서버**: metrics N분 미수신이면 통계 불완전. 보고서에서 "데이터 부족" 마킹 필요 (CPU AVG = "—" 등)

---

## 5. 결정 사항

세 가지 원칙: (a) 기존 자산 재사용 (b) YAGNI — 필요 시점에 확장 (c) 시계열 집계는 DB(TimescaleDB) 책임.

| 항목 | 결정 | 근거 |
|------|------|------|
| RISK 분류 | **USE Method (Brendan Gregg) + AWS/Azure/GCP 공식 임계값**. 별도 모듈 `web/services/recommendation.py` 신설. 분류 enum: `idle`·`shutdown`·`over_provisioned`·`under_provisioned`·`optimal` | `docs/ai_roadmap.md` §3.B(USE Method) + §3.C(임계값 출처). UI badge 임계값(mapper 90/75)과는 별 도메인 — 시각 신호 vs right-sizing 결정 |
| 통계 단위 | **p95 (Azure 기준)** + peak. avg는 보조. TimescaleDB `percentile_cont(0.95) WITHIN GROUP` | `ai_roadmap.md` §3.B "p95 (Azure 기준) 와 p99.5 (AWS 기준) 둘 다 산출". 평균은 outlier 흡수 — right-sizing 판정엔 p95가 정석 |
| Role 분류 | 자동 (`service_classifier` 결과 매핑). 분류 실패 시 "unknown" | 추가 컬럼·UI·permission 없음. 운영자 수동 태그는 정확도 검증 후 |
| 보고서 PDF/PPT | 브라우저 인쇄 (`print CSS` 정비) | print CSS·`no-print` 이미 존재. 컨설턴트 수동 N건 use case에 백엔드 PDF는 과대. `ai_roadmap.md` §3.F는 WeasyPrint 명시 — 추후 확장 옵션 |
| JSON `additional_disks` 매핑 | `device_filters.find_parent_disk` 조인 (device→mount) | 이미 storage 페이지에서 검증. mount path만으론 LVM·partition·tmpfs 부정확 |
| 산출물 캐시 | 미도입. 성능 측정 후 무거우면 TimescaleDB continuous aggregate | 컨설턴트 수동 트리거 빈도 낮음. 시계열 집계는 Redis 아닌 DB 책임 |

### RISK 분류 — USE Method 임계값 표 (구현 전체 출처)

전체 정의: `docs/ai_roadmap.md` §3.C "룰 기반 추천 엔진".

| 분류 | 조건 | 출처 |
|------|------|------|
| `idle` | CPU peak ≤ 1% AND NET avg ≤ 1KB/s AND 14일 지속 | AWS Compute Optimizer |
| `shutdown` | CPU p95 ≤ 3% AND NET ≤ 2Mbps | Azure Advisor |
| `over_provisioned` | CPU p95 ≤ 30% AND MEM p95 ≤ 50% (headroom 30%) | AWS Compute Optimizer + GCP Recommender |
| `under_provisioned` | swap 사용 OR CPU p95 ≥ 70% OR MEM p95 ≥ 80% | Kleinrock 큐잉 이론(1975) / Google SRE Book / Linux page cache |
| `optimal` | 그 외 | — |

판정 순서: `idle` → `shutdown` → swap 사용(`under_provisioned`로 short-circuit) → `over_provisioned` → `under_provisioned` → `optimal`.

### 1차 구현 범위 (단계적)

`ai_roadmap.md` §3.B 모든 metric을 한 번에 통합하지 않고 단계 분할:

1. **MVP (현재)**: CPU p95/peak + MEM p95/peak + swap 사용 여부 + load_15m max → over/under/optimal 분류
2. **Net I/O 추가**: idle/shutdown 판정 활성 (net_io 집계 SQL 추가)
3. **iowait p95 / disk i/o p95 추가**: USE Method Saturation 차원 보강
4. **Recommendation outcome 추적**: 6개월 후 재평가 (`ai_roadmap.md` §5 평가 루프)

MVP에서 net 의존 분류(`idle`·`shutdown`)는 잠정 미발동 — 분류 결과에 표시되지 않음. 양식 A·B는 over/under/optimal 3종으로 시작.

---

## 6. 구현 순서 권장

1. **데이터 집계 layer** — `query_repository.get_report_aggregate` + `get_inventory_export`. 단위 테스트.
2. **JSON Export 먼저** — 단순 (스키마 변환만). API + 다운로드 + 작은 UI 버튼.
3. **양식 A (고객 요약)** — KPI + 요약 테이블 + RISK 분류. HTML 템플릿.
4. **양식 B (엔지니어 상세)** — 양식 A 위에 상세 테이블 추가.
5. **운영자 수동 입력 (role·평가 코멘트)** — 추후. 일단 자동 추론으로 시작.

---

## 7. 변환 lifecycle 안에서의 의존 관계

본 산출물은 `task-agent-workflow.md`의 입력 측이기도 함:
- 보고서 → 의사결정 → "이 N대 install" → ZConverter Install task 발행
- 정제 JSON → 자동화 파이프라인 → 별도 도구 (Terraform·OpenStack 등)

즉 본 portal이 Assessment 단계의 hub고, 이 산출물이 다음 단계 input 역할.
