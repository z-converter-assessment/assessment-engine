# Windows VM 파이프라인 검증 (libvirt)

본 문서는 dev 파이프라인의 Windows agent 검증 단일 진실. Linux 호스트(x86_64) + libvirt(KVM) 한정 (#A0).
Linux VM 5대는 `docs/development/pipeline.md`, Windows VM 1대는 본 문서가 단일 진실. 둘 다 동일 libvirt
(qemu:///system)·virbr0 NAT(192.168.122.0/24)에 붙어 분리망·gateway 추론 없이 통일됐다.

## 왜 Windows Server (Win11 아님)

x86_64 홈서버에선 Win11 대신 Windows Server 2022 Eval x64 가 모든 면에서 낫다:
- TPM 2.0·Secure Boot·MS 계정 OOBE 강요가 없다 -> autounattend 응답 파일·domain XML 이 단순(swtpm/secboot 불요).
- Server 2022/2025 평가판 x64 ISO 는 제품키 없이(180일) MS fwlink 직링크라 `curl` 로 받힌다 (Win11 은 폼/세션 토큰).
- agent 가 호출하는 Win32 API(`GetSystemTimes`·`GlobalMemoryStatusEx`·`IOCTL_DISK_PERFORMANCE`·
  `GetExtendedTcpTable`)·SCM·Performance Counter 는 Server 와 11 이 동일 -> 페이로드·스키마 정합 100% 커버.
- 이 제품 자체가 "서버 평가" 포털이라 Server 게스트가 도메인상 더 현실적이다.
- ARM 제약(ARM Server ISO 부재)으로 Win11 ARM 을 썼던 이유가 x86 에선 소멸. agent.exe(mingw PE32+ x86-64)는
  x86 Server 에서 네이티브 실행 — UTM 시절의 x64 emulation(Prism) 오버헤드도 사라진다.

## VM 역할 (Linux 5대 + Windows 1대)

| VM | 가상화 | distro | 서비스 (2) | 카테고리 | 비고 |
|----|--------|--------|-----------|----------|------|
| win-server-01 | libvirt (KVM) | Win Server 2022 Std Eval x64 | IIS (`W3SVC`, native role) | web | `Install-WindowsFeature Web-Server` |
|               |               | (Desktop Experience) | redis (tporadowski, 서비스명 `redis`) | cache | GitHub release MSI |

서비스 혼합 의도: native role(IIS) + 크로스플랫폼(redis) 양쪽이 `service_classifier`(web/cache)에 올바로
분류되는지 검증. IIS 의 SCM unit 명은 `W3SVC` — `service_classifier` 카탈로그의 `w3svc`/`iis` 키워드로 `web`.
redis(tporadowski, 서비스명 `redis`)는 소문자 매칭으로 `cache`. SQL Server(`mssqlserver`) 등 그 외 Windows
native 서비스는 미등록 — 필요 시 카탈로그 확장 (#E7).

## 자동화 — autounattend 무인 설치 (기본 포함)

Win11 UTM 시절의 "GUI 1회 수동 설치"가 libvirt + autounattend 로 완전 무인화됐다. 무거워도(ISO ~4.7GB +
설치 ~20min) "모든 환경" 원칙으로 기본 포함 — `dev-up.sh` 를 인자 없이 실행하면 Linux VM 에 이어 항상
실행된다. Linux 전용 dev 는 `WIN_ENABLE=0` 으로 opt-out. dev-down 이 Windows VM(qcow2)도 삭제하므로
최초 1회 무인설치(~20min)로 설치 과정을 검증한 뒤 완성본을 골든 이미지(`win-server-01-golden.qcow2`)로
캐시하고, 이후 dev-up 은 골든을 clone(~수십초)해 OS 설치를 skip 한다(dev-down 이 win-server-01.qcow2 는
지우되 골든은 보존). agent 는 매번 deploy 가 멱등 갱신(.exe 교체)이라 최신 반영.

```bash
./dev/dev-up.sh               # Linux 5 + Windows 모두 (기본)
WIN_ENABLE=0 ./dev/dev-up.sh  # Windows 제외 (Linux 전용)
```

`dev-up.sh` main 의 Windows 블록 흐름 (Linux VM 기동·web healthy 후):
1. `check_win_prereqs` — genisoimage·mingw-w64·cmake 자동 설치(sudo apt) + OVMF·autounattend 템플릿 점검.
2. `start_win_vm` — 도메인 있으면 start, 없으면:
   - `ensure_win_iso` — Server 2022 eval ISO fwlink 다운로드(dev/run, 1회 캐시) -> 풀 vol-upload.
   - `build_win_autounattend` — `dev/win/autounattend.xml.tmpl` 치환 -> ISO(루트 autounattend.xml·provision.ps1) -> 풀 import.
   - `define_win_domain`(boot order hd 우선 — 빈 디스크 첫 부팅은 cdrom 폴백, 설치 후 재부팅은 hd 의 Windows Boot Manager 직행) -> `virsh start` -> `virsh send-key`(Enter 연타로 "Press any key to boot from CD" 통과).
   - SSH 도달 대기(cap `WIN_SSH_CAP`, 기본 2400s = 설치 + FirstLogonCommands(OpenSSH) 완료까지).
   - 시계 UTC 동기화(`w32tm /resync`) — agent 배포(첫 발행) 이전에 수행. 골든 clone 부팅 직후 RTC 가
     local TZ 로 떠 있으면 발행된 `collected_at` 이 UTC 오프셋만큼 미래로 튀어 "가짜 최신 행"이 잔존
     -> 대시보드 CPU delta(최신 2행) 깨짐. 근본 차단은 provision.ps1 의 `RealTimeIsUniversal=1`(RTC=UTC 해석,
     골든 영구), 본 resync 는 NTP 보정.
3. `build_win_agent` — `ensure_win_vendor`(vendor static libs 없으면 자동 크로스빌드, 최초 1회 캐시) 후 mingw 크로스빌드(incremental).
4. `install_win_redis` — tporadowski MSI host 다운로드 -> scp -> msiexec(/qn). redis 서비스 있으면 skip.
5. `deploy_win_agent` — agent.env(RABBITMQ_HOST=게이트웨이 IP) scp -> agent.exe 설치·New-Service(공백 경로 quote) -> IIS/redis 기동 + agent restart.

재실행 멱등: 도메인 있으면 설치 건너뛰고 start + agent.env·exe·서비스만 갱신.

## autounattend 응답 파일

`dev/win/autounattend.xml.tmpl` (Server 2022 Std Desktop, UEFI/GPT). placeholder 2개를 `build_win_autounattend` 가 치환:

| placeholder | 값 |
|-------------|----|
| `@@HOSTNAME@@` | `win-server-01` (ComputerName) |
| `@@ADMINPASS@@` | built-in Administrator 비밀번호 (`WIN_ADMIN_PASS`, dev 고정) |

provision(OpenSSH·방화벽·SSH키·DefaultShell·IIS·`RealTimeIsUniversal`)은 `provision.ps1` 로 ISO 에 동봉 —
FirstLogonCommands 가 CD 스캔 후 실행. 거대 base64 를 CommandLine 에 직접 넣으면 maxLength 초과로 oobeSystem reject 라 분리.

핵심:
- 이미지 선택 = `/IMAGE/INDEX` 2 (eval ISO: 1=Std Core, 2=Std Desktop, 3=DC Core, 4=DC Desktop). 인덱스가
  바뀌면 `dism /Get-WimInfo /WimFile:<ISO>/sources/install.wim` 로 확인 후 템플릿 수정.
- 디스크 = GPT(UEFI): EFI(300MB)+MSR(16MB)+Primary(나머지, C:). SATA 디스크라 virtio 드라이버 주입 불요.
- OOBE 우회 = HideEULAPage·HideLocalAccountScreen·HideOnlineAccountScreens·NetworkLocation(Work) + AutoLogon(Administrator,
  LogonCount=1)로 FirstLogonCommands 실행. (ProtectYourPC·HideOEMRegistrationScreen·HideWirelessSetupInOOBE 는 Server 2022
  에서 "Value is invalid"(0x80220005) 유발이라 제거 — setupact.log UnattendGC 로 확인된 값 제한.)
- FirstLogonCommands(XML escape 회피 위해 base64 EncodedCommand 1줄) = OpenSSH Server capability 설치 +
  sshd Automatic·start + 방화벽 프로파일 off(dev) + `administrators_authorized_keys`(dev 공개키)+ACL +
  DefaultShell=PowerShell + `Install-WindowsFeature Web-Server`(IIS).
  - OpenSSH capability 는 Windows Update 에서 받는다 — VM outbound NAT(virbr0)로 도달.
  - SSH 키는 Administrator 가 관리자라 `~/.ssh` 가 아닌 `C:\ProgramData\ssh\administrators_authorized_keys`
    (`Administrators:F`·`SYSTEM:F` ACL).

## 도메인 (libvirt domain XML)

`define_win_domain` 생성. q35 + OVMF UEFI(non-secboot) + SATA + e1000e:
- 펌웨어: `<loader>OVMF_CODE_4M.fd` + per-VM `<nvram>` (template OVMF_VARS_4M.fd). Server 라 secboot/TPM 불요.
- 디스크: SATA qcow2(`WIN_DISK_GB`, 기본 64G). cdrom 2개 = Server ISO(boot) + autounattend ISO. disk 는
  모두 `type='file'` 명시 경로 — virt-aa-helper(apparmor) 프로파일 정합(Linux VM 과 동일 함정 회피).
- NIC: e1000e (Windows 인박스 드라이버) on virbr0 -> virtio-win ISO·드라이버 주입 불요. DHCP lease 는
  NIC 모델 무관이라 `virsh domifaddr --source lease` 로 IP 확인.
- boot order cdrom -> hd. 첫 부팅은 "Press any key to boot from CD" 프롬프트를 `virsh send-key`(Enter)
  연타로 통과. 설치 후엔 디스크 부팅(프롬프트 timeout).

성능보다 호환을 택한 선택(SATA·e1000e). agent 검증엔 무영향. virtio 성능이 필요하면 설치 후 virtio-win +
qemu-guest-agent 추가 가능(현재 미사용).

## 네트워크 — Linux VM 과 통일

```
libvirt VM (win-server-01)  [virbr0 192.168.122.0/24]
  RABBITMQ_HOST = 192.168.122.1                 -> host:5672 (docker 퍼블리시 -> DNAT -> rabbitmq 컨테이너)
  WORKER_DOWNLOAD_ALLOWED_HOSTS = 192.168.122.1 -> host:8000 (dev ZDM mock, ADR 0018)
```

UTM 시절의 host IP 추론(`resolve_host_ip`)·`host.docker.internal` 분기가 사라졌다 — Linux VM 과 동일하게
libvirt NAT 게이트웨이(`LIBVIRT_GW`, 기본 192.168.122.1)를 `dev-up.sh` 가 agent.env 에 주입. docker 퍼블리시
포트는 libvirt 기본 forward 규칙(LIBVIRT_FWO) + docker DNAT 로 도달(막힐 경우 fallback:
`sudo iptables -I DOCKER-USER -i virbr0 -j ACCEPT`, `pipeline.md` 참조).

## agent (Windows) 빌드 — host mingw 크로스컴파일 (dev-up 자동)

Windows agent 빌드는 agent repo(`assessment-agent/windows-agent`) 책임이며 `dev-up.sh` 가 자동 수행한다
(수동 개입 불요). `check_win_prereqs` 가 mingw-w64·cmake 를 자동 설치하고, `ensure_win_vendor` 가 vendor
static libs(openssl/zlib/cjson/rabbitmq/curl)를 없을 때만 크로스빌드(최초 1회 ~10min, 이후 캐시),
`build_win_agent` 가 mingw incremental 빌드로 `dist/assessment-agent.exe` 산출. repo·toolchain 부재 시에만 skip.

cross-compile 정합 핵심 (Ubuntu mingw-w64 환경 — `ensure_win_vendor` 가 windows-agent Makefile 에 주입):
- openssl: `OPENSSL_CROSS=--cross-compile-prefix=x86_64-w64-mingw32-` (windres 까지 prefix 적용 — 미적용 시 build 단계 windres not found).
- cmake(cjson/rabbitmq/curl): `CMAKE_SYSTEM_NAME=Windows`(ws2_32 getaddrinfo 인식) + OpenSSL 경로 직접 지정(`OPENSSL_*_LIBRARY`) + find-root `BOTH`(vendor openssl 이 /usr root 밖이라). Makefile 의 cmake build 줄은 plain `cmake --build` 라 위 -D 가 build 단계 `-j` 를 안 깬다.
- 링크: rabbitmq-c 의 OpenSSL threading 콜백용 `-lwinpthread` (windows-agent Makefile LDLIBS — 미포함 시 pthread_mutex undefined).

수동 빌드(디버깅용)가 필요하면:
```bash
cd ../assessment-agent/windows-agent
make vendor-build CC=x86_64-w64-mingw32-gcc AR=x86_64-w64-mingw32-ar \
  OPENSSL_CROSS=--cross-compile-prefix=x86_64-w64-mingw32- \
  CMAKE="cmake -DCMAKE_SYSTEM_NAME=Windows -DCMAKE_RC_COMPILER=x86_64-w64-mingw32-windres -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=BOTH"
make release CC=x86_64-w64-mingw32-gcc AR=x86_64-w64-mingw32-ar   # dist/assessment-agent.exe
```

산출물 `assessment-agent.exe`(PE32+ x86-64 정적 링크). x86 Server 에서 네이티브 실행(Prism emulation 불요 —
UTM ARM 시절 대비 단순). `deploy_win_agent` 가 staging 경로(`agent.exe.new`) scp -> 서비스 정지 -> 진짜 경로
교체 -> 서비스 미등록이면 `New-Service` + `sc.exe failure` recovery. agent.env 위치·키는 Linux 와 동일
(`dev/agent.env.example`), `RABBITMQ_HOST`·`WORKER_DOWNLOAD_ALLOWED_HOSTS` 만 게이트웨이 IP.

## 검증

```bash
WIN_ENABLE=1 ./dev/dev-up.sh         # 최초: ISO 다운로드 + 무인 설치(~20min) + agent/IIS/redis
```

1. 도메인·IP: `virsh list --all` + `virsh domifaddr win-server-01 --source lease`.
2. 설치 진행(설치 중 SSH 전): `virsh screenshot win-server-01 /tmp/w.png` (PNG) 로 화면 확인.
3. 서비스: `ssh -i dev/.ssh/id_dev Administrator@<vm-ip> 'Get-Service assessment-agent,W3SVC,redis'`.
4. RabbitMQ 콘솔(http://localhost:15672)에서 win-server-01 메시지 적재.
5. web UI(http://localhost:8000/servers/)에서 win-server-01 등록 + `os_family=windows` + 분류(IIS=web, redis=cache)
   + Windows nullable 필드(load_avg·swap 의미) 표시.

engine 미기동 상태에선 agent 서비스가 Running 이라도 RabbitMQ 연결 실패(재시도)가 정상 — `./dev/dev-up.sh`
전체 기동(web healthy) 후 발행 확인.

## env override

| 변수 | 기본 | 용도 |
|------|------|------|
| `WIN_ENABLE` | `0` | `1` 이면 Windows 파이프라인 실행 (기본 skip) |
| `WIN_VM_NAME` | `win-server-01` | libvirt 도메인 이름 |
| `WIN_SSH_USER` | `Administrator` | OpenSSH 계정 (Server 내장 관리자) |
| `WIN_ADMIN_PASS` | `Assess!Dev2026` | built-in Administrator 비밀번호 (dev) |
| `WIN_MEM_MIB` / `WIN_VCPU` / `WIN_DISK_GB` | `4096` / `4` / `64` | VM 리소스 |
| `WIN_ISO_URL` | Server 2022 eval fwlink | ISO 출처 (2025 등 교체 가능) |
| `WIN_SSH_CAP` | `2400` | 신규 설치 SSH 대기 cap(초) |
| `WIN_OVMF_CODE` / `WIN_OVMF_VARS` | `/usr/share/OVMF/OVMF_CODE_4M.fd` 등 | UEFI 펌웨어 경로 |
| `WIN_AGENT_REPO` | `../assessment-agent/windows-agent` | mingw 빌드 sibling repo |

## 종료 / lifecycle

`dev-down.sh` 는 Windows VM 을 `virsh shutdown`(보존, undefine 아님) — 설치 비용(~20min)이 커 stop/start 보존
(Linux VM 은 재생성 전제로 undefine). 수동:

```bash
virsh shutdown win-server-01                       # graceful 정지
virsh start win-server-01                          # 재기동 (dev-up.sh 재실행 시 agent/서비스 갱신)
virsh domifaddr win-server-01 --source lease       # VM IP
virsh screenshot win-server-01 /tmp/w.png          # 화면 (설치/디버깅)
virsh undefine win-server-01 --remove-all-storage --nvram   # 완전 제거 (재설치 전제)
```

## 한계 / 주의

- 최초 설치 ~20min(무인) + ISO ~4.7GB 다운로드 1회 — 이후 골든 이미지 clone 으로 재사용(설치 skip).
  dev-up 기본 포함이며 `WIN_ENABLE=0` 으로 opt-out(Linux 전용).
- Server 2022 Eval = 180일. 만료 후 `slmgr /rearm` 또는 재설치.
- autounattend `/IMAGE/INDEX`·FirstLogonCommands 는 Win 빌드별 미세 차이 가능 — 설치 실패 시 screenshot 으로
  단계 확인 후 템플릿 보정.
- SATA·e1000e(성능 < virtio) — agent 검증엔 무영향, 성능 마이크로벤치마크엔 부적합.
- IIS(`w3svc`)/MSSQL(`mssqlserver`) 등 Windows native 서비스 분류는 `service_classifier` 카탈로그 확장 필요
  (#E7). redis(tporadowski, 서비스명 `redis`)는 현재도 분류됨.
- OpenSSH capability 설치가 Windows Update 의존 — VM outbound(NAT) 필수. 폐쇄망은 FoD ISO 별도.
