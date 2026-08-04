# 프로비저닝 어세스먼트 API 계약

소스 서버를 관측해 재해복구/마이그레이션 대상 VM을 만드는 데 필요한 정보를 한 응답으로 제공하는 계약이다. 소비자(재해복구/마이그레이션 에이전트)는 이 응답을 보고 타겟 VM을 재현(소스 레이아웃 그대로 복제)하거나 수정 사이징(관측 부하에 맞춰 조정)해서 생성한다.

이 문서는 소비자가 코드를 작성하는 기준이 되는 계약의 단일 진실이다. 엔드포인트, 요청, 응답 구조, 필드 의미, 단위, 불변식, 엣지 동작, 버전 규약을 담는다.

## 1. 설계 원칙

안전이 최우선이다. 우선순위는 안전(대상이 부하를 못 받는 상황을 절대 만들지 않음) > 정확(관측 반영) > 절감(부차)이다. 이 API의 산출물은 재해복구, 하이퍼바이저 변경 등 다양한 목적에 쓰이므로 자원 절감은 목표가 아니다. 사이징이 애매하면 항상 안전한 쪽(더 큰 크기, 소스 유지)으로 기운다.

산출물은 플랫폼 중립이다. 원시 자원 수량(vCPU 개수, 메모리 MiB, 디스크 GiB)을 낸다. 오픈스택 플레이버나 특정 클라우드 인스턴스 타입으로의 매핑은 소비자 몫이다. 엔진은 타겟 클라우드를 알 필요가 없고, 오픈스택이 아닌 사설망에서도 그대로 동작한다.

재현 범위는 레이아웃 청사진이다. 이 응답은 타겟에서 스토리지 계층(파티션 테이블, LVM/RAID 조립, 파일시스템 컨테이너)과 OS/네트워크 구성을 그대로 재구성하는 데 필요한 사실을 제공한다. 파일시스템의 실제 데이터 내용(파일, 설치된 패키지 바이트)은 이 API의 범위가 아니다 - 엔진은 인벤토리와 메트릭만 저장하고 디스크 데이터를 보유하지 않는다. 데이터 이관은 소비자의 별도 채널(블록 복제, rsync, 백업 복원 등)이 담당한다. 즉 이 응답은 "빈 그릇을 소스와 똑같은 모양으로 만드는 방법"을 주고, "그릇을 채우는 것"은 소비자가 자기 복제 채널로 한다.

## 2. 계약 규약 (never-change)

이 계약은 파괴적 변경을 하지 않는다. 진화는 항상 덧붙이는 방식(additive)이다.

버전:
- 응답 최상위 `contract_version`이 `"major.minor"` 형식이다.
- minor 상향은 필드/enum 값/사이징 축 추가만 한다(기존 필드 삭제, 의미 변경, 타입 변경 없음).
- major 상향은 파괴적 변경이며 사전 고지한다.

소비자 버전 의무:
- 파싱 전에 `contract_version`을 먼저 검사한다.
- major가 소비자가 아는 major와 다르면 응답을 거부한다(파싱 중단, 운영자 경보). 의미 드리프트를 조용히 흡수하지 않는다.
- 상위 minor는 수용한다(모르는 추가 필드/축은 아래 규칙으로 처리).

소비자 필드/enum 처리:
- reproduction/diagnostics 계열의 모르는 필드는 무시해도 안전하다.
- sizing은 예외다. sizing.axes는 반드시 전 원소를 순회한다 - 모르는 축(axis 값)이라도 건너뛰지 않고 `max(current, recommended)`로 프로비저닝한다. 한 사이징 축을 무시하면 그 자원을 통째로 누락해 under-provision이 되므로, "모르는 필드 무시" 규약은 sizing.axes 안에서는 적용하지 않는다.
- 모르는 action 값을 만나면 action을 해석하려 하지 말고 `max(current, recommended)`를 적용한다. 모르는 action을 `keep`(현재값 유지)으로 강등하면 실제로 증설이 필요한 축을 축소하는 셈이라 금지한다.
- 모르는 enum 값(reproduction 계열: os_family, block_device.type, part_type, id_type, interface.kind, bond_mode, lvm_segtype 등)은 미상(null 동급)으로 처리하고, 4.3의 null 폴백(해당 세부는 기본값 처리 또는 블록 레벨 복제)을 따른다. os_family가 미상이면 이미지를 추측하지 말고 재현을 보류하며 경고를 노출한다.
- 표시 enum(confidence, estimate_quality)의 모르는 값은 가장 보수적인 등급(confidence=low, estimate_quality=uncertain)으로 취급한다.

필드 존재 대 값:
- 구조(필드 존재)는 지금 확정한다. 문서화된 모든 키는 항상 존재하며, 아직 안 채워진 값은 `null`로 나온다(키 생략 아님). 소비자는 항상 `null`을 처리한다.

## 3. 요청

```
GET /api/assessment
```

식별자로 서버를 고른다. 소비자는 자기 인벤토리가 아는 이름이나 IP로 조회하며, 내부 식별자(public_id)를 몰라도 된다.

| 파라미터 | 타입 | 설명 |
|----------|------|------|
| `hostname` | string (쉼표 다건) | 호스트명 정확 매칭(대소문자 구분) |
| `ip` | string (쉼표 다건) | 인터페이스 IP 주소 매칭(물리/가상 인터페이스 무관, IPv4/IPv6 무관) |
| `public_id` | string (쉼표 다건) | 서버 public_id(UUID). 있으면 유일 지정 |
| `pair` | string (쉼표 다건) | 순서쌍 `hostname~판별자`(판별자=IP 또는 public_id). 동명 호스트를 하나로 지정 |
| `window_days` | int, 기본 14, 최소 14, 최대 90 | 평가 창(일). 14 미만 불가 - 임계가 14일 창에 맞춰져 있어 짧은 창은 관측 부족으로 과소 사이징을 유발한다. 연장(14 초과)만 안전 방향 |
| `end` | ISO 8601 datetime | 평가 창 종료 시각(기본 현재) |

식별자를 하나도 안 주면 등록된 전체 서버를 반환한다. 여러 조건은 합집합(하나라도 매칭이면 포함)이다.

호스트명은 유일하지 않다. 동명 호스트를 하나로 지정하려면 `pair=hostname~ip`로 두 조건을 동시에 만족시킨다. 응답의 `warnings`가 동명 충돌과 해석 실패를 알려준다.

파라미터 검증 실패(window_days 범위 밖, 형식 오류 등)는 HTTP 422를 반환한다(FastAPI 표준 `{"detail": [...]}` 형태). 매칭 서버 0건은 오류가 아니라 `count: 0`, `servers: []`다(404 아님).

## 4. 응답

### 4.1 최상위 구조

