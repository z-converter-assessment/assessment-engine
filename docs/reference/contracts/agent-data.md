# Agent 메시지 데이터 계약 (wire)

정본 = `wire.schema.json`(JSON Schema draft 2020-12) + `wire-examples.json`(예시 6종). 본 문서는 그 계약을 사람이 읽는 카탈로그로 서술한다 — 스키마와 어긋나면 스키마가 이긴다. 정책: CLAUDE.md #B.

`schema_version` = 현재 `"1.0"`. 메시지 4종: `metrics` · `inventory` · `task.result` · `error`.

통일 버전: 시스템 전체 계약 버전은 엔진 레포 `contract.CONTRACT_VERSION`(현 `"1.0"`, major.minor)로 단일화된다 — wire·assessment API·export·task.install 4계약 공통 단일 값. 에이전트도 4종 메시지를 `"1.0"` 으로 emit 한다. 게이트는 major(점 앞 정수)만 비교. task.install(engine -> agent)은 이 버전을 실어 보내 에이전트가 실행 전 major 게이트한다. 버전 규약 단일 진실 = `contract.py`.

설계 원칙:
- 2레이어 분리 — Layer 1(wire) = 자원 네임스페이스별 raw counter/gauge 사실만. Layer 2(engine) = USE Method 해석(`recommendation.py`). USE(이용률·포화·오류 판정)를 wire 에 넣지 않는다.
- agent = stateless. emit 시점 raw 누적 스냅샷만 싣고, rate·delta·util·await·ratio 파생은 전부 엔진.
- null 의미론 — 값(0 포함) = 실측 성공. null = 측정불가·미지원(OS 개념 부재 또는 측정 인프라/권한 없음). 추측·대체값 금지. 배열 지표는 측정된 축만 싣는다.
- counter = monotonic 누적(엔진이 Δ 산출). gauge = 순간값(직접 소비).
- 인바운드 DTO 는 `extra=ignore` — 모르는 필드가 와도 통과·무시(부분 배포 비대칭 흡수).

---

## A. 인코딩

### A1. 두 형태

- `metrics` / `inventory` = envelope(불변 메타) + `system.*` 네임스페이스(datapoint-array) + inventory 배열. USE 재설계 대상.
- `task.result` / `error` = envelope + 평면 body 필드. `system.*` 재설계 비대상 — 작업/오류 이벤트.

최상위 required(전 타입 공통) = `schema_version`(`^1\.[0-9]+$`) + `message_type`(enum inventory/metrics/task.result/error).

### A2. system.* datapoint-array

각 `system.<namespace>` 값 = object 또는 null. null 이면 그 네임스페이스 전체 미지원(예 Windows `system.pressure`). 네임스페이스 = `{ "<metric명>": <metric> }` 맵.

metric 객체:
```
{ "type": "counter"|"gauge", "unit": "<base 단위>", "points": [ {"attr": {...}, "value": <number|null>}, ... ] }
```
datapoint = `{ "attr": {<k>:<string|number>}, "value": <number|null> }`. `attr` 옵셔널(생략 = 단일 스칼라 포인트). `value` 는 number 또는 null.

동일 metric 의 여러 차원(cpu별·device별·state별·direction별)은 `points` 배열 개별 원소로, 구분은 오직 `attr`. 이름에 방향·상태를 박지 않는다 — `rx_bytes` 금지, `network.io` + `attr.direction=receive`.

---

## B. Envelope (불변 메타)

`metrics`/`inventory` envelope. task.result/error 는 J절 body 표 참조.

