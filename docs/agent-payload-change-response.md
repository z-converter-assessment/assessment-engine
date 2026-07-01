# 에이전트 -> 엔진 payload 개선 요청 회신

`assessment-agent-temp/docs/engine-payload-change-requests.md` 8건 + 부록에 대한 에이전트 쪽 회신이다.
각 항목에 "반영 가부 / 채택 필드·구조 / 제약"을 담았고, 엔진 실측과 에이전트 실측이 어긋나는 부분은
에이전트 소스 기준(파일:라인)으로 바로잡았다. 코드 근거는 모두 `assessment-agent-temp/` 기준.

## 전환 원칙

무중단 additive 원칙에 동의한다. 에이전트는 신형 필드를 구형과 함께 발행하고, 전 배포 완료 후
`agent_version` major bump 시점에 구형 필드를 제거한다. 아래 "반영"은 별도 언급이 없으면 additive를 뜻한다.

## 요약

| # | 항목 | 반영 | 핵심 제약 |
|---|------|------|-----------|
| 1 | device `kind` 태그 통일 | 채택 | 공용 분류기 신규. "pre-drop 폐지"는 단계적(먼저 태그 additive, 그다음 완화) |
| 2 | services pid/exe | 채택 | Windows 즉시(이미 보유). Linux는 MainPID 배치 조회 |
| 3 | interfaces 구조화(iface명/family/kind/IPv6) | 채택 | 1번 분류기 공유. `ip_external`은 별도 유지 |
| 4 | 안정 agent_id | 조건부 채택 | additive 즉시 가능. 단 prep-image가 agent_id도 초기화해야 함(짝 필수) |
| 5 | Windows saturation | 채택(후순위) | PDH 신규 도입 -> DLL 의존/legacy32(NT5.2) 제약 |
| 6 | Windows 메모리 canonical | 채택 | swap 불변식 소스 미보장 확인 -> clamp/재매핑 |
| 7 | boot_time 정적 | Linux 이미 정적 / Windows만 부분 | 엔진 주석이 Linux에 대해 stale |
| 8 | task.result os 필드 | 채택 | Linux는 os 필드 전무, Windows는 os_id 없음·os_version 의미 상이 |

## 공통: 분류기 단일화 (1·3의 토대)

"가상이냐"를 에이전트 공용 분류 함수 하나가 정하라는 방향에 동의한다. Linux/Windows 각각 collect.c에
device kind 분류기를 하나 두고, disk_io·net_io·interfaces·mounts가 같은 함수를 호출하게 한다. 채택 taxonomy:

- disk `kind`: `physical` | `partition` | `lvm` | `raid` | `virtual` (loop/ram/sr/rom은 계속 drop, 정책은 코드 한 곳에 문서화)
- iface `kind`: `physical` | `loopback` | `bridge` | `veth` | `bond_master` | `bond_member` | `vlan` | `tunnel` | `virtual`
- mount `kind`: `data` | `virtual_fs` | `boot` | `image` (+ `fstype`)

제약: Windows는 세분 분류가 Linux만큼 안 나온다(예: bond_master/bond_member/veth 구분 불가). Windows는
coarse 값(`physical`|`loopback`|`virtual`|`tunnel`)만 실을 수 있고, 엔진은 값 목록에 이 coarse 집합도
`physical`/비physical 판정에 포함해야 한다.

---

## 1. device 분류 규칙 통일 — 채택 (HIGH)

반영한다. 다만 엔진 실측 일부를 에이전트 소스 기준으로 정정한다.

정정(디스크): 인벤토리 `disks[]`는 `is_excluded_block_dev`가 아니라 `lsblk -dn -b -e 7,11 -o NAME,MAJ:MIN,SIZE,TYPE -J`
경로다(`src/collect.c:493`). `-d`로 파티션 제외, `-e 7,11`로 loop(major7)/sr(major11)를 커널 major에서 제외한다.
즉 lsblk가 이미 `TYPE`(disk/part/lvm/raid/rom)을 준다 -> 우리는 이걸 `kind`로 매핑만 하면 된다(저비용).
`is_excluded_block_dev`(`src/collect.c:131`)는 `disk_io[]`(`/proc/diskstats`)와 sysfs fallback에만 쓰인다.
`disk_io[]`는 `is_excluded_block_dev` + `/sys/block/<dev>` 존재 요구로 파티션은 이미 빠지지만 dm-/md는 실린다.