```json
{
  "contract_version": "1.0",
  "generated_at": "2026-07-10T05:00:00Z",
  "window": { "days": 14, "start": "...", "end": "...", "basis": "관측 창 근거 설명" },
  "filter": { "hostname": [], "ip": [], "public_id": [], "pair": [] },
  "warnings": {
    "ambiguous_hostnames": ["동명이 2대 이상이라 필터가 여러 서버에 명중한 호스트명"],
    "unresolved_pairs": ["해석 실패한 순서쌍"],
    "unmatched_filters": ["어떤 서버에도 매칭 안 된 필터 토큰"]
  },
  "count": 1,
  "servers": [ /* 4.2 */ ]
}
```

매칭 서버가 없으면 `count: 0`, `servers: []`를 반환한다(404 아님).

타임스탬프(generated_at, window.start/end)는 ISO 8601 UTC 형식이다 - 실제 출력은 `+00:00` 오프셋에 소수초를 포함할 수 있다(예 `2026-07-10T05:00:00.123456+00:00`). 예시의 `...Z`/무소수초 표기는 가독성 축약이니, 소비자는 `Z`만 가정하지 말고 표준 ISO 8601 파서를 쓴다.

### 4.2 서버 항목

두 계층으로 나뉜다. 결정에 필요한 것(identity/reproduction/sizing/assessment)이 1급이고, 진단/감사용 상세는 diagnostics로 분리한다.

```json
{
  "identity":     { /* 아래 */ },
  "reproduction": { /* 4.3 - 어떻게 재현하나 */ },
  "sizing":       { /* 4.4 - 얼마 크기로 */ },
  "assessment":   { /* 4.5 - 판정/신뢰도 */ },
  "diagnostics":  { /* 4.6 - 근거/힌트(선택 소비) */ }
}
```

identity:
```json
"identity": {
  "public_id": "3b1e...", "hostname": "app-lin-01", "hostname_ambiguous": false,
  "primary_ip": "10.50.3.15", "os_family": "linux", "online": true
}
```

### 4.3 reproduction - 재현 팩트

타겟 VM을 소스와 동일하게 재구성하는 데 필요한 원시 사실이다. 재현 프로비저닝은 여기(와 sizing.axes의 current)를, 수정 프로비저닝은 여기(와 sizing.axes의 recommended)를 쓴다.

```json
"reproduction": {
  "os": {
    "family": "linux", "id": "ubuntu", "version": "22.04", "codename": "jammy",
    "kernel": "5.15.0-91-generic",
    "arch": "x86_64",                     // x86_64 | aarch64 | ... - 이미지 ISA 분기. null이면 추측 금지(7절)
    "bits": 64,                           // 32 | 64 | null
    "boot_firmware": "uefi",              // uefi | bios | null - 부팅 방식. null이면 추측 금지(7절)
    "secure_boot": false,                 // bool | null
    "edition": null,                      // Windows EditionID(SKU 코드, 예 ServerDatacenter) | null(Linux)
    "timezone": "Asia/Seoul",             // IANA tz | null
    "rtc_utc": true                       // RTC가 UTC인가(true) localtime인가(false) | null
  },
  "boot": {
    "kernel_cmdline": "BOOT_IMAGE=... root=UUID=a1b2c3 ro",   // string | null
    "root_ref_type": "uuid",              // uuid | label | partuuid | path | null - root= 참조 방식
    "grub_install_target": "virtio-pci-0000:00:05.0"          // 부트로더 설치 디스크 id | null
  },
  "network": {
    "interfaces": [{
      "id": "fa:16:3e:e6:f4:57", "id_type": "mac",   // 안정 식별자(조인 키). MAC 부재 시 ifguid/by-path
      "name": "ens3", "kind": "physical",            // physical | bond_master | bond_member | vlan | ...
      "mtu": 1500,
      "addresses": [
        { "address": "10.50.3.15", "prefix": 24, "family": "ipv4", "origin": "static" }  // origin: static | dhcp | null
      ],
      "gateway": "10.50.3.1",
      "dns": ["10.50.0.2"],
      "routes": [{ "dest": "10.60.0.0/16", "via": "10.50.3.254" }],   // default 외 static route
      "bond_mode": null,                             // lacp | active-backup | balance-rr | balance-xor | broadcast | balance-tlb | balance-alb | null
      "vlan_id": null,
      "speed_mbps": null
    }]
  },
  "storage": {
    "block_devices": [{
      "id": "virtio-pci-0000:00:05.0", "id_type": "by-path",   // 안정 식별자(트리 조인 키)
      "name": "vda", "type": "disk",                           // disk | part | lvm | raid | crypt | mpath | dynamic | volume | swap (그 외 문자열 pass-through)
      "parent": null,                                          // 부모 노드의 id (root=null). 다중 부모면 노드 반복
      "size_bytes": 32212254720,

      // --- disk 노드 (물리/가상 디스크) ---
      "partition_table": "gpt",                                // gpt | mbr | null
      "sector_size": 512,                                      // 512 | 4096 - 파티션 오프셋 해석 기준
      "serial": "SN123456", "wwn": "0x5000...",                // 디스크 식별
      "rotational": false,                                     // true=HDD, false=SSD - 볼륨 타입 힌트

      // --- part 노드 (파티션) ---
      "part_number": 1,
      "part_start_bytes": 1048576,                             // 시작 오프셋 - 정확한 파티션 레이아웃 재현
      "part_type": "0fc63daf-8483-4772-8e79-3d69d8477de4",     // GPT=소문자 무중괄호 GUID / MBR=0x hex(예 0x83)
      "part_name": "root",                                     // GPT 파티션 레이블
      "part_flags": ["boot"],                                  // ["boot","esp","bios_grub","lvm","raid",...]

      // --- 파일시스템 (fs가 얹힌 노드) ---
      "fstype": "ext4", "fs_uuid": "a1b2c3", "fs_label": "root",
      "block_size": 4096,
      "mountpoint": "/",
      "mount_options": ["rw", "relatime"],                     // fstab 마운트 옵션
      "fs_freq": 0,                                            // fstab 5번째 필드(dump)
      "fs_passno": 1,                                          // fstab 6번째 필드(fsck 순서: root=1, 나머지=2)

      // --- lvm 노드 (논리 볼륨) ---
      "lvm_vg": null, "lvm_lv": null,
      "lvm_segtype": null,                                     // linear | striped | mirror | thin | ...
      "lvm_stripes": null, "lvm_stripe_size_kib": null,

      // --- raid 노드 (md) ---
      "raid_level": null,                                      // 0 | 1 | 5 | 6 | 10
      "raid_chunk_kib": null, "raid_metadata": null,           // "1.2" 등
      "raid_uuid": null,

      // --- crypt 노드 (LUKS) ---
      "crypt_type": null                                       // luks1 | luks2 (구조만, 키 없음)
    }],
    "lvm_vgs": [{
      "name": "vg0", "vg_uuid": "...",
      "size_bytes": 0, "free_bytes": 0,                        // 확장 여력
      "extent_size_bytes": 4194304,                            // PE 크기 - LV 재생성 기준
      "pv_ids": ["partuuid:..."]                               // 구성 PV(block_device id 참조)
    }]
  },
  "mounts": [{
    "source": "nfs-srv:/export/data", "target": "/mnt/data",   // 비블록 마운트(block_devices에 없는 것)
    "fstype": "nfs", "options": ["rw"], "fs_freq": 0, "fs_passno": 0
  }]
}
```