| 필드 | 타입 | 비고 |
|------|------|------|
| schema_version | string `^1\.[0-9]+$` | 최상위 required |
| message_type | enum | 최상위 required |
| agent_id | string | 첫 실행 시 1회 생성·영구저장 불변 UUID. 매칭·식별·라우팅 단일 키 |
| message_id | string | 멱등성 키 (`idempotent:{message_id}` SET NX) |
| collected_at | ISO8601 UTC(tzinfo-aware) | 수집 시각. 시계열 자연키 |
| boot_time | string \| null | 판독 불가 시 null. counter reset(재부팅) 게이트 |
| agent_started_at | string \| null | agent 재시작 = counter reset 게이트 |
| os_family | enum linux/windows | 조건부 분기 기준 |
| agent_version | string | 옵셔널. major bump 수신 시 엔진 코드 수정 트리거 / minor silent 호환 |
| composite_id | string \| null | 옵셔널. SHA-256 composite hash. 감사·표시용(식별·라우팅 미사용). "" -> None 정규화 |
| machine_id | string \| null | 옵셔널. raw machine-id 표시 전용 |

metrics/inventory 필수 = `agent_id, message_id, collected_at, boot_time, agent_started_at, os_family` 6개. `agent_version`·`composite_id`·`machine_id` 는 옵셔널.

호스트 정적 서술자(hostname·os_id 등)는 envelope 아니라 `inventory` 메시지가 싣는다(F절). metrics 는 정적 서술자 없이 `agent_id` 로 서버에 조인된다.

---

## C. Required-필드 매트릭스

R=required, opt=optional, nullable=값 null 허용.

| 필드 | metrics | inventory | task.result | error |
|------|---|---|---|---|
| schema_version / message_type | R | R | R | R |
| agent_id / message_id / collected_at / os_family | R | R | R | R |
| boot_time / agent_started_at | R(nullable) | R(nullable) | R(항상 null) | opt |
| agent_version | opt | opt | R | opt |
| composite_id | opt | opt | (없음) | opt |
| machine_id | opt | opt | R(nullable) | opt |
| system.cpu / memory / disk / network | R | - | - | - |
| system.paging / filesystem / pressure / cgroup | opt | - | - | - |
| hostname / os_id / os_version / os_codename / kernel_version / cpu_model / cpu_cores / mem_total_bytes / ip_external / services / listen_ports | - | R | - | - |
| block_devices | - | R | - | - |
| net_interfaces | - | R | - | - |
| lvm_vgs | - | opt | - | - |
| hostname / os_id / os_version / os_codename | - | - | R | - |
| task_id / status / failure_reason / exit_code / signal_no / duration_ms / stdout_tail / stderr_tail / completed_at | - | - | R | - |
| task_policy | - | - | opt | - |
| error_code / error_message / failed_component | - | - | - | R |
| retry_count / first_failed_at / recovered_at | - | - | - | opt |

Windows metrics/inventory 는 추가로 `system.pressure`=null 강제 + `lvm_vgs` 금지(H절).

---

## D. Base 단위

OTel/Prometheus 정본. Windows-ism(100ns)·sectors·jiffies·% 는 에이전트가 base 로 정규화 후 발행.

| unit | 의미 |
|------|------|
| s | 시간(초, float) |
| By | 바이트 |
| bit/s | 링크속도 |
| 1 | ratio (0..1, % 아님) |
| operations | I/O 연산 카운트 |
| packets | 패킷 카운트 |
| errors | 오류 카운트 |
| segments | TCP 세그먼트 카운트 |
| connections | conntrack 엔트리 카운트 |
| events | 이벤트 카운트(oom_kill·mce) |
| tasks | 프로세스/스레드 카운트(run_queue·blocked) |
| cpu | 논리 CPU 개수(cpu.logical.count·cgroup.cpu.limit) |
| inodes | inode 카운트 |

---

## E. Device 안정키 정책

metric `attr.device` 와 inventory `block_devices[].id`/`parent`, `net_interfaces[].id` 는 이름(`dm-N`, perflib `0 C:`, Windows NIC `tap...`)이 재부팅·디스크추가에 바뀌므로 안정 id 를 조인 키로 쓴다. 단일 필드로 부족(fs UUID 없는 stripe/thin LV, GUID 없는 RAW 디스크)이라 계층 폴백 + `id_type` 라벨.