에이전트 변경:
- 디스크: `disks[]`·`disk_io[]` 각 항목에 `kind` 추가. `disks[]`는 lsblk TYPE 매핑(disk->physical, part->partition,
  lvm->lvm, raid->raid). `disk_io[]`는 분류기로 kind 부여. sysfs fallback 경로는 `/device` 유무로 physical 판정.
- 네트워크: `net_io[]`에 `kind` 추가. 현재 필터는 `lo`만(`src/collect.c:1521`)이라 분류기 신규 필요
  (`/sys/class/net/<if>/`의 bridge/bonding/tun_flags/lower_* 속성으로 판정).
- 마운트: `mounts[]`에 `kind` 추가. Linux는 이미 `fstype` 보유(인벤토리)라 fstype -> kind(data/virtual_fs/boot/image)
  매핑 가능. 단 metrics variant는 현재 `fstype`·`kind` 둘 다 없음(`src/collect.c:778-804`) -> 둘 다 추가.
- Windows: 디스크는 전부 PhysicalDrive라 `kind:"physical"` 상수(`windows-agent/src/collect.c:295`). mounts는
  DRIVE_FIXED만이라 `kind:"data"`. major=0 고정 문제(`collect.c:296,343,387`)는 kind로 대체되면 해소된다.

제약: "pre-drop 폐지하고 전부 실어라"는 payload 볼륨 증가(파티션/dm/veth 등)와 새 분류기 비용이 있어 단계적으로 간다.
1단계 additive: 현재 수집 범위에 `kind`만 부여(엔진은 즉시 태그 기반 필터로 전환 가능). 2단계: loop/ram/sr 등
"절대 무의미" 외 pre-drop 완화. 채택 필드명 `kind` 확정.

---

## 2. services <-> listen_ports pid join — 채택 (HIGH)

반영한다. `listen_ports[]`는 이미 양 OS에서 `pid`를 싣는다(Linux `/proc/PID/fd` 매칭 `src/collect.c:1252-1259`,
Windows `GetExtendedTcpTable(TCP_TABLE_OWNER_PID_LISTENER)` `windows-agent/src/collect.c:862`). 빠진 건 services 쪽이다.

에이전트 변경: `services[]` 각 항목에 `pid`(int|null), `exe`(string|null) 추가.
- Windows: `EnumServicesStatusExW(SC_ENUM_PROCESS_INFO)`가 이미 `ServiceStatusProcess.dwProcessId`를 쥔다
  (`windows-agent/src/collect.c:796-829`) -> pid는 즉시. exe는 `fill_comm_for_pid`(NT6 한정) 재사용.
- Linux: 현재 `systemctl list-units` 파싱이라 pid 없음(`src/collect.c:1044-1075`). MainPID를
  `systemctl show --property=Id,MainPID <unit들...>` 배치 1회 호출로 조인(per-unit show 반복은 fork 비용 커서 회피).
  exe는 listen_ports가 쓰는 `/proc/PID/comm` 인프라 재사용.

제약: Linux legacy(EL6, SysV/upstart) 호스트는 systemctl이 없어 services 자체가 비거나 pid null일 수 있음
(플랫폼 차이). Windows NT5(2003)는 exe basename이 null(현재 `fill_comm_for_pid`가 NT6 전용).

---

## 3. ip_internal 구조화 — 채택 (HIGH)

반영한다. 엔진 실측대로 Linux는 CIDR 문자열/IPv4-only/iface명 없음/loopback만 제외다(`src/collect.c:814-844`).

에이전트 변경: 구조화 배열 `interfaces[]` 신규 발행(구형 `ip_internal` 문자열 배열과 additive 병행).
```
"interfaces": [
  {"name": "eth0",    "address": "10.0.1.15",  "prefix": 24, "family": "ipv4", "kind": "physical"},
  {"name": "docker0", "address": "172.17.0.1", "prefix": 16, "family": "ipv4", "kind": "bridge"},
  {"name": "eth0",    "address": "fd00::1",    "prefix": 64, "family": "ipv6", "kind": "physical"}
]
```
- Linux: `getifaddrs`가 이미 iface명·netmask를 주므로 name/address/prefix 분리는 저비용. `AF_INET6` 추가로 IPv6 포함.
  `kind`는 1번 분류기 공유.
- Windows: `GetAdaptersAddresses`로 동일 구조 생성(name=FriendlyName/AdapterName, family, prefix=OnLinkPrefixLength).