트리 조인 규약: block_devices는 평면 목록이고 부모-자식은 `parent`가 부모의 `id`를 참조해 표현한다. `name`(vda, dm-0 등)은 표시용이고 유일하지 않으므로 조인은 반드시 `id`로 한다. 한 디바이스가 부모가 여럿이면(스트라이프 등) `(id, parent)` 쌍으로 여러 항목이 나올 수 있으며, 이때 `size_bytes`는 `id`당 한 번만 계산한다.

레이아웃 정확 복제: 이 목록만으로 소스 스토리지 계층 구조를 그대로 재구성할 수 있다. 디스크(파티션 테이블, 섹터 크기) -> 파티션(번호, 시작 오프셋, 타입 GUID, 플래그) -> LVM/RAID/crypt 조립(VG extent, LV 세그먼트, 스트라이프, RAID 레벨/청크, LUKS 타입) -> 파일시스템(타입, UUID, 레이블, 블록 크기) -> 마운트(경로, 옵션, fstab 필드) 순으로 트리를 따라 내려가며 복원한다. 필드가 `null`이면 그 세부는 미수집(6절 채움 상태)이며, 소비자는 해당 항목을 기본값으로 처리하거나 블록 레벨 복제로 폴백한다. block_devices에 없는 마운트(tmpfs, nfs, cifs, bind, 9p 등)는 `mounts[]`에 별도로 담아 fstab 재생성에 쓴다.

fstab 재생성 범위: 엔진은 마운트 팩트(경로, fstype, 옵션, UUID, dump/passno)를 제공한다. 이 팩트로 fstab을 재생성하는 것은 소비자 몫이다 - 엔진이 완성된 fstab 파일을 내지는 않는다.

### 4.4 sizing - 크기 결정

현재 스펙(재현용)과 권고 스펙(수정용)을 축별로 나란히 준다. 사이징 축은 배열이다 - 소비자가 전 원소를 순회해 각 자원을 프로비저닝한다. 새 자원 축(예: gpu)이 추가돼도 배열 원소로 도착하므로, 순회하는 소비자는 코드 변경 없이 자동으로 반영한다.

```json
"sizing": {
  "axes": [
    { "axis": "cpu",    "current": 1,    "recommended": 2,    "unit": "vcpus", "action": "increase", "estimate_quality": "exact" },
    { "axis": "memory", "current": 2048, "recommended": 2048, "unit": "mib",   "action": "keep",     "estimate_quality": "exact" },
    { "axis": "disk",   "mountpoint": "/",     "device_ref": "partuuid:aaaa-1", "current": 30,  "recommended": 30,  "unit": "gib", "action": "keep",     "estimate_quality": "exact", "used_pct": 42.3, "runway_days": null, "note": null },
    { "axis": "disk",   "mountpoint": "/data", "device_ref": "partuuid:bbbb-2", "current": 100, "recommended": 150, "unit": "gib", "action": "increase", "estimate_quality": "exact", "used_pct": 88.1, "runway_days": 45, "note": null }
  ]
}
```

축 원소 공통 필드: `axis`, `current`, `recommended`, `unit`, `action`(increase | keep | decrease), `estimate_quality`(exact | floor | uncertain).
- `cpu`, `memory`: 호스트당 하나. 추가 필드 없음. 관측 근거(활용률/포화)는 호스트 단위라 diagnostics.resources에서 본다.
- `disk`: 마운트별 하나(멀티볼륨 토폴로지를 담기 위함). `mountpoint`(사람 참조), `device_ref`(reproduction.storage.block_devices[].id 참조, 트리 조인용)에 더해 마운트별 관측 근거를 축 원소에 직접 싣는다 - diagnostics.resources의 disk 축은 호스트 단위 하나뿐이라 per-mount 근거를 담을 수 없기 때문이다. 추가 필드: `used_pct`(현재 사용률 %, float|null), `runway_days`(현 증가율 기준 소진까지 남은 일수, int|null - 미측정이거나 증가 추세 없으면 null), `note`(크기로 안 풀리는 신호나 특기사항 문자열|null, 예 inode 소진).

소비자 소비 규약: axes를 전부 순회하고, 각 축을 `max(current, recommended)` 크기로 프로비저닝한다. `axis` 값으로 자원 종류를 구분하고, 모르는 axis라도 같은 규약을 적용한다(2절).

불변식 (계약 레벨 보증):
- under 축은 `recommended >= current`를 절대 위반하지 않는다. under인데 정확 수치 산정이 불가능한 경우(포화 주도 등)에도 안전한 한 단계 상향 바닥값을 채우고 `estimate_quality: "floor"`로 표기한다. `action: "increase"`인데 `recommended == current`인 조합은 나오지 않는다.
- sizing.axes에 존재하는 모든 축은 `recommended`가 non-null이다(최소 current 폴백) - `max(current, recommended)`가 null을 만나지 않는다. 판정 유보(미측정/표본 부족) 축은 `recommended = current` + `estimate_quality: "uncertain"`. base 수량(cpu 코어 수 / 총 RAM / disk 총 용량)이 미상인 자원(인벤토리 결손, 드묾)은 그 축을 sizing.axes에 넣지 않는다 - null 원소를 만들지 않아 위 불변식이 항상 성립한다. 소비자는 축 부재를 그 자원의 사이징 신호 없음으로 보고 reproduction/기존 인벤토리로 폴백한다(7절).
- `action: "keep"`이면 `recommended`는 `current`를 그대로 복사한 값이다(재계산으로 인한 미세 드리프트 없음).
- disk 축은 축소하지 않는다. `action`은 `increase` 또는 `keep`만 가능하다(온라인 디스크 축소는 파괴적이고 데이터가 안 맞을 수 있음). 소진 임박 마운트는 목표 크기가 현재를 초과하면 `increase`, 목표가 현재 안에 들면 `keep`(확장 불요)다 - diagnostics의 disk status가 `filling`이어도 sizing.action은 `keep`일 수 있다.
- `action: "decrease"`(recommended < current)는 다운사이즈 안전 게이트(신뢰도 높음 + 상승 추세 아님 + 관측 충분)를 통과할 때만 발행된다. 게이트 미충족 과다 자원은 `keep` + `recommended = current`로 낮추고, 과다 사실은 diagnostics에만 노출한다.

단위:
- `vcpus`: 정수 개수.
- `mib`: 2^20 바이트(binary MiB). `current`는 관측 총량 내림, `recommended`는 올림(하향 오차 방지).
- `gib`: 2^30 바이트(binary GiB). ceil. 소비자가 프로바이더의 십진 GB(10^9)로 변환할 때도 ceil을 유지해 하향(under) 방향 오차를 막는다.