디스크 폴백(non-null 보장): `dm/uuid -> partuuid -> wwid -> serial -> by-id -> by-path -> name`. dm 계열(lvm/crypt/mpath/raid)은 `/sys/block/<kname>/dm/uuid` 항상 존재.
Windows 폴백: 디스크 `gptid -> mbrsig -> serial(RAW 포함)`, 파티션 `gptid`, 볼륨 `volguid`.
네트워크: `id` = MAC(`id_type=mac`, OpenStack 포트 MAC). 폴백 Windows `ifguid` / Linux `by-path`. `name` 은 표시용.

id_type enum:
- block_device: `dm, partuuid, wwid, serial, by-id, by-path, fsuuid, gptid, mbrsig, volguid, mac, ifguid, name, null` (mac·ifguid 는 net_interface 축 어휘 — 정본 wire.schema.json 이 공유 enum 으로 수용, block 노드 실사용은 아님).
- net_interface: `mac, ifguid, by-path, name, null`.

id + id_type 표현 (inventory vs metrics 이원):
- inventory: `id`(값) + `id_type`(라벨) 별도 필드. `id` 는 best-effort(nullable).
- metrics `attr.device`: `"<scheme>:<value>"` prefix 문자열. metric device attr 은 조인 키라 non-null 보장(inventory id 와 보장 수준 다름).
- metric device prefix scheme 카탈로그: 블록 = `gptid, mbrsig, serial, wwid, by-path, dm, partuuid` / E축 참조(RAID 배열·fs 레벨) = `md, btrfsuuid, fsuuid` / 네트워크 = `mac`. (inventory id_type enum 보다 넓다 — E축 배열 참조 어휘 포함.)
- `parent` = 부모 노드의 id. root=null. 복수 부모(stripe 멤버)면 같은 id 로 노드 반복, `parent` 만 다름.

---

## F. inventory 메시지

`agent_id` 로 서버에 upsert. envelope + 정적 서술자 + 배열.

### F1. 정적 호스트 서술자 (top-level)

| 필드 | 타입 | 용도 |
|------|------|------|
| hostname | string | 전 화면 호스트 표시·식별 |
| os_id | string \| null | OS 분류(almalinux/ubuntu/windows..)·서비스 분류 OS 분기 |
| os_version | string \| null | OS EOL 판정(attention)·버전 분포·right-sizing OS 분기 |
| os_codename | string \| null | OS 표시 라벨 |
| kernel_version | string \| null | Windows 빌드·Linux 커널 표시·EOL |
| arch | string \| null | ISA(x86_64/aarch64..). 재현 이미지 ISA 분기 |
| bits | integer \| null | 32/64. 재현 |
| boot_firmware | string \| null | uefi/bios. 재현 부팅 방식 |
| secure_boot | bool \| null | UEFI Secure Boot 여부. 미판별 null |
| edition | string \| null | Windows EditionID(SKU). Linux null. 서버 상세 os_display 조합 표시 |
| product_name | string \| null | Windows CurrentVersion ProductName 원문(예 "Windows Server 2019 Standard"). Linux null. os_display 짧은 라벨(연도/세대) 파싱 소스 — DisplayVersion(os_version)이 LTSC/SAC 를 "1809" 로 뭉뚱그리는 한계 보강. agent 무가공 발행(교정은 엔진 몫) |
| timezone | string \| null | IANA tz. 재현 |
| rtc_utc | bool \| null | RTC UTC(true)/localtime(false). 미판별 null |
| cpu_model | string \| null | CPU 모델 표시 |
| cpu_cores | integer \| null | CPU 포화(run_queue/cores)·사이징 목표. metrics `cpu.logical.count` 와 정합 |
| mem_total_bytes | integer(By) \| null | 메모리 사이징·표시. metrics `memory.limit` 와 동치 |
| ip_external | array \| null | 외부 IP 목록(표시) |
| boot | object \| null | 부트로더 재현. {kernel_cmdline(string\|null), root_ref_type(uuid/label/partuuid/path/null), grub_install_target(string\|null)} |
| nonblock_mounts | array \| null | 블록장치 없는 마운트(tmpfs 등). fstab 재생성용. 원소 {source, target, fstype, options(array), fs_freq(int), fs_passno(int)} |