결정 요청: `ip_external`은 같은 배열에 `scope`로 합치기보다 별도 필드 유지를 제안한다(내부/외부는 수집 소스·의미가
달라 분리가 단순). 엔진이 통합을 강하게 원하면 `scope:"internal"|"external"`로 합칠 수 있다 — 택일 회신 바람.

---

## 4. 안정 agent_id — 조건부 채택 (HIGH, 장기)

방향 동의. 현재 상태 정정: composite_id는 엔진이 아니라 에이전트가 계산한다.
`composite_id = SHA256(machine_id + "\n" + sorted MACs)`를 에이전트가 만들어 모든 메시지에 싣는다
(`src/collect.c:924-978`, `windows-agent/src/collect.c:553`). raw `machine_id`도 함께 보낸다. `agent_id`(첫 install
생성·영구 저장 UUID) 개념은 현재 없다. worker state dir(`/var/lib/agent-worker`, Windows `%ProgramData%\assessment-agent\worker`)은
있으나 식별자를 저장하지 않는다.

에이전트 변경: install(또는 최초 실행) 시 UUIDv4 생성 -> 영구 저장 -> 모든 메시지에 `agent_id` additive 발행.
- 저장 위치: Linux `/var/lib/assessment-agent/agent-id`, Windows `%ProgramData%\assessment-agent\agent-id`.
- `machine_id`·`mac_addresses`·`composite_id`는 과도기 동안 유지(감사·relink 백업).

제약(중요): VM 골든 이미지 복제 시 agent_id도 복제되면 클론들이 동일 id를 갖는다. 현재 `prep-image`는
machine-id(Linux)/MachineGuid(Windows)만 초기화한다. agent_id 도입 시 prep-image가 agent_id 파일도 삭제(다음
기동 때 재생성)하도록 반드시 짝으로 바꾼다. 이 짝 없이는 agent_id가 오히려 MAC보다 나쁜 중복원이 된다.
엔진 식별키(DB UNIQUE, MQ 라우팅 `agent.tasks.{composite_id}`) 마이그레이션은 엔진 ADR로 분리 진행에 동의.

진행: 에이전트가 `agent_id`를 additive로 먼저 실어둘 수 있다(엔진은 준비되면 전환). prep-image 확장이 선행 조건.

---

## 5. Windows saturation 카운터 — 채택(후순위) (MEDIUM)

현재 Windows 에이전트에 PDH 사용 흔적이 전혀 없다(`pdh`/`PDH` grep 0건). saturation 필드도 미생성.
메트릭은 전부 `GlobalMemoryStatusEx`/IOCTL 기반이다.

에이전트 변경: `saturation:{cpu_run_queue, disk_queue, mem_paging_rate}` raw 발행. 정규화하지 않는다(P1 raw-first 동의).
- CPU: `\System\Processor Queue Length`
- Disk: `\PhysicalDisk(_Total)\Avg. Disk Queue Length`
- Memory: `\Memory\Pages/sec`

제약:
- PDH 도입 시 `pdh.dll` 런타임 의존이 생긴다 -> release verify의 DLL 화이트리스트에 pdh 추가 필요. legacy32(NT5.2/2003)는
  DLL 최소화가 원칙이라, `pdh.dll` 대신 `RegQueryValueEx(HKEY_PERFORMANCE_DATA)`로 raw counter를 읽는 방식을 검토한다
  (추가 DLL 없이 동일 카운터 취득 가능).
- `Avg. Disk Queue Length`는 PDH가 시간구간 평균으로 계산하는 파생 카운터라, raw diff로 재현하려면 카운터 타입 처리가 필요.
  구현 시 카운터 의미(순간 depth vs 구간 평균)를 재확인한다.
- 우선순위: 1~3·6·8 반영 후 착수.

---

## 6. Windows 메모리 canonical 매핑 — 채택 (MEDIUM)

확인 결과 `swap_free <= swap_total`가 소스 차원에서 보장되지 않는다.
- `mem_available_kb <= mem_total_kb`: `ullAvailPhys <= ullTotalPhys`라 항상 성립(문제 없음).
- `swap_total = (TotalPageFile > TotalPhys) ? (TotalPageFile - TotalPhys) : 0`,
  `swap_free = (AvailPageFile > AvailPhys) ? (AvailPageFile - AvailPhys) : 0`
  (`windows-agent/src/collect.c:228-233`). 서로 다른 뺄셈이라 상한 관계가 없어, 페이지파일이 작을 때 `swap_free > swap_total`가
  발생 가능 -> 엔진 clamp가 실제로 발동하는 케이스.