estimate_quality 의미:
- `exact`: 관측 통계로 산정한 수치(5절 통계 기반 목표). 또는 `keep`으로 현재 값 그대로.
- `floor`: under인데 정확 수치 산정 불가라 안전 한 단계 상향으로 채운 하한. 실제 필요량은 이 이상일 수 있다.
- `uncertain`: 해당 축이 미측정이거나 표본 부족이라 판정 유보. `recommended = current`(소스 유지, 추측 없음).

action과 estimate_quality 유효 조합:
- `increase` + `exact`: 통계로 증설 목표 산정.
- `increase` + `floor`: 증설이 필요하나 정확 수치 불가, 안전 하한.
- `keep` + `exact`: 적정 유지(확신). 또는 게이트 미충족으로 억제된 과다(과다 사실은 diagnostics).
- `keep` + `uncertain`: 미측정/표본 부족으로 판정 유보(현재값 유지).
- `decrease` + `exact`: 게이트 통과 축소.

### 4.5 assessment - 판정

```json
"assessment": {
  "classification": "under_provisioned",   // under_provisioned | over_provisioned | idle | optimal | insufficient_data
  "confidence": "medium",                  // high | medium | low
  "data_quality": {
    "sufficient": false,
    "notes": ["표본 부족"]                  // 신뢰도 하향 사유(사람 읽기용). confidence=medium이라 sufficient=false
  }
}
```

- `classification`은 호스트 종합 판정이다. 정렬/표시 편의용이며, 실제 프로비저닝 결정은 sizing.axes를 소비한다.
- `insufficient_data`는 사이징 2축(cpu·memory)이 둘 다 미측정일 때다 - disk/disk_io/network가 측정돼 있어도 두 축이 없으면 판정 불가로 본다. 단 디스크 용량이 소진 임박(filling)이면 under_provisioned가 우선한다. cpu·memory 중 한쪽만 측정된 부분 결손은 insufficient가 아니다 - 측정된 축으로 classification을 완결하고, 미측정 사이징 축은 sizing.axes에서 `estimate_quality: "uncertain"`으로 표기한다. 특정 자원의 미측정 여부는 해당 sizing 축의 estimate_quality와 diagnostics.resources[].status로 확인한다.
- `data_quality.sufficient`는 `confidence == "high"`일 때만 `true`다(medium/low면 `false`). `notes` 불변식: `confidence`가 `high`가 아니면 notes에 최소 하나의 하향 사유가 담긴다.
- 크기로 안 풀리는 신호는 sizing에 반영되지 않는다. `classification`이 `under_provisioned`인데 sizing.axes 전 축이 `keep`이면 원인은 디스크 용량 신호이며 diagnostics의 disk status(`filling`)로 확인한다 - inode 소진인 경우 해당 disk 축의 `note`에도 사유가 실린다. 디스크 I/O 병목과 네트워크 혼잡은 classification을 under로 만들지 않고 `advisory.disk_io_tier_hint` / `advisory.network_congested`로만 노출되므로, 완전한 프로비저닝은 classification과 무관하게 advisory를 확인한다.

### 4.6 diagnostics - 근거/힌트 (선택 소비)

사이징 결정에는 불필요하나 투명성/감사/소비자 자체 정책을 위해 제공한다. 진단은 사이징을 게이팅하지 않는다.

```json
"diagnostics": {
  "root_cause": "memory",                  // cpu | memory | disk | disk_io | null (근본원인 축)
  "root_cause_detail": "메모리 (CPU 유발)",  // 사람 읽기용 인과 설명 문자열 | null
  "resources": [{
    "axis": "cpu",                         // cpu | memory | disk | disk_io | network
    "status": "under",                     // 축별 부분집합(아래)
    "utilization": { "eval_pct": 78.0, "sizing_pct": 78.0 },   // eval=판정 p95, sizing=사이징 통계(4.6 주석)
    "saturation": { "signal": "run queue/core", "value": 1.2, "threshold": 1.0, "unit": "per_core", "measured": true, "saturated": true },
    "confidence_notes": ["표본 부족"]
  }],
  "advisory": {
    "disk_io_tier_hint": null,             // high_iops | null (호스트 단위)
    "network_congested": false
  }
}
```

- `resources[].status`는 축별로 의미가 달라 허용값도 축별 부분집합이다:
  - `cpu`, `memory`: under | optimal | over | insufficient | unmeasured
  - `disk`(용량): filling | capacity_ok | unmeasured
  - `disk_io`: io_bound | io_ok | unmeasured
  - `network`: congested | quality_ok | unmeasured
- `resources`는 항상 5개 축(cpu/memory/disk/disk_io/network) 전부를 담는다(문제없는 축도 status로 포함, 문제축만 담는 sparse list 아님). 소비자는 배열 인덱스가 아니라 `axis` 키로 조회한다.
- `utilization`은 cpu·memory 축만 채운다 - cpu는 eval/sizing 둘 다 p95, memory는 eval=p95 / sizing=near-peak. 나머지 축(disk, disk_io, network)은 둘 다 `null`이며, 마운트별 사용률은 sizing.axes의 disk 축 `used_pct`에서 본다.
- `saturation`은 cpu/memory/disk_io 축만 객체이고 disk(용량)/network 축은 `null`이다. 소비자는 축별로 접근한다(모든 원소에서 saturation.saturated 접근 금지).
- `root_cause`는 근본원인 축(소문자 enum) 또는 `null`이고, `root_cause_detail`은 사람 읽기용 인과 설명이다. 사이징이 인과 억제를 하지 않으므로(5절), root_cause는 정보 제공용이며 sizing.axes는 관측된 모든 under 축을 독립적으로 담는다.
- `advisory.disk_io_tier_hint`가 디스크 I/O 티어 권고의 단일 권위다(호스트 단위). 엔진은 디바이스별 I/O 지연을 산출하지 않으므로 per-disk 티어 힌트는 제공하지 않는다.

## 5. 사이징 모델 (안전 우선)

권고 수치를 도출하는 방식이다. 계약 표면은 필드/단위/불변식이고, 아래의 통계 선택과 임계 튜닝 값(목표%/포화선/runway 일수/await 기준)은 엔진 내부 파라미터다 - 튜닝 값은 계약 버전을 올리지 않고 바뀔 수 있으니, 소비자는 수치 자체가 아니라 불변식(4.4/6/7절)에 의존한다. 아래는 산출 방식을 이해시키기 위한 설명이다.

통계는 자원의 물리 특성에 맞춘다(fit-for-purpose):
- 판정(under/over 분류)과 CPU 사이징은 p95를 쓴다. CPU는 탄력적이라 순간 초과를 실행큐 큐잉으로 흡수한다 - working set처럼 물리적으로 딱 맞아야 하는 자원이 아니다. 목표 이용률 착지 + run queue 포화 headroom 중 큰 쪽을 목표로 한다.
- 메모리 사이징은 near-peak(관측 피크 대표 통계)를 쓴다. 메모리는 초과하면 즉시 OOM이라 큐잉으로 못 버틴다 - working set이 물리적으로 맞아야 한다. 관측 피크 위에 여유를 두고 착지시킨다.