### F2. block_devices[] (required, 정규화 평면 DAG 노드)

fs -> 물리디스크 확정 매핑(parent-by-id) + 스토리지 3계층(배정/파일시스템/확장여력).

| 필드 | 타입 | 비고 |
|------|------|------|
| name | string | 표시용(vdb, Disk0, H:). 키 아님 |
| type | string | disk/part/lvm/crypt/raid/mpath/dynamic/volume/swap. unknown 문자열 pass-through |
| size_bytes | integer(By) \| null | |
| fstype | string \| null | |
| mountpoint | string \| null | swap 노드는 `[SWAP]` 또는 pagefile 경로 |
| parent | string \| null | 부모 id. root=null. 복수 부모면 노드 반복 |
| id | string \| null | 안정 id(E절 폴백). best-effort |
| id_type | enum(E절) | |
| partition_table / sector_size / serial / wwn / rotational | string·int·bool \| null | 디스크 HW·파티션테이블 reproduction (gpt\|mbr·섹터·시리얼·회전형). 이하 reproduction = agent 확장 optional, 자연 노드타입에만 emit(미해당 부재) |
| part_number / part_start_bytes / part_type / part_name / part_flags | int·string·array \| null | 파티션 레이아웃 (part_type=GPT GUID/MBR hex) |
| fs_uuid / fs_label / block_size / mount_options / fs_freq / fs_passno | string·int·array \| null | 파일시스템 reproduction (fstab 재생성) |
| lvm_vg / lvm_lv / lvm_segtype / lvm_stripes / lvm_stripe_size_kib | string·int \| null | LVM 레이아웃 |
| raid_level / raid_chunk_kib / raid_metadata / raid_uuid | int·string \| null | RAID 레이아웃 (raid_level raw, 엔진 int\|null 정규화) |
| crypt_type | string \| null | LUKS 타입 (luks1\|luks2) |

swap 노드 = type=swap, size_bytes = 스왑 할당 크기(Linux 스왑 파티션 / Windows pagefile). 프로비저닝 스펙.

### F3. net_interfaces[] (required)

| 필드 | 타입 | 비고 |
|------|------|------|
| name | string | 표시용(ens3 / Windows 불안정) |
| id | string \| null | 안정키 = MAC |
| id_type | enum mac/ifguid/by-path/name/null | |
| kind | string \| null | physical/loopback/bridge/veth/bond_master/... |
| speed_mbps | integer \| null | 링크속도(util 분모). virtio·Windows NT5.2 부재 -> null. 이때 엔진은 metrics `network.link.speed`(bit/s)로 폴백 |
| addresses | array of {address, prefix(int\|null), family(ipv4/ipv6), origin(static\|dhcp\|null, reproduction)} | 인터페이스 IP. 서버 IP 표시·토폴로지(L3 서브넷 추론)·right-sizing IP 필터 |
| gateway | string \| null | default route gateway |
| mtu / dns / routes / bond_mode / vlan_id | int·array·string \| null | 인터페이스 reproduction (agent 확장 optional). routes=[{dest(CIDR), via}] |

### F4. lvm_vgs[] (opt, Linux 전용 — Windows 발행 금지)

| 필드 | 타입 | 비고 |
|------|------|------|
| name | string | |
| size_bytes | integer(By) \| null | |
| free_bytes | integer(By) \| null | 확장 여력(3계층째) 실측 — 디스크 추가 없이 바로 붙일 여유 |
| data_percent / metadata_percent | number \| null | 씬풀 충전율(used/total 블록, %). VG당 thin-pool 1개일 때만 발행 (0개·다수·status 파싱 불가 시 null) |
| vg_uuid / extent_size_bytes / pv_ids | string·int·array \| null | VG reproduction (agent 확장 optional). pv_ids=구성 PV 의 block_device id |

