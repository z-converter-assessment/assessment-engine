# 스토리지 계층 데이터 수집 개선 요청 (엔진 -> 에이전트)

> 협의용 입력 문서. 엔진(Assessment Portal)이 VM 스토리지를 "배정 / 파일시스템 / 확장 여력" 계층으로
> 평가하기 위해 에이전트가 추가로 긁어와 주면 좋을 데이터 요청. self-contained — 에이전트 측 컨텍스트만으로 읽힘.

## 1. 배경 / 목적

엔진은 각 VM의 스토리지를 세 계층으로 나눠 용량·right-sizing 평가를 하려 한다:

- 배정 블록 = OpenStack 이 이 VM 에 준 물리(virtio) 디스크 크기.
- 파일시스템 = 실제 마운트되어 쓰는 df 용량.
- 확장 여력 = 배정됐지만 아직 파일시스템이 아닌 공간 (LVM VG 여유·미파티션).

지금 데이터로는 배정(물리 디스크)·파일시스템(마운트)까지만 실측되고, 중간 계층(파티션·LVM)과
"확장 여력의 성격"은 추론에 그친다. 이걸 실측으로 올리는 게 목적.

## 2. 현재 에이전트가 보내는 것 (기존 계약)

- `disks[]`: `{name, size_bytes, type, major, minor, kind}` — 물리 디스크(예: `vda`). `kind` 물리/가상 분류.
- `mounts[]`: `{mount, fstype, total_bytes, major, minor, kind}` (inventory. `free/avail` 은 metrics). `kind`=data/boot/image.
- fs <-> 디스크 연결: `mount.major/minor` 를 `disk.major/minor` 와 매칭 (major/minor 조인).

## 3. 지금 못 하는 것 (갭)

관측 실례 — Linux VM (vda 40GB):

```
vda        252:0   40.0G   disk            <- 배정 블록 (physical)
├─vda1     252:1    0.6G   part  vfat  /boot/efi
├─vda2     252:2    1.0G   part  xfs   /boot
└─vda3     252:3   38.4G   part
  └─dm-0   253:0   16.4G   lvm   xfs   /    <- LVM 논리 볼륨 위
```

1. fs -> 디스크 확정 연결 불가: `/` 는 dm(major 253) 위라 디스크 vda(major 252)와 major 가 안 맞아
   major/minor 조인이 끊긴다. `/` 가 어느 물리 디스크 위인지 현재 데이터로는 모름.
2. 확장 여력의 성격 불명: 배정 40GB - 파일시스템(16.4+1.0+0.6=18GB) = 22GB 가 LVM VG 여유(디스크
   추가 없이 `/` 확장 가능)인지, 미파티션인지, 오버헤드인지 구분 불가. "22GB 여유"까진 알아도
   "그 중 얼마를 바로 붙일 수 있나"는 모름.
3. 중간 계층 노드(파티션·논리 볼륨) 자체가 페이로드에 없음 (disks 는 물리만).

## 4. 요청 데이터

### 4.1 Linux — 블록 트리 + LVM

블록 트리 (예: `lsblk -J -b -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINT,MAJ:MIN,PKNAME`):

- `TYPE`: `disk` / `part` / `lvm` / `crypt` / `raid` — 계층 타입 확정 라벨.
- `PKNAME`: 부모 디바이스명 — 부모 체인으로 fs -> LV -> PV -> partition -> disk 추적. (major/minor 추론 폐기.)
- 노드별 `SIZE`(bytes), `FSTYPE`, `MOUNTPOINT`, `MAJ:MIN`.

LVM (있을 때만; `vgs`/`lvs --units b --nosuffix` 또는 LVM API):

- VG: `{name, size_bytes, free_bytes}` — `free_bytes` 가 "확장 가능 여력".
- (선택) LV: `{name, vg, size_bytes}`.

### 4.2 Windows

- `Get-Disk` / `Get-Partition` / `Get-Volume` (또는 WMI `Win32_DiskDrive`/`Win32_DiskPartition`/`Win32_LogicalDisk`):
  - 물리 디스크(크기) -> 파티션 -> 볼륨(드라이브 레터·fs·크기) 계층 + 부모 링크.
- Storage Spaces 사용 시 가상 디스크(storage pool) 계층 노출.

## 5. 이걸로 열리는 것

- 3계층 실측: 배정 블록 / 파일시스템 / 확장 가능-미할당(VG free) 을 추론 없이 산출.
- fs -> 물리 디스크 확정 매핑 (부모 체인). major/minor 추론·조인 폐기.
- 계층 타입 배지(파티션 직접 / LVM / 암호화) 정확 표시.
- actionable 신호: "이 VM 은 40GB 배정 중 18GB 파일시스템, VG 여유 22GB -> `/` 에 바로 확장 가능".

## 6. 우선순위 / 스코프

- 필수(높은 가치): VG `free_bytes` + 부모 체인(`PKNAME`/`TYPE`). 이 둘이면 3계층이 완성된다.
- 선택(엔지니어 디테일, 후순위): 개별 LV 전체 트리, 암호화/RAID 상세, 스냅샷.

## 7. 계약 / 호환 고려

- 추가는 additive — 기존 `disks[]`/`mounts[]` 유지하고 새 필드(부모 링크·TYPE) 또는 새 섹션(`lvm`/`block_tree`)만 추가.
  엔진 입력 모델이 `extra=ignore` 라 비대칭 배포(에이전트 먼저 배포)에도 안전.
- Windows `major/minor=null` 관례 유지 — 부모 링크는 별도 키(이름 기반)로.
- 페이로드 증가는 VM 당 블록 노드 수(보통 수~수십 개) 수준 — 무시할 규모.

## 8. 지금 엔진 측 진행 방향 (참고)

에이전트 개선 전까지 엔진은 기존 데이터(배정=물리 디스크 / 파일시스템=마운트 / 미할당=배정-파일시스템 추론)로
표현을 먼저 구현한다. 위 데이터가 들어오면 "미할당" 을 "확장 가능 여력(VG free)" 로 승격하고 계층 배지를 실측화한다.