이 CPU(p95, 탄력)와 메모리(near-peak, 비탄력)의 비대칭이 fit-for-purpose 원칙 그 자체다 - 자원마다 초과의 결과가 다르므로(CPU=큐잉 지연, 메모리=OOM) 사이징 통계도 다르다. 데이터 해상도(5분 버킷)상 못 잡는 짧은 스파이크가 있으면 해당 축을 `floor`로 표기한다.

CPU 두 축: 사이징 목표는 이용률 목표와 run queue 포화 headroom 중 큰 쪽이다. 이용률만으로 못 잡는 포화(실행큐 적체)를 별도 축으로 보정한다. per-core 이용률이 높으면 다운사이즈를 보류한다(단일 스레드 보호).

바닥값(floor): under인데 정확 수치를 못 내는 축은 안전한 한 단계 상향으로 채운다(recommended가 null이 되지 않게).
- CPU 포화 주도 under(이용률은 낮으나 실행큐 적체로 목표가 현재 이하): 현재보다 한 단계 큰 vcpu, `estimate_quality: floor`.
- 메모리 이용률 미측정 + OOM 발생: 현재 위에 안전 여유를 얹은 크기, `estimate_quality: floor`(이용률 없이 신호만으로는 정확 목표 불가). 이용률 없이 paging 신호만 있으면 정상 하드폴트와 구분되지 않아 under로 채택하지 않는다(축은 `keep` + `uncertain`). 이용률이 측정된 상태에서 paging/OOM이면 현재를 초과하는 목표를 산정한다(`estimate_quality: exact`).
- 크기로 안 풀리는 축은 floor를 강제하지 않는다. inode 소진은 볼륨 용량(GB) 확장으로 해결되지 않으므로(mkfs 시 고정) 사이징 축이 아니라 진단 advisory로 노출한다. 디스크 I/O 병목도 마찬가지로 크기가 아닌 티어 문제라 advisory다.

인과 억제 없음: 관측된 모든 under 축을 독립적으로 사이징한다. 근본원인 분석(메모리발 CPU 등)은 diagnostics.root_cause에 설명으로만 담고 사이징 수치를 게이팅하지 않는다. 일회성 마이그레이션은 "근본원인 고치고 재평가" 루프가 없으므로, 관측된 부족은 모두 안전하게 반영한다(어느 축도 미달로 만들지 않음). 절감이 목표가 아니라 이 방향의 과다 사이징은 허용 오차다.

디스크 용량(마운트별): 소진 임박을 가용 이력 전체 span의 2점(시작/종료 여유 공간) 선형 fill-rate 외삽으로 판정하고 목표 수명 크기를 산정한다. 마운트별 `recommended = max(current, ceil(목표_바이트 / 2^30))`으로 절대 현재보다 작아지지 않는다. current와 recommended가 같은 파일시스템 기준이라 단위 불일치로 인한 오축소가 없다. 디스크 축은 가상 파일시스템(tmpfs/overlay/proc/sysfs 등)과 `/boot*` 마운트를 뺀 나머지 마운트 전부를 담는다. 원격 마운트(NFS/CIFS/9p/FUSE)를 별도로 제외하지 않으므로, 소비자는 `device_ref`가 null인 disk 축을 로컬 볼륨으로 프로비저닝하지 말고 reproduction.mounts와 대조해 원격 여부를 판별한다.

디스크 I/O(호스트 단위): 응답 지연이 높으면 `advisory.disk_io_tier_hint: "high_iops"`로 빠른 볼륨 타입을 권고한다. 용량과 별개 축이라 크기(gib)를 늘리지 않는다. 엔진의 I/O 지연 신호가 호스트 단위(디바이스별 아님)라 advisory도 호스트 단위다.

## 6. 필드별 값 제공 현황

계약 필드 집합은 수집 에이전트와 합의된 것이고, 구조(키 존재)는 고정이다(2절 additive). 문서화된 모든 키는 항상 존재하며 값이 없으면 `null` 이다 - 소비자는 어느 필드든 `null` 을 처리한다. 아래는 각 필드가 현재 값을 담는지, 담지 않으면 왜 `null` 인지다.

값이 채워짐 (인벤토리/메트릭에서 확보 - 이미지/부트 기반 정확 재현 가능):
- identity 전체
- reproduction.os{family, id, version, codename, kernel, arch, bits, boot_firmware, timezone}
- reproduction.boot{kernel_cmdline, root_ref_type}
- reproduction.network{id, name, kind, addresses, gateway, mtu, dns, routes}
- reproduction.storage.block_devices 전 레이아웃 상세: 디스크(size_bytes, partition_table, sector_size, serial, wwn, rotational), 파티션(part_number, part_start_bytes, part_type, part_name, part_flags), 파일시스템(fstype, fs_uuid, fs_label, block_size, mountpoint, mount_options, fs_freq, fs_passno), LVM(lvm_vg, lvm_lv, lvm_segtype, lvm_stripes, lvm_stripe_size_kib), RAID(raid_level, raid_chunk_kib, raid_metadata, raid_uuid), crypt(crypt_type)
- reproduction.storage.lvm_vgs{name, vg_uuid, extent_size_bytes}
- reproduction.mounts[] (비블록 마운트: source, target, fstype, options, fs_freq, fs_passno)
- sizing.axes의 current 전 축, assessment 전체, diagnostics 전체

호스트·OS 구성에 해당할 때만 값, 아니면 `null`:
- reproduction.os.secure_boot: UEFI Secure Boot efivar 존재 시. 미노출 펌웨어면 null.
- reproduction.os.rtc_utc: RTC 시각 기준 판별 가능 시. 판별 불가면 null.
- reproduction.os.edition: Windows 에디션 전용. 리눅스는 항상 null.
- reproduction.network{bond_mode, vlan_id, speed_mbps}: 본딩/VLAN/물리 링크 속도가 있는 인터페이스에서만. speed_mbps 는 inventory 미발행(virtio/Windows NT5.2) 시 metrics `network.link.speed`(bit/s -> Mbps)로 폴백 — 둘 다 없으면 null.

현 에이전트가 아직 값을 안 실어 `null` (성격이 갈림):
- reproduction.network.addresses[].origin (static/dhcp): getifaddrs 에 origin 정보 자체가 없다. 채우려면 netlink IFA_FLAGS 나 설정/리스 파싱 등 다른 경로가 필요한 구조적 한계.
- reproduction.storage.lvm_vgs{size_bytes, free_bytes, pv_ids}: 엔진은 에이전트 발행값을 그대로 통과시키므로 안 실은 에이전트에서는 null(9절 예시 호스트가 그렇다). 필드별 발행 여부의 정본은 `docs/reference/contracts/agent-data.md` F4 절이다.
- reproduction.boot.grub_install_target: 부트로더 설치 디스크 식별 미구현(항상 null). ESP 파티션의 부모 디스크 귀속으로 채울 수 있음.

소비자 규약: 위 `null` 필드가 있어도 소비자는 추측하지 않고 그대로 처리한다. 예로 lvm_vgs 여유 용량이 null 이면 확장 여력 판단을 보류한다.