에이전트 변경: swap 매핑을 canonical 불변식에 맞게 정합. 최소 `swap_free = min(swap_free, swap_total)` clamp,
가능하면 실제 pagefile 크기/사용량 기반으로 `swap_total`/`swap_free`를 재정의. 형식 변경 없음(값 정합만).
엔진 clamp는 defense로 유지하되 정상 에이전트에선 발동 0을 목표로 한다.

부록 요청 수용: Windows 카운터 -> canonical 필드 매핑표를 에이전트 문서로 제공한다(GlobalMemoryStatusEx 필드 -> mem_*/swap_* 대응).

---

## 7. boot_time 정적 소스 — Linux 이미 정적 / Windows 부분 (MEDIUM)

엔진 주석이 Linux에 대해 stale이다. Linux boot_time은 이미 정적 `/proc/stat btime`이다
(`cached_boot_time_iso`, `src/collect.c:31-61`, 발행 `113-114`). now-minus-uptime이 아니다 -> Linux는 변경 불요(확인 완료).

Windows는 now-uptime 방식이나(`util.c:219-234`, `GetSystemTimeAsFileTime - monotonic_ms`), 프로세스 시작 시 1회
캐시해 재사용한다(`collect.c` cache) -> 한 프로세스 생애 동안 boot_time은 상수다(per-collection 지터 없음). 다만
에이전트 재시작 시 ±1초 흔들릴 수 있다(재부팅 아님).

에이전트 변경: restart-stable을 원하면 Windows boot_time을 정적 소스로 전환 가능(부팅시각 직접 취득). 다만 엔진의 5초
tolerance로 이미 흡수되고 per-collection 지터는 없으므로 우선순위는 낮게 둔다. 엔진이 tolerance 유지로 충분하다면
Windows도 현행 유지로 종결 가능 — 판단 회신 바람.

---

## 8. task.result os 필드 — 채택 (LOW)

반영한다. 현재 상태:
- Linux task.result: os 필드 전무(`src/worker.c:151-204`에 os_family/os_id/os_version 없음).
- Windows task.result: `os_family`="windows" + `os_version` 있으나 `os_id` 없음. 게다가 이 `os_version`은
  `os_build_number()`의 bare `CurrentBuildNumber`(`windows-agent/src/worker.c:200-210`)라 inventory의
  `os_version`(DisplayVersion)과 의미가 다르다.

에이전트 변경: 양 OS task.result에 `os_family` + `os_id` + `os_version`을 일관 발행하고, `os_version`은 inventory와
동일 소스를 쓴다(Windows는 DisplayVersion으로 통일). 이러면 엔진의 inventory 역조회가 사라진다.
```
"os_family": "linux", "os_id": "rocky", "os_version": "9.3"
```

---

## 부록 회신

- os_family nullable fallback: os_family는 어디서도 JSON null이 아니다(항상 "linux"/"windows"). 유일한 결측은
  Linux task.result에 필드 자체가 없는 것 -> 8번으로 해소된다. 8번 반영 후 엔진 fallback 제거 가능.
- listen_ports.uid Windows null: 정당한 플랫폼 차이에 동의, 변경하지 않는다.
- mac_addresses: Linux 인벤토리는 `mac_addresses` 배열을 싣지만(`src/collect.c:1302`) Windows 인벤토리는 별도
  `mac_addresses`를 싣지 않는다(composite_id 계산에만 사용). 4번 진행 시 Windows도 감사용 `mac_addresses`를
  additive로 맞출 수 있다 — 필요 여부 회신 바람.
- 역제안: 위 필드명(`kind`, `interfaces`, `agent_id`, `saturation`)은 그대로 채택 가능. 3번 `ip_external` 통합 여부와
  7번 Windows 종결 방식만 택일 회신해주면 에이전트 구현 순서를 확정한다.

## 추가 확인: install.args -> 자식 프로세스 전달 (엔진 요청)

질문: 엔진이 Linux(shell/install.sh)·Windows(direct_exec .exe) 양쪽에 `["-s", zdm_host, "-u", zdm_user]`를 동일하게
보낸다. Windows .exe가 `-s host -u user`를 그대로 받는지.

검증 결과(에이전트 전달 계층): 양 OS 모두 args를 verbatim argv로 전달한다. 전달은 대칭이며 계약상 문제없다.
- Linux(`src/exec.c:209-220`): `execve(install.sh, [install.sh, "-s", host, "-u", user, NULL])`. 스크립트가 argv[1..]로 그대로 받음.
- Windows direct_exec(`windows-agent/src/exec.c:120-124`): cmdline `"<target.exe>" -s <host> -u <user>`를 구성해
  `CreateProcessA(NULL, cmdline)`으로 실행. Windows가 이 cmdline을 다시 argv로 파싱하므로 .exe는
  `[target.exe, -s, host, -u, user]`를 Linux와 동일하게 받는다.

