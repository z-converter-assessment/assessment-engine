# right-sizing API 프로비저닝 통합 — 데이터 모델 개선 계획 (확정)

성격: 내부 구현 계획 메모(삭제 자유). 외부 공유용 아님 — 엔진 코드 경로/심볼 명시.
목적: right-sizing API 를 "판단 + 목표"에서 "프로비저닝 self-contained(현재 스펙 + 판단 + 목표)"로 키우는 방향을
확정하고, 어디까지 지금 가능하고 어디부터 에이전트 데이터 대기인지 티어로 못박는다. 이후 piecemeal 변경 없이 본 계획대로.

관련: 에이전트가 보낼 스토리지 레이아웃 계약 = `unified-resource-data-model.md` 2.8절(block_devices/lvm_vgs 정규화 그래프).
본 계획의 "보류" 티어가 그 계약이 실현돼야 풀린다.

## 0. 결정 — 방향 A (통합)

right-sizing API 하나가 "이 서버를 우리 판단대로 프로비저닝"에 필요한 전부를 준다: 현재 스펙 + 자원별 판단 + 목표.
지금은 판단(right-sizing API)과 스펙(JSON inventory export)이 갈라져 소비자가 public_id 로 조인해야 하고 두 응답이
어긋날 위험이 있다. A 는 이를 없앤다. 구현은 export 스펙 빌더(`web/services/mappers/export.py`)를 재사용 —
로직 중복 0, 함수 1개·소비처 2개(export·right-sizing).

## 1. 용어 확정 (혼동 제거)

swap 은 세 축으로 분리한다. 지금까지 코드가 다룬 "swap"은 3번(포화 신호)뿐이고, 프로비저닝이 쓰는 건 1번(디스크 배정)이다.

| 개념 | 의미 | 소속 | 데이터 |
|---|---|---|---|
| swap 할당 크기 | 디스크에 스왑으로 준 용량 | 스토리지/디스크 스펙 | 있음 (`swap_total_kb`, inbound/outbound DTO) |
| swap 위치 | 스왑 파티션 vs swapfile, 어느 디스크 | 스토리지 레이아웃 | 없음 (에이전트 대기) |
| swap 사용(page-out) | 메모리 압박 발생 신호 | 메모리 포화 (사이징) | 있음 (`mem_swap_paging`, right-sizing 반영됨) |

swap 할당은 메모리가 아니라 디스크 소비 -> spec 의 storage 쪽에 `swap_gb`. 사용 신호(page-out)만 `resources.memory.saturation` 유지.
OS 중립: Linux swap == Windows pagefile 를 같은 `swap_gb` 로, 유형 구분은 레이아웃에서.

## 2. 데이터 모델 개선 지점 — 티어

대시보드가 이미 수집·저장하는 것(인벤토리: disks/mounts/interfaces/services/os/cpu_cores/mem_total/swap_total,
메트릭: cpu/mem/disk/net 시계열 + saturation raw) 기준. 프로비저닝 관점·자원적정성분류 관점 둘 다 표기.

### 티어 1 — 즉시 (데이터 있음, 노출만. 에이전트 무관)

| 항목 | 관점 | 소스(있음) | 배치 |
|---|---|---|---|
| swap 할당 크기 `swap_gb` | 프로비저닝 | `swap_total_kb` (outbound DTO) | `resources.disk`/spec.storage |
| OS 상세 (version·distro(os_id)·kernel·arch) | 프로비저닝(이미지 선택) | inventory os_* (export 에 os_version 있음) | spec.os |
| boot/data 디스크 분해 (boot_gb + additional[]) | 프로비저닝 | export `_split_disks` 로직 | spec.storage |
| 네트워크 addresses (v4/v6·internal/external·인터페이스) | 프로비저닝(네트워크 부착) | export `_network_addresses` | spec.network |
| boot mode (BIOS/UEFI) | 프로비저닝 | os_id 에 힌트(`alma8bios`/`alma8uefi`) — 확정 필드화 검토 | spec.os |

분류 관점 티어1: 현행 분류(ADR 0052 rollup_host)는 cpu/mem/disk/net 로 완결 — swap 할당·OS 상세는 스펙이지 분류 신호가 아니라 분류엔 추가 불요.
단 참고 신호로 "swap 미구성(swap_total=0)인데 메모리 압박"은 신뢰도/맥락 노트로 쓸 여지 있음(후순위, 필수 아님).