## 7. 엣지 동작

- 신규 서버(인벤토리만, 메트릭 없음): reproduction과 cpu/memory sizing 축의 current는 채워지고 두 축은 `uncertain`(recommended = current). 디스크 사이징 축은 메트릭 이력이 있어야 생성되므로 신규 서버엔 부재다(디스크 크기는 reproduction.storage로만 제공). `classification: insufficient_data`.
- 오프라인 서버(`online: false`): 마지막 창 데이터로 판정한다. reproduction은 최신 인벤토리 스냅샷이다. 데이터가 stale일 수 있음은 `identity.online: false` 플래그로 판단한다(별도 신선도 note는 없다).
- Windows 포화 축 미측정(perflib 미발행): 측정된 축으로 판정을 완결하고, 미측정 축만 `data_quality.notes`에 "포화 수치 미관측"으로 표기한다. cpu/memory가 미측정이면 해당 sizing 축이 `uncertain`.
- 부팅 크리티컬 필드 null(arch, boot_firmware): 정상 리눅스 호스트에선 채워지나(6절), 인벤토리 결손 등으로 예외적으로 null 이면 소비자는 추측하지 않는다. arch가 null이면 ISA를 x86_64로 가정하지 말고 재현을 보류하며 경고를 노출한다. boot_firmware가 null이면 파티션 플래그(ESP 존재 등)로 추정하되 불확실하면 보류한다. 안전 우선 원칙을 재현 축에도 적용한다.
- LVM/RAID/멀티패스 호스트: storage.block_devices가 계층을 트리로 표현한다. sizing.axes의 disk current 총량은 멀티패스 중복과 RAID 멤버 이중계산을 배제한 실 프로비저닝 크기다.
- 바인드/다중 마운트 이중계상: sizing.axes의 disk 축은 마운트별 하나라, 서로 다른 마운트포인트가 같은 backing device를 공유(bind mount, 같은 볼륨 다중 마운트)하면 별개 축으로 이중 계상될 수 있다. 파일시스템 device_id가 발행되면(에이전트 수집 확장) `(server_id, device_id)`로 dedup 가능 - 현재는 device_id 부재라 마운트포인트 기준이며 소비자는 device_ref 트리 조인으로 같은 backing 여부를 식별한다.
- 인벤토리 결손으로 base 수량(cpu 코어 수, 총 RAM)이 미상인 축: 그 축은 sizing.axes에서 생략된다(null 원소를 만들지 않아 4.4의 `max(current, recommended)` 불변식 유지). 소비자는 축 부재를 사이징 신호 없음으로 보고 reproduction/기존 인벤토리로 폴백한다. 이때의 classification 판정은 4.5절을 따른다.

## 8. export 엔드포인트 (파일 다운로드)

```
POST /api/exports/inventory
```

같은 데이터를 다운로드용 JSON 파일로 내려받는 채널이다. 운영자가 파일을 저장해 Terraform/Ansible 등에 오프라인으로 투입하는 용도다.

- 요청 body(JSON): GET /api/assessment의 쿼리 파라미터와 동일한 필터 필드(`hostname`, `ip`, `public_id`, `pair`는 문자열 배열, `window_days`는 int, `end`는 ISO datetime).
- 응답: `application/json` 파일 다운로드(Content-Disposition attachment, 파일명 `assessment-<timestamp>.json`).
- 파일 최상위 객체는 GET /api/assessment 최상위 객체(4.1)와 구조가 동일하다 - 같은 `contract_version`, 같은 필드. 별도 래퍼로 감싸지 않는다.
- 파일도 2절 additive-only 규약과 버전 규약의 적용 대상이다.

## 9. 워크드 예시 (복잡 스토리지 리눅스 VM — 실측)

실제 fleet 호스트 `raidlvmluks` 한 대의 `GET /api/assessment` 응답 그대로다(값 편집 없음). "꽤 모던한 리눅스 VM"(Ubuntu 24.04)이되 스토리지 레이아웃만 아주 복잡하게 잡은 검증 호스트다 - RAID1 + LVM(다중 LV) + LUKS + 별도 데이터 디스크. 방금 프로비저닝돼 부하가 없어(idle) sizing 은 전부 keep 이고, 이력이 얕아 confidence 는 medium(표본 부족)이다. block_devices 트리와 reproduction 상세가 이 예시의 초점이다.

스토리지 레이아웃 (JSON 은 아래):

```
vda (30G)  Ubuntu 부트 디스크
 |- vda1  29G ext4  /
 |- vda14  4M (bios_grub 예약)
 |- vda15 106M vfat /boot/efi (part_type=ESP GUID)
 `- vda16 913M ext4 /boot
vdb (10G) + vdc (10G)  -> mdadm RAID1 = md0 (raid_level 1) -> LVM PV -> VG "datavg"
   |- dm-0 (lv_data)  5G ext4 /data
   |- dm-1 (lv_logs)  3G xfs  /var/log/svc
   `- dm-2 (lv_swap)  1G swap
vdd (8G)  -> LUKS2 (crypt_type luks2) -> dm-3 -> ext4 /secure
```

md0 은 부모(PV member)가 vdb·vdc 둘이라 `(id, parent)` 쌍으로 노드가 두 번 나오고 `size_bytes` 는 id 당 한 번만 센다(4.3 트리 조인). swap LV(dm-2)는 lvm 노드와 swap 노드 두 형태로 나타난다(lsblk 실측 그대로).