즉 에이전트 전달은 정상이다. 단, 아래 제약을 계약 문서에 명기해야 한다.

1. install.type가 `msi`면 args가 드롭된다. `build_cmdline`의 MSI 분기(`windows-agent/src/exec.c:111-117`)는
   `msiexec.exe /i <target> /quiet /norestart`만 만들고 argv_extra(-s/-u)를 붙이지 않는다. 따라서 `-s`/`-u`는
   direct_exec(Windows)·shell(Linux)에서만 전달된다. MSI 설치가 -s/-u를 필요로 하면 argv가 아니라 MSI PROPERTY
   방식이어야 하며, 현재 에이전트는 MSI에 args 전달을 지원하지 않는다.
2. 인용 규칙이 최소적이다. `append_quoted`(`windows-agent/src/exec.c:91-101`)는 값에 공백/탭이 있을 때만 `"..."`로
   감싸고, 내부 `"`·말미 `\`는 Windows CommandLineToArgvW 규칙대로 이스케이프하지 않는다. zdm_host/zdm_user가 단순
   문자열이면 안전하나, 공백 포함 값은 단순 인용만 되고 따옴표·백슬래시 말미가 든 값은 깨질 수 있다.
   install.args 값은 argv-simple로 유지 권장.
3. install.type가 OS별로 제약된다. Linux는 `shell`만 허용(그 외 `unsupported_install_type` 실패, `src/worker.c:298`),
   Windows는 `direct_exec`/`msi`만 허용(`shell`은 실패, `windows-agent/src/worker.c:690`). 지금처럼 엔진이
   Linux=shell / Windows=direct_exec로 분기하는 것이 맞다.

진짜 미검증 항목(에이전트 밖): "Windows .exe가 -s/-u를 install.sh와 동일 의미로 파싱하느냐"는 ZConverter Windows
설치 바이너리의 자체 arg 파서 문제다. 에이전트는 argv 전달(대칭)만 보장하고 설치 바이너리의 플래그 해석엔 관여하지
않는다. 이건 에이전트로 검증 불가 — 실제 ZDM Windows installer로 확인해야 한다. 계약 문서 문구 제안: "에이전트는
install.args를 양 OS에 verbatim argv로 전달(대칭 보장). 설치 대상(Linux 스크립트 / Windows exe)이 동일 플래그를
동일 의미로 받도록 하는 것은 installer 계약이며, Windows exe의 -s/-u 수용은 ZDM installer 쪽에서 별도 검증."

## 확인 회신: 착수 순서 "(1)"의 의미

질문("(1)"이 발행 dual-write 수정이냐, payload item 1이냐)에 답한다.

- "(1)"은 payload item 1(device `kind` 태그)이다. 착수 순서의 숫자 1~8은 payload 항목 번호이지 전환 단계 번호가 아니다.
- 엔진 판단이 맞다: item 1에 대해 지금 엔진은 스키마 여는 것(`kind` 필드 additive 수용, extra=ignore)까지만 하면 된다.
  실제 dual-read + `device_filters` 태그 기반 교체는 에이전트가 `kind`를 실어 발행한 뒤에 한다. 지금 그 이상은 읽을 게
  없어 불가한 게 정상이다.
- 발행 dual-write(신형+구형 필드 동시 발행)는 에이전트 몫이다 — 엔진이 발행 쪽 dual-write를 만들 필요 없다. 엔진의
  역할은 각 항목마다 (a) 스키마 개방(지금) -> (b) 신형-우선 이중 읽기(언제든, 에이전트 발행 후 실효) -> (c) 에이전트
  major bump 후 구형 경로 제거다.

## 착수 순서(제안)

1차(HIGH, additive, 저위험): 1(kind 태그) + 2(services pid) + 3(interfaces) + 8(task.result os) + 6(swap 정합) 묶음,
`agent_version` minor bump. 2차: 4(agent_id + prep-image 짝). 3차: 5(saturation) + 7(Windows boot_time, 필요 시).

## 1차 구현 완료 — 실제 발행 형식 (dual-read 정합용)

agent_version 1.1.0으로 아래를 additive 발행한다(구형 필드 유지). 엔진 이중 읽기는 이 필드명/값으로 맞춘다.

- disks[] / disk_io[]: `kind` ∈ {physical, partition, lvm, raid, virtual}
- net_io[]: `kind` — Linux {physical, loopback, bridge, veth, bond_master, bond_member, vlan, tunnel, virtual},
  Windows coarse {physical, loopback, tunnel, virtual}
- mounts[]: `kind` ∈ {data, virtual_fs, boot, image}. metrics variant에도 이제 `fstype`+`kind` 포함(기존엔 없었음)
- interfaces[] (신규, inventory only, ip_internal과 병행): `{name, address, prefix, family("ipv4"|"ipv6"), kind}`.
  IPv6 포함. `ip_external`은 별도 유지(결정대로)
- services[]: `pid`(int|null) + `exe`(string|null) 추가. listen_ports[].pid와 pid로 조인 가능
- task.result: `os_family` + `os_id` + `os_version` 추가(양 OS). Windows `os_version`은 inventory와 동일 DisplayVersion

주의: Windows net kind는 coarse라 bridge/veth/bond/vlan을 physical/virtual로만 구분한다. Linux는 세분.
services pid는 EL6/비-systemd 및 Windows NT5(2003, exe basename)에서 null로 graceful degrade.

## item 6: Windows 메모리 canonical 매핑표

| canonical 필드 | Windows 소스 (GlobalMemoryStatusEx) |
|---|---|
| mem_total_kb | ullTotalPhys / 1024 |
| mem_available_kb, mem_free_kb | ullAvailPhys / 1024 |
| mem_buffers_kb, mem_cached_kb | null (Windows 등가 카운터 없음) |
| swap_total_kb | (ullTotalPageFile - ullTotalPhys)/1024, 음수면 0 |
| swap_free_kb | min((ullAvailPageFile - ullAvailPhys)/1024, swap_total_kb) — clamp로 swap_free<=swap_total 보장 |

pagefile 기반 정밀 재정의는 후속(현재는 근사 + clamp). 엔진 defensive clamp는 유지해도 정상 에이전트에선 미발동.

## 2차/3차 구현 — 추가 발행 형식 (dual-read 정합용)

### item 4: agent_id (구현 완료, additive)

모든 메시지(inventory / metrics / error / task.result)에 `agent_id`(문자열, UUIDv4)를 추가 발행한다.
첫 실행 시 생성해 영구 저장(Linux state dir, Windows `%ProgramData%\assessment-agent\agent-id`)하고
재사용하므로 MAC/machine_id 재발급과 무관하게 불변이다. `machine_id`/`composite_id`/`mac_addresses`는
과도기 동안 그대로 유지(relink 백업).

- prep-image 짝 구현됨: `prep-image`(양 OS) + Linux image-prep.sh가 agent-id 파일을 삭제해 클론마다
  새로 생성되게 한다. 골든 이미지 봉인 전 prep-image 실행이 전제.
- 엔진 전환: `agent_id`가 준비되면 식별 키를 composite_id -> agent_id로 옮길 수 있다(엔진 ADR/마이그레이션은
  엔진 몫). 그 전까지는 additive라 엔진 영향 0.

### item 5: saturation (부분 구현 — 데이터 정합성 우선)

metrics에 `saturation` 객체를 추가한다. 현재 발행 형식:
```
"saturation": {"disk_queue": 0, "cpu_run_queue": null, "mem_paging_rate": null}
```
- `disk_queue`: 물리 디스크들의 순간 큐 깊이 합(IOCTL_DISK_PERFORMANCE.QueueDepth). 추가 DLL/PDH 없이
  신뢰성 있게 취득 -> 값 채워서 발행. (raw, 정규화 안 함)
- `cpu_run_queue`(Processor Queue Length), `mem_paging_rate`(Pages/sec): perflib
  (HKEY_PERFORMANCE_DATA) 파싱이 필요한데 실 Windows 호스트 없이 값 정합을 검증할 수 없어, 검증 전까지
  `null`(미측정)로 둔다. 잘못된 saturation 값이 right-sizing 판정을 오도하는 것을 막기 위한 의도적 보류다.

엔진 dual-read: `saturation.disk_queue`는 지금 값이 온다(disk saturation 축 채움). `cpu_run_queue`/
`mem_paging_rate`는 계속 null(unmeasured)이므로 CPU/메모리 saturation 축은 현행처럼 부분 평가 유지.
perflib 검증이 끝나면 이 두 필드가 raw 값으로 채워지고 그때 별도 통지한다.