Windows 확장여력은 디스크크기 - 파티션합(미할당)으로 엔진이 파생.

### F5. services[] (required, 열거 불가면 null) / listen_ports[] (required)

서비스 카테고리 분류(서비스 뱃지·워크로드 역할). USE system.* 재설계 대상 아님 — 서비스 분류 전용.

- `services[]` = {unit, sub, pid(int\|null), exe(string\|null)}. 열거 불가면 null.
- `listen_ports[]` = {proto(tcp/tcp6/udp/udp6), addr, port(int), uid(int\|null), pid(int\|null), comm(string\|null)}. 분류 우선순위 name -> comm -> port. uid Windows null / pid null=소켓액티베이션(Linux)·권한부족(Windows) / port <= 1024 well-known.

---

## G. system.* metric 카탈로그 (metrics 메시지)

type: t=counter, g=gauge.

### G1. system.cpu (필수)

| metric | type | unit | attr | Linux | Windows | null |
|--------|---|---|---|---|---|---|
| cpu.time | t | s | cpu(코어 idx), state | /proc/stat jiffies/CLK_TCK | GetSystemTimes 100ns->s | 미지원 state |
| cpu.run_queue | g | tasks | source | procs_running | Processor Queue Length | - |
| cpu.blocked | g | tasks | source | procs_blocked | (없음) | Windows null |
| cpu.logical.count | g | cpu | - | nproc/sysconf | GetSystemInfo | - |
| cpu.mce | t | events | (source) | machinecheck+mcelog | WHEA | 소스 부재 |

`cpu.time` state = user/system/nice/idle/iowait/irq/softirq/steal. Windows 는 user/system/idle 만. U = 1 - rate(idle)/Σrate. run_queue.source = procs_running \| processor_queue(OS 신호원 비대칭 노출 — 엔진은 source 로 판별, os_family 분기 불요).

### G2. system.memory (필수)

| metric | type | unit | attr | Linux | Windows | null |
|--------|---|---|---|---|---|---|
| memory.usage | g | By | state | /proc/meminfo | GlobalMemoryStatusEx + Available MBytes | 미지원 state |
| memory.limit | g | By | - | MemTotal | ullTotalPhys | - |
| memory.commit.usage | g | By | - | Committed_AS | Committed Bytes | - |
| memory.commit.limit | g | By | - | CommitLimit | Commit Limit | - |
| memory.oom_kill | t | events | - | vmstat oom_kill(4.13+) | (없음) | 커널<4.13 / Windows null |
| memory.hardware_corrupted | g | By | - | HardwareCorrupted | (WHEA) | 소스 부재 |

`memory.usage` state = used/free/cached/buffered/available. available(MemAvailable / Available MBytes) = 회수 반영 실가용 — 엔진 압박 = 1 - available/limit(used% 아님). 3.14 미만 커널은 MemFree+Buffers+Cached 폴백 or null.

### G3. system.paging (opt)

| metric | type | unit | attr | Linux | Windows |
|--------|---|---|---|---|---|
| paging.operations | t | operations | direction(in/out), type(major/minor) | vmstat pswpin/pswpout, pgmajfault | Pages Input/Output/sec |

swapless Linux 는 direction=out 상시 0(실측) -> PSI/commit 이 포화 커버.

### G4. system.disk (필수, per device)

| metric | type | unit | attr | 산출 축 |
|--------|---|---|---|---|
| disk.io | t | By | device, direction(read/write) | throughput |
| disk.operations | t | operations | device, direction | IOPS |
| disk.io_time | t | s | device | U축 = %util(busy 분율) |
| disk.operation_time | t | s | device, direction | S축 = await(Δoperation_time/Δoperations) |
| disk.pending_operations | g | operations | device | S축 보조 = queue |
| disk.errors | t | errors | device, kind, class, (member) | E축 |