```jsonc
{
  "contract_version": "1.0",
  "generated_at": "2026-07-10T20:58:29Z",
  "window": { "days": 14, "start": "2026-06-26T20:58:29Z", "end": "2026-07-10T20:58:29Z",
              "basis": "관측 창(기본 14일). 데이터가 창보다 짧으면 assessment.data_quality 로 신뢰도 하향." },
  "filter": { "hostname": ["raidlvmluks"], "ip": [], "public_id": [], "pair": [] },
  "warnings": { "ambiguous_hostnames": [], "unresolved_pairs": [], "unmatched_filters": [] },
  "count": 1,
  "servers": [{
    "identity": { "public_id": "468d4beb-b140-406a-bb7b-f836c2d6bae0", "hostname": "raidlvmluks",
                  "hostname_ambiguous": false, "primary_ip": "10.50.6.171", "os_family": "linux", "online": true },
    "reproduction": {
      "os": { "family": "linux", "id": "ubuntu", "version": "24.04", "codename": "noble",
              "kernel": "6.8.0-87-generic", "arch": "x86_64", "bits": 64, "boot_firmware": "uefi",
              "secure_boot": null, "edition": null, "timezone": "Etc/UTC", "rtc_utc": null },
              //  ^ secure_boot/rtc_utc: 이 호스트에서 agent 가 미판별 null. edition: Windows 전용.
      "boot": { "kernel_cmdline": "BOOT_IMAGE=/vmlinuz-6.8.0-87-generic root=LABEL=cloudimg-rootfs ro console=tty1 console=ttyS0",
                "root_ref_type": "label", "grub_install_target": null },   // grub_install_target: agent 1차 null
      "network": { "interfaces": [{
        "id": "fa:16:3e:0d:b3:20", "id_type": "mac", "name": "enp3s0", "kind": "physical", "mtu": 1450,
        "addresses": [
          { "address": "10.50.6.171", "prefix": 24, "family": "ipv4", "origin": null },
          { "address": "fe80::f816:3eff:fe0d:b320", "prefix": 64, "family": "ipv6", "origin": null }
        ],  //  ^ origin(static|dhcp): agent 가 getifaddrs 로는 미판별 null(netlink 도입 시 확정 가능)
        "gateway": "10.50.6.1", "dns": ["127.0.0.53"],
        "routes": [ { "via": "10.50.6.1", "dest": "8.8.8.8/32" }, { "via": "10.50.6.2", "dest": "169.254.169.254/32" } ],
        "bond_mode": null, "vlan_id": null, "speed_mbps": null }] },
      "storage": {
        "block_devices": [
          // 첫 노드 vda 는 전 필드 펼침(응답의 모든 노드가 이 키를 전부 가진다 - 미해당은 null). 이후는 채워진 필드만.
          { "id": "pci-0000:05:00.0", "id_type": "by-path", "name": "vda", "type": "disk", "parent": null,
            "size_bytes": 32212254720, "partition_table": "gpt", "sector_size": 512,
            "serial": null, "wwn": null, "rotational": true,
            "part_number": null, "part_start_bytes": null, "part_type": null, "part_name": null, "part_flags": null,
            "fstype": null, "fs_uuid": null, "fs_label": null, "block_size": null, "mountpoint": null,
            "mount_options": null, "fs_freq": null, "fs_passno": null,
            "lvm_vg": null, "lvm_lv": null, "lvm_segtype": null, "lvm_stripes": null, "lvm_stripe_size_kib": null,
            "raid_level": null, "raid_chunk_kib": null, "raid_metadata": null, "raid_uuid": null, "crypt_type": null },
          // 파티션: part_type(GPT GUID)·part_flags·fs_uuid·fs_label·mount_options·block_size 실측.
          { "id": "db72259b-2da1-485a-84b3-6311c3a8aa57", "id_type": "partuuid", "name": "vda15", "type": "part",
            "parent": "pci-0000:05:00.0", "size_bytes": 111149056, "part_number": 15, "part_start_bytes": 5242880,
            "part_type": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "part_flags": ["esp"], "fstype": "vfat",
            "fs_uuid": "56E6-A418", "fs_label": "UEFI", "block_size": 512, "mountpoint": "/boot/efi",
            "mount_options": ["umask=0077"], "fs_freq": 0, "fs_passno": 1 },
          { "id": "...vda1-partuuid", "id_type": "partuuid", "name": "vda1", "type": "part",
            "parent": "pci-0000:05:00.0", "size_bytes": 30601641472, "part_type": "...", "fstype": "ext4",
            "mountpoint": "/", "fs_passno": 1 },
          { "id": "...vda16", "id_type": "partuuid", "name": "vda16", "type": "part", "parent": "pci-0000:05:00.0",
            "fstype": "ext4", "mountpoint": "/boot" },
          { "id": "...vda14", "id_type": "partuuid", "name": "vda14", "type": "part", "parent": "pci-0000:05:00.0",
            "part_flags": ["bios_grub"] },
          // 데이터 디스크 2개 (RAID 멤버).
          { "id": "185d4842-e673-4056-...", "id_type": "partuuid", "name": "vdb", "type": "disk", "parent": null,
            "size_bytes": 10737418240, "rotational": true },
          { "id": "4dc3bbd4-ccb0-44ae-...", "id_type": "partuuid", "name": "vdc", "type": "disk", "parent": null,
            "size_bytes": 10737418240, "rotational": true },
          // md0: 다중 부모(vdb·vdc) -> 2회, size 는 id 당 1회. raid_level/raid_metadata/raid_uuid 실측.
          { "id": "md0", "id_type": "name", "name": "md0", "type": "raid", "parent": "185d4842-e673-4056-...",
            "size_bytes": 10727981056, "raid_level": 1, "raid_metadata": "1.2",
            "raid_uuid": "7d2e4435-cb32-bab6-194a-f999d37c457d" },
          { "id": "md0", "id_type": "name", "name": "md0", "type": "raid", "parent": "4dc3bbd4-ccb0-44ae-...",
            "size_bytes": 10727981056, "raid_level": 1, "raid_metadata": "1.2",
            "raid_uuid": "7d2e4435-cb32-bab6-194a-f999d37c457d" },
          // LVM LV: lvm_vg/lvm_lv/lvm_segtype/lvm_stripes 실측.
          { "id": "LVM-SnS55Ved1y...grr4tOzg", "id_type": "dm", "name": "dm-0", "type": "lvm", "parent": "md0",
            "size_bytes": 5368709120, "fstype": "ext4", "fs_uuid": "ad0b1649-f4b9-4e04-b269-bfe98fd85308",
            "block_size": 4096, "mountpoint": "/data", "mount_options": ["defaults", "nofail"], "fs_freq": 0,
            "fs_passno": 2, "lvm_vg": "datavg", "lvm_lv": "lv_data", "lvm_segtype": "linear", "lvm_stripes": 1 },
          { "id": "LVM-SnS55Ved1y...ssxoYNfc", "id_type": "dm", "name": "dm-1", "type": "lvm", "parent": "md0",
            "fstype": "xfs", "mountpoint": "/var/log/svc", "lvm_vg": "datavg", "lvm_lv": "lv_logs",
            "lvm_segtype": "linear", "lvm_stripes": 1 },
          { "id": "LVM-SnS55Ved1y...swap", "id_type": "dm", "name": "dm-2", "type": "lvm", "parent": "md0",
            "fstype": "swap", "lvm_vg": "datavg", "lvm_lv": "lv_swap" },
          { "id": "...dm-2-swap", "id_type": "dm", "name": "dm-2", "type": "swap", "parent": null,
            "fstype": "swap", "mountpoint": "[SWAP]" },   // swap 사용면(같은 dm-2 의 swap 형태)
          // LUKS: crypt_type luks2 실측.
          { "id": "CRYPT-LUKS2-e34163ed...-securecrypt", "id_type": "dm", "name": "dm-3", "type": "crypt",
            "parent": "a1c9b0dc-b310-484e-...", "size_bytes": 8573157376, "fstype": "ext4",
            "fs_uuid": "63bb1cc6-1476-45a5-b393-1f7707850f1f", "block_size": 4096, "mountpoint": "/secure",
            "mount_options": ["defaults", "nofail"], "fs_freq": 0, "fs_passno": 2, "crypt_type": "luks2" }
        ],
        "lvm_vgs": [{ "name": "datavg", "vg_uuid": "SnS55V-ed1y-yfbk-Xy3y-WQox-x5Mk-XOavBF",
                      "size_bytes": null, "free_bytes": null, "extent_size_bytes": 4194304, "pv_ids": null }]
                    //  ^ size_bytes/free_bytes/pv_ids: 이 호스트 agent 가 미발행 null
      },
      "mounts": [
        { "source": "tmpfs", "target": "/run", "fstype": "tmpfs",
          "options": ["rw", "nosuid", "nodev", "noexec", "relatime"], "fs_freq": 0, "fs_passno": 0 },
        { "source": "tmpfs", "target": "/dev/shm", "fstype": "tmpfs", "options": ["rw", "nosuid", "nodev"],
          "fs_freq": 0, "fs_passno": 0 }
        // ... /run/lock, /run/user/0 (블록장치 없는 마운트 - fstab 재생성 팩트)
      ]
    },
    "sizing": {
      "axes": [
        { "axis": "cpu",    "current": 1,    "recommended": 1,    "unit": "vcpus", "action": "keep", "estimate_quality": "exact" },
        { "axis": "memory", "current": 1961, "recommended": 1961, "unit": "mib",   "action": "keep", "estimate_quality": "exact" },
        { "axis": "disk", "mountpoint": "/",            "device_ref": "...vda1", "current": 29, "recommended": 29, "unit": "gib", "action": "keep", "estimate_quality": "exact", "used_pct": 6.6, "runway_days": null, "note": null },
        { "axis": "disk", "mountpoint": "/data",        "device_ref": "LVM-SnS55Ved1y...grr4tOzg", "current": 5, "recommended": 5, "unit": "gib", "action": "keep", "estimate_quality": "exact", "used_pct": 0.0, "runway_days": null, "note": null },
        { "axis": "disk", "mountpoint": "/secure",      "device_ref": "CRYPT-LUKS2-e34163ed...", "current": 8, "recommended": 8, "unit": "gib", "action": "keep", "estimate_quality": "exact", "used_pct": 0.0, "runway_days": null, "note": null },
        { "axis": "disk", "mountpoint": "/var/log/svc", "device_ref": "LVM-SnS55Ved1y...ssxoYNfc", "current": 3, "recommended": 3, "unit": "gib", "action": "keep", "estimate_quality": "exact", "used_pct": 3.0, "runway_days": null, "note": null }
      ]
    },
    "assessment": { "classification": "idle", "confidence": "medium",
                    "data_quality": { "sufficient": false, "notes": ["표본 부족"] } },
    "diagnostics": {
      "root_cause": null, "root_cause_detail": null,
      "resources": [
        { "axis": "cpu", "status": "optimal", "utilization": { "eval_pct": 0.8, "sizing_pct": 0.8 },
          "saturation": { "signal": "run queue (procs_running)/core", "value": 1.45, "threshold": 1.0, "unit": "per_core", "measured": true, "saturated": false }, "confidence_notes": ["표본 부족"] },
          // run queue 1.45/core 지만 이용률 0.8% 라 미포화 - idle 호스트를 실행큐 노이즈로 오증설하지 않음(dual-gate).
        { "axis": "memory", "status": "over", "utilization": { "eval_pct": 16.8, "sizing_pct": 17.2 },
          "saturation": { "signal": "swap page-out", "value": null, "threshold": null, "unit": "event", "measured": true, "saturated": false }, "confidence_notes": ["표본 부족"] },
        { "axis": "disk", "status": "capacity_ok", "utilization": { "eval_pct": null, "sizing_pct": null },
          "saturation": null, "confidence_notes": ["표본 부족"] },
        { "axis": "disk_io", "status": "io_ok", "utilization": { "eval_pct": null, "sizing_pct": null },
          "saturation": { "signal": "await", "value": null, "threshold": 20, "unit": "ms", "measured": false, "saturated": null }, "confidence_notes": ["표본 부족"] },
        { "axis": "network", "status": "quality_ok", "utilization": { "eval_pct": null, "sizing_pct": null },
          "saturation": null, "confidence_notes": ["표본 부족"] }
      ],
      "advisory": { "disk_io_tier_hint": null, "network_congested": false }
    }
  }]
}
```

