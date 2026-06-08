# JSON Export

본 문서는 JSON Export 산출물(정제 inventory + 사용량 통계를 자동화 도구 입력용 표준 JSON으로 다운로드)의 존재 의의·구현 의도·근거를 정리한다. 스키마·필드·정제 원칙·자동화 도구 매핑 deep dive는 `docs/architecture/web/export-schema.md` 별도.

## 위치

- UI 진입점: 대시보드 list 페이지에서 N대 선택 → "Export" 버튼 → 다운로드
- API: `POST /api/exports/inventory` (envelope JSON)
- 산출물 형태: 단일 JSON 파일 — 선택 서버 N대의 정제 inventory + 사용량 통계 + 평가 윈도우 메타

## 존재 의의

본 엔진이 수집한 inventory·메트릭을 외부 자동화 도구(Terraform·OpenStack Heat·Ansible·CSP SDK)가 직접 입력할 수 있는 표준 형식으로 제공. 다음 질문에 답한다.

질문 1: "이 N대를 다른 환경(클라우드 등)으로 마이그레이션하려면 instance type을 어떻게 결정하나?"

각 서버의 vCPU·메모리·디스크 raw spec + 7일 사용량 통계(p95·peak)가 JSON 한 파일에 묶임. Terraform·Heat·CSP SDK가 그대로 받아 `instance_type` lookup table과 매핑 → 신규 환경 sizing 결정.

질문 2: "마이그레이션 전 자원 적정 산정에 측정값 기반 근거가 있어야 한다."

사용량 통계 p95·peak가 raw spec과 같이 들어가므로 다운사이즈·동등 sizing·업사이즈 어느 쪽도 측정값 근거로 결정 가능. "우리 추측"이 아닌 "7일 측정"이 기준.

질문 3: "Ansible inventory·Terraform tfvars로 N대 일괄 자동화하려면?"

JSON 안 표준 명명(`mount_point`·`vcpu_count`·`addresses[]` 등 Terraform·OpenStack·CSP SDK 표준 어휘에 가깝게)으로 정제됨. 별도 가공 없이 자동화 도구 입력. `docs/architecture/web/export-schema.md` 자동화 도구 매핑 표 참조.

## 산출 정보

JSON envelope (요약 — 자세한 스키마는 architecture/web/export-schema.md):

```json
{
  "period_window": {"days": 14, "start": "...", "end": "..."},
  "size_class_guide": {...},
  "servers": [
    {
      "hostname": "db-01",
      "role": "db",
      "os_id": "ubuntu",
      "os_version": "22.04",
      "vcpu_count": 4,
      "mem_total_gb": 16,
      "disks": [{"mount_point": "/", "total_gb": 100, "used_gb": 45}, ...],
      "addresses": [{"type": "internal", "ip": "10.0.0.1"}],
      "usage": {
        "cpu_p95_pct": 12.3,
        "cpu_peak_pct": 45.1,
        "mem_p95_pct": 35.0,
        "mem_peak_pct": 60.2,
        ...
      },
      ...
    },
    ...
  ]
}
```

핵심 메타:
- `period_window` — 평가 윈도우 (CLAUDE.md #F10 단일 진실. JSON envelope·표제 명시 의무)
- `size_class_guide` — 분류 임계값 reference (자동화 도구가 같은 임계로 결정 가능)

## 자동화 도구 호환

`docs/architecture/web/export-schema.md` "자동화 도구 매핑" 표 참조. 주요 매핑:

| export 필드 | Terraform | OpenStack Heat | Ansible |
|------------|-----------|----------------|---------|
| `vcpu_count` | `instance_type` lookup | `flavor` lookup | inventory var |
| `mem_total_gb` | (instance_type 결정 보조) | (flavor 결정 보조) | inventory var |
| `disks[].total_gb` | `volume_size` | `volume.size` | playbook var |
| `addresses[].ip` | `private_ip` | `port.fixed_ip` | inventory host |

단일 export JSON을 AWS·Azure·GCP·OpenStack 어디든 입력 가능 — 도구·CSP별 매핑은 도구 측 책임 (단순 dict lookup).

## 의사결정 근거

표준 명명 정합:
- `vcpu_count`·`mem_total_gb`·`mount_point` 같은 명명은 Terraform·OpenStack·CSP SDK 표준 어휘. JSON 받는 측이 추가 변환 없이 그대로 활용 — 자동화 step 1개 줄임.

period_window envelope:
- 자동화 도구가 "이 데이터가 언제 측정됐나"를 알면 stale 판단·재수집 결정 가능
- envelope 메타로 두면 도구가 일관 처리 (각 server 안에 박지 않음 — 중복·오류)
- CLAUDE.md #F10 평가 윈도우 단일 진실 원칙과 정합

size_class_guide envelope:
- 본 엔진이 측정값으로 분류한 결과(under/over/idle/optimal)를 자동화 도구가 같은 기준으로 검증 가능
- 도구가 자체 임계를 갖지 않고 본 엔진의 USE Method 기준 그대로 차용

## 보고서 양식 A/B와의 분기 의도

| 항목 | JSON Export | 보고서 (양식 A·B) |
|------|------------|------------------|
| 형식 | JSON (machine-readable) | HTML SSR (human-readable) |
| 의도 | 자동화 도구 입력 | 운영자·고객 의사결정 |
| 사용자 | Terraform·Heat·Ansible·CSP SDK | 컨설턴트·엔지니어·고객 |
| 가공 | 정제·표준 명명 | KPI·위험도·자동 진단 텍스트 |

같은 데이터 source(`server_inventory` + 시계열 통계)지만 산출 형태·사용자가 다름. 보고서는 "사람이 읽는다", JSON Export는 "도구가 받는다".

## 한계

1. instance type 직접 매핑 X — JSON에는 raw spec만 (`vcpu_count`·`mem_total_gb`). 실제 `t3.medium`·`m5.large` 결정은 자동화 도구 측 책임 — 도구가 자체 lookup table 보유 의무.
2. 시간 흐름 export 미지원 — 단일 시점 snapshot만. 시계열 export(같은 서버의 7일 분포)는 별도 endpoint·도구로 (`/api/charts/...`).
3. PII·secret 노출 위험 — inventory에 hostname·internal IP 박힘. 외부 도구 입력 시 sanitize 의무는 외부 인프라 책임 — 본 엔진은 원본 데이터 그대로 export.
4. 평가 윈도우 7일 default — `?period_days=N`으로 override 가능 (1~90일). 정책 단일 진실은 CLAUDE.md #F10.
5. 자동화 도구 매핑은 reference만 — 실제 도구가 본 매핑을 따른다는 보장 없음. 도구 측 변환 코드 검증 의무.

## 관련 문서·코드

- `docs/architecture/web/export-schema.md` — JSON Export 스키마·필드 카탈로그·정제 원칙·자동화 도구 매핑 단일 진실
- `docs/products/environment-report.md` / `server-report.md` — 같은 데이터 source 의 사람용 출력
- `src/assessment_engine/web/routers/exports.py` — Export endpoint
- `src/assessment_engine/web/services/query_service.py::get_inventory_export` — 데이터 build
- CLAUDE.md #F10 — 평가 윈도우 단일 진실