### 티어 2 — 중간 (export 에 있음, spec 블록으로 통합. 로직 재사용)

- spec 블록 신설: `spec.os` / `spec.network` / `spec.storage(boot_gb, additional[], swap_gb)`.
- export 스펙 빌더를 공용 함수로 뽑아 export·right-sizing 이 공유(중복 0). export 응답은 유지(하위 호환), right-sizing 이 그 스펙을 임베드.

### 티어 3 — 보류 (에이전트 데이터모델 확정 대기)

`unified-resource-data-model.md` 2.8절 스토리지 그래프 계약이 실현돼야 풀림:

- 정확한 스토리지 레이아웃 — `block_devices[]`(name·type·size·fstype·mount·parent) + `lvm_vgs[]`(free_bytes). fs->물리디스크 확정 매핑, 확장 여력(VG free) 실측.
- swap 위치 — 파티션 vs swapfile, 어느 디스크.
- Windows pagefile 크기·위치, Storage Spaces(가상 디스크 pool), 드라이브 레터·볼륨 구조.
- LVM/RAID/암호화 계층 배지.

이 티어 들어오면: spec.storage 의 `layout[]` 채움 + "미할당"을 "확장 가능 여력(VG free)"로 승격 + swap 위치.

## 3. 목표 응답 형태 (확정 시)

```
servers[]:
  hostname, public_id, primary_ip, online, os_family
  classification, root_cause, recommendation{summary,kind,actions[],suppressed[]}, confidence_notes
  resources:
    cpu    : { status, utilization_p95_pct, saturation, current_cores, sizing_target_cores, ... }
    memory : { status, utilization_p95_pct, saturation(page-out), current_mb, sizing_target_mb, ... }
    disk   : { capacity{ status, current_gb, sizing_target_gb, worst_mount, days_until_full },
               io{ status, saturation },
               swap_gb,                       # 티어1 (할당 크기)
               layout[] }                     # 티어3 (에이전트 대기)
    network: { status, signals }
  spec:                                       # 티어2 — 프로비저닝 재현용 (export 빌더 재사용)
    os      : { family, version, id, kernel, arch, boot_mode }
    storage : { boot_gb, additional[], swap_gb }
    network : { addresses[] }
```
(disk.swap_gb 와 spec.storage.swap_gb 는 하나로 둘지 결정 — spec.storage 에 두는 게 "재현 스펙" 의미상 자연.)

## 4. 구현 원칙 (실행 시)

- additive — export·기존 right-sizing 필드 유지, spec/swap 추가만. 소비자 하위 호환.
- 스펙 로직 단일 진실 — export 빌더 재사용(디스크 분해·네트워크 addresses·OS). right-sizing 이 재계산하지 않음.
- 계약 변경 체인(#F9 "메시지 페이로드 schema 변경" / inbound 컬럼 추가)은 티어3(에이전트 신규 필드) 때만. 티어1/2 는 엔진 내부 노출이라 payload 계약 무변.
- 문서: `/reference/api` 상세 페이지가 계약 단일 소유(routers.md 는 pointer). 티어별로 응답 필드·enum·예시 갱신.
- 테스트: `tests/unit/test_right_sizing_api.py` 계약 테스트에 swap_gb·spec 블록 추가.

## 5. 실행 게이트

- 티어1·2: 에이전트 무관 — 승인 시 언제든. (swap_gb 노출 + spec 블록 통합 = 한 작업 단위.)
- 티어3: 에이전트가 `unified-resource-data-model.md` 2.8절 스토리지 그래프를 구현·발행한 뒤. 그 전엔 spec.storage.layout 은 미포함(추론값을 실측인 척 노출 금지 — F12 현황 선언).

## 6. 미결 결정 (실행 전 확정할 것)

1. swap_gb 위치 — `resources.disk.swap_gb` vs `spec.storage.swap_gb` (재현 스펙 의미상 후자 권장).
2. spec 블록을 right-sizing 에 임베드 vs export 유지 + 링크 — A 채택이니 임베드. export 는 하위 호환 유지.
3. boot_mode 를 os_id 파생(추론) vs 에이전트 확정 필드 — 추론이면 티어1, 확정 필드면 티어3.