읽는 법:
- 스토리지: `mountpoint` 있는 노드가 실제 마운트(`/`·`/boot`·`/boot/efi`·`/data`·`/var/log/svc`·`/secure`). 트리는 `id` 로만 조인(`name`=dm-0/md0 등은 표시용·비유일). md0 은 다중 부모라 (id,parent) 쌍으로 2회. 총 프로비저닝 용량은 `type=disk` 노드(vda 30G + vdb 10G + vdc 10G + vdd 8G)의 `size_bytes` 합 - RAID 멤버·LVM LV·crypt 매핑 중복 배제. 레이아웃 상세(partition_table·part_type·raid_level·lvm_segtype·crypt_type·fs_uuid·mount_options 등)가 채워져 파티션/파일시스템/RAID/LVM/LUKS 조립을 그대로 복원할 수 있다.
- 재현 완전성: os arch(x86_64)·boot_firmware(uefi)·kernel_cmdline·root_ref_type 이 채워져 이미지/부트 기반 재현이 가능하다. 아직 null 인 값들: secure_boot·rtc_utc(이 호스트에 efivars SecureBoot·/etc/adjtime 부재라 정상 null), addresses[].origin(getifaddrs 에 static/dhcp 정보 없음 - 구조적), lvm_vgs size_bytes/free_bytes/pv_ids(이 호스트 agent 미발행), grub_install_target(agent 미구현). 소비자는 항상 null 을 처리한다.
- sizing/assessment: 방금 뜬 idle 호스트라 전 축 keep, classification idle, confidence medium(표본 부족 - 이력<30h). cpu 는 실행큐가 코어당 1.45 여도 이용률 0.8% 라 미포화로 처리(idle 을 실행큐 노이즈로 오증설하지 않는 dual-gate). 부하가 쌓이면 이용률·await 등이 채워지고 confidence 가 오른다. 마운트별 disk 축은 소진 추세가 없어 전부 keep, swap/`/boot`/`/boot/efi` 는 disk 사이징 축이 아니다.

## 10. 운영 계약

릴리스 계약으로서 운영 항목을 명시한다.

- 인증/인가: 현재 무인증(내부 관리망 전용 전제). 외부 노출 시 별도 인증 게이트웨이를 앞단에 둔다 - 본 API는 자체 토큰/인증을 요구하지 않는다.
- 에러 응답: 파라미터 검증 실패 = HTTP 422(FastAPI 표준 `{"detail": [{"loc","msg","type"}, ...]}`). 서버 내부 오류 = HTTP 500. 매칭 0건은 오류가 아니라 200 + `count: 0`(404 없음).
- rate limit: 없음. 대량 호출은 호출 측이 조절한다.
- pagination: 없음. 매칭 전량을 한 응답으로 반환한다(외부 자동화가 fleet를 한 번에 소비하는 목적). 대규모 인벤토리는 hostname/ip/public_id/pair 필터로 스코프해 응답 크기와 비용을 줄인다.
- export(POST /api/exports/inventory)도 동일 운영 계약을 따른다.