Linux 소스: diskstats(sectors*512 -> By, io_ticks/time_reading ms->s, in_flight). Windows: perflib(Disk Read/Write Bytes/sec, Avg. Disk sec/Read·Write, Current Disk Queue Length, query_time - %Idle Time). IOCTL 성능경로 폐기.
`disk.errors` 는 단일 counter + attr 로 통합: `{kind: mdraid, class: member_errors|degraded, member: vdf}`, `{kind: btrfs, class: corruption}`, `{kind: ext4, class: errors_count}`, Windows `{kind: eventlog}`. 정상 시 0.

### G5. system.filesystem (opt, per mount)

| metric | type | unit | attr | Linux | Windows | null |
|--------|---|---|---|---|---|---|
| filesystem.usage | g | By | device, mountpoint, type, state(used/free) | statvfs + /proc/mounts | GetDiskFreeSpaceEx | RAW/BitLocker 용량 null |
| filesystem.inodes.usage | g | inodes | device, mountpoint, state(used/free) | statvfs | (없음) | Windows null |

### G6. system.network (필수, per interface / host)

| metric | type | unit | attr | Linux | Windows | null |
|--------|---|---|---|---|---|---|
| network.io | t | By | device, direction(receive/transmit) | /proc/net/dev | MIB_IF_ROW2 | - |
| network.packets | t | packets | device, direction | /proc/net/dev | MIB_IF_ROW2 | - |
| network.errors | t | errors | device, direction | /proc/net/dev errs | In/OutErrors | - |
| network.dropped | t | packets | device, direction | /proc/net/dev drop | In/OutDiscards | - |
| network.link.speed | g | bit/s | device | /sys/class/net/*/speed | TransmitLinkSpeed | virtio/가상 -> null |
| network.tcp.retransmits | t | segments | (host 전역) | /proc/net/snmp RetransSegs | MIB_TCPSTATS | - |
| network.conntrack.usage | g | connections | (host 전역) | nf_conntrack_count | (미발행) | 모듈 미로드 null |
| network.conntrack.limit | g | connections | (host 전역) | nf_conntrack_max | (미발행) | 미로드 null |

link.speed null 이면 네트워크 U(io/speed) 미산출(수용). retransmits/conntrack/errors/dropped = E축(사이징 아님). Windows 는 conntrack metric 자체 미발행(개념 부재).

### G7. system.pressure (opt, PSI — Linux 4.20+)

| metric | type | unit | attr | 소스 |
|--------|---|---|---|---|
| pressure.stall.ratio | g | 1 | resource(cpu/memory/io), scope(some/full), window(10/60/300) | /proc/pressure/* avg |
| pressure.stall.time | t | s | resource, scope | /proc/pressure/* total(us->s) |

Windows 는 `system.pressure` 네임스페이스 전체가 null. 14일 saturation canonical = `pressure.stall.time`(K절·I절).

### G8. system.cgroup (opt, 컨테이너 배포 시만 — VM 이면 전체 null)

| metric | type | unit | attr |
|--------|---|---|---|
| cgroup.cpu.limit | g | cpu | - |
| cgroup.cpu.throttled.time | t | s | - |
| cgroup.memory.limit | g | By | - |
| cgroup.memory.events | t | events | type(oom/high) |

---

## H. os_family 조건부

| 조건 | 강제 |
|------|------|
| message_type=metrics | required system.cpu/memory/disk/network |
| message_type=inventory | required block_devices + F1 정적 서술자 |
| (metrics\|inventory) AND os_family=windows | system.pressure MUST be null + lvm_vgs 금지 |

Windows 비대칭: PSI 전체 null / lvm_vgs 금지 / cpu.blocked null / memory.oom_kill·hardware_corrupted null / filesystem.inodes.usage null / conntrack 미발행 / signal_no 항상 null / cpu.time state=user·system·idle 만.
구커널(EL6/7·SLES11-12·Debian10): PSI null / memory available 3.14 미만 폴백 / oom_kill 4.13 미만 null. swapless 사각은 PSI 로 보강(폴백 대체 아님).

---

## I. USE 매핑 (Layer 2, 엔진 해석 — wire 아님)

| 자원 | 축 | 산출 |
|------|---|------|
| CPU | U | 1 - rate(cpu.time[idle]) / Σrate(cpu.time) |
| CPU | S | pressure(cpu,some) 우선, 없으면 cpu.run_queue/cpu_cores |
| CPU | 인과 | cpu.blocked(D-state) = disk->cpu 인과 게이트 |
| Memory | U | 1 - memory.usage[available]/memory.limit |
| Memory | S | pressure(memory) 우선, 없으면 paging.operations[out] rate \| commit.usage/commit.limit |
| Disk | U | rate(disk.io_time) (busy 비율) |
| Disk | S | Δdisk.operation_time/Δdisk.operations(await) 우선, disk.pending_operations 보조 |
| Disk | E | rate(disk.errors) |
| Network | U | rate(network.io)/network.link.speed (speed null 시 미산출) |
| Network | E | rate(network.errors)+dropped+tcp.retransmits+conntrack |

14일 saturation canonical = `pressure.stall.time`(counter 적분). saturation(14일) = Δstall.time/Δwall(시간가중 평균 압박, 표본 손실 0). `ratio`(avg10 점표본)를 14일 창에 쓰면 표본 사이 stall 손실·평활 편향 -> 실시간 참고용만.
E축은 사이징 숫자 미반영 — 자원 fault 시 그 자원 U/S confidence 하향(steal_biased 동형) + attention 경보로만.

---

## J. task.result / error body

v2 envelope + 평면 body. `additionalProperties:false`.

### J1. task.result

발행 워커가 수집 캐시와 분리되어 `boot_time`/`agent_started_at` 항상 null. `task_id` 로 매칭(composite_id 불요).

| 필드 | 타입 | required |
|------|------|----------|
| schema_version / message_type / message_id / agent_id / agent_version | (const/string) | R |
| hostname / os_family | string/enum | R |
| os_id / os_version / os_codename | string \| null | R |
| machine_id / boot_time / agent_started_at | string \| null | R(boot_time·agent_started_at 항상 null) |
| collected_at / completed_at | ISO8601 | R |
| task_id / status | string | R |
| failure_reason | string \| null | R |
| exit_code | integer \| null | R |
| signal_no | integer \| null | R |
| task_policy | boolean \| null | opt |
| duration_ms | integer >= 0 | R |
| stdout_tail / stderr_tail | string | R |

종료 신호(POSIX wait status): 정상종료=exit_code / 시그널종료=signal_no / 미포착=둘 다 null. exit_code·signal_no 상호배타, Windows signal_no 항상 null. `task_policy`(bool\|null)는 exit_code 보다 우선.

### J2. error

수집/발행 실패 이벤트.

| 필드 | 타입 | required |
|------|------|----------|
| schema_version / message_type / agent_id / message_id / collected_at / os_family | | R |
| error_code | string minLen1 | R |
| error_message | string | R |
| failed_component | string minLen1 | R (자유 문자열 — Literal 로 좁히면 유효 메시지 DLQ) |
| agent_version / composite_id / machine_id / boot_time / agent_started_at | | opt |
| retry_count | integer >= 0 | opt |
| first_failed_at / recovered_at | ISO8601 | opt |

---

## K. counter reset · 멱등성

- counter reset(재부팅·agent재시작·wraparound)은 값-감소로 나타난다. 게이트 = envelope `boot_time`(재부팅) + `agent_started_at`(agent 재시작) — 시계열 테이블 공통 저장, 변화 시 reset 정밀 식별.
- 엔진 집계 = TimescaleDB continuous aggregate + timescaledb_toolkit `counter_agg` 가 값-감소 기준 reset 을 일률 처리. hand-rolled LAG + boot_time gate 부활 금지.
- 멱등성 2단 = `safe_set_nx(idempotent:{message_id}, 24h)` 1단 fail-open + 시계열 자연키 UNIQUE + `on_conflict_do_nothing` 2단.

routing key(broker 토폴로지)는 `message_type`(body 판별자)과 별개 — `docs/reference/rabbitmq.md`.
