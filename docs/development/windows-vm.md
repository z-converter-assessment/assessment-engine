# Windows VM 파이프라인 검증 (UTM)

본 문서는 dev 파이프라인의 Windows agent 검증 단일 진실. macOS + Apple Silicon 한정 (#A0).
Linux VM 3대는 OrbStack (`docs/development/pipeline.md`), Windows VM 1대는 UTM 으로 분리한다.

## 왜 UTM (OrbStack 이 아니라)

- OrbStack 은 macOS·Linux guest 만 지원 (Apple Virtualization.framework wrap). Windows guest 불가.
- UTM 은 QEMU(emulate) + Apple Virtualization(virtualize) 두 백엔드를 갖춰 Windows guest 가능.
- Apple Silicon 에서 x86_64 Windows 는 QEMU TCG emulate 라 10~30배 느려 dev 부적합 → ARM Windows 를 Virtualize 백엔드로.
- Windows Server 2022/2025 ARM64 공식 ISO 부재 (Microsoft Evaluation Center 미제공, community UUP 도 불안정).
  -> Windows 11 ARM 24H2 retail (CrystalFetch `A64FRE` 빌드, 무료) 채택. agent 가 호출하는 Win32 API
  (`GetSystemTimes`·`GlobalMemoryStatusEx`·`IOCTL_DISK_PERFORMANCE`·`GetExtendedTcpTable`)·SCM·
  Performance Counter 는 Server 와 동일 → 페이로드·스키마 정합 검증 100% 커버. Server-only role
  (AD/DC 등)은 본 agent 의존 밖.

## VM 역할 (4 VM 매트릭스 중 1대)

| VM | 가상화 | 서비스 (2개) | 카테고리 | 비고 |
|----|--------|--------------|----------|------|
| win-server-01 | UTM (Win11 ARM) | IIS (`w3svc`, native) | web | Windows native role |
|               |                 | redis (크로스플랫폼) | cache | tporadowski redis (서비스명 `redis`) |

서비스 혼합 의도: native role (IIS) + 크로스플랫폼 서비스 (redis) 양쪽이 `service_classifier`
(`web`/`cache`)에 올바로 분류되는지 검증. IIS 의 SCM unit 명은 `W3SVC` (display "World Wide Web
Publishing Service") — agent 가 SCM 에서 수집하는 service unit 이 `service_classifier._PATTERNS`
의 `nginx`/`httpd` 등 Linux 키워드와 다르므로, IIS(`w3svc`)·SQL Server(`mssqlserver`) 등 Windows
native 서비스가 분류 안 될 수 있다. 이 경우 `service_classifier` 카탈로그 확장이 별도 결정 사항
(#E7, 코드 변경 트리거) — 본 문서는 wire 검증까지, 분류 카탈로그 확장은 UI 부정합 작업 단계에서.

## 사용자 수동 단계 (자동화 불가)

UTM VM 생성·Windows 설치는 GUI 절차라 스크립트화 불가. 아래는 사용자 직접 수행.

### 1. Windows 11 ARM ISO 입수

- CrystalFetch 권장 (확정): `brew install --cask crystalfetch` -> 앱에서 Windows 11 / ARM64 선택 ->
  Download + Build ISO. UUP(=Windows Update CDN, Microsoft 인프라)에서 aria2 멀티커넥션이라 공식
  페이지 단일 HTTPS 보다 빠름. 산출물 예: `26100.4349..._A64FRE_en-us.iso` (`A64FRE` = ARM64 표식).
- 또는 Microsoft 공식: https://www.microsoft.com/software-download/windows11arm64
- arm64 빌드 확인 필수 (amd64 는 Intel Mac 용 — Apple Silicon 에서 Virtualize 불가).

### 2. UTM VM 생성

UTM GUI 에서:
1. "+" -> Virtualize -> Windows
2. "Windows 10 이상 설치" 체크 + ISO (위 ARM64) 를 boot 매체로 선택. "드라이버 및 SPICE 도구 설치" 체크 유지
   (VirtIO 드라이버 + 게스트 도구 자동 마운트 — 동적 해상도·클립보드. 단 utmctl ip-address 용 QEMU 게스트
   에이전트는 SPICE 도구 설치 후에야 동작)
3. TPM 2.0 + UEFI Secure Boot 활성 (Windows 11 요구 — 미설정 시 설치 거부)
4. CPU 코어 4+ / RAM 4~8GB (Win11 ARM 데스크톱 부하 — 4GB 도 agent 검증 충분) / 디스크 64GB+
5. VM 이름 `win-server-01` (utmctl 식별자 — win-pipeline.sh 가 이 이름으로 찾음)

부팅 시 UEFI Interactive Shell 로 빠지면 (ISO 자동 부팅 1초 타이밍 놓침), Shell 프롬프트에서 ARM64
부트로더 직접 실행: `FS0:\EFI\BOOT\BOOTAA64.EFI` (FS0 = CDROM). 설치 후엔 디스크 부팅이라 1회만.

### 3. Windows 설치 + 초기 설정

설치 마법사에서 신경 쓸 2가지 (나머지는 다음다음): 제품 키 = "없음" 선택, 계정 = 로컬 계정.
Windows 11 24H2 가 MS 계정 로그인을 강요하므로 계정 화면에서 `Shift+F10` -> `start ms-cxh:localonly`
로 로컬 계정 생성 화면 진입 (구버전은 `OOBE\BYPASSNRO`). 계정명·비번 기억 (SSH 계정 — dev 는 `test`).

설치 후 VM 안 PowerShell 관리자에서 (SSH 뚫리기 전이라 복붙 불가 — 직접 타이핑):

```powershell
# OpenSSH Server 활성 (~~~~0.0.1.0 의 물결 4개 주의). Add-WindowsCapability 는 Windows Update
# 에서 ARM64 빌드 받아 x64 emulation 으로 설치 — 1~5분 정상 소요.
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service sshd -StartupType Automatic
```

방화벽: OpenSSH 설치가 22 inbound 규칙을 자동 생성하지 않는 경우가 있어 (확정 관찰) 22 가 막힌다.
dev 격리 VM 이라 프로파일 전체 off 가 가장 빠름:

```powershell
Set-NetFirewallProfile -Enabled False
```

이후는 host 에서 ssh 로 처리 가능. host 공개키 등록 + DefaultShell 변경은 비번 1회만 있으면 host 에서
`expect` 로 자동화된다 (수동 타이핑 불필요). 핵심:
- 키 위치: 계정이 Administrators 멤버면 `~/.ssh/authorized_keys` 가 아닌
  `C:\ProgramData\ssh\administrators_authorized_keys` + ACL (`Administrators:F`,`SYSTEM:F`). dev `test`
  계정은 첫 계정이라 관리자 -> 이 경로.
- DefaultShell -> PowerShell (이후 ssh 명령이 cmd 가 아닌 powershell): 
  `reg add "HKLM\SOFTWARE\OpenSSH" /v DefaultShell /t REG_SZ /d "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" /f`

## 네트워크 — host 도달 (host.docker.internal 불가)

OrbStack 의 `host.docker.internal` 은 OrbStack 전용 — UTM VM 은 미해석. UTM Shared Network
모드에서 host 의 실제 IP 를 직접 써야 한다.

```
UTM VM (win-server-01, Win11 ARM)
  RABBITMQ_HOST = <host IP>   ->  host:5672 (docker compose rabbitmq 포트 매핑)
  download.url host = <host IP>:8000  ->  host:8000 (dev ZDM mock, ADR 0018)
```

host IP (확정): UTM Shared Network 에서 host 는 VM subnet 의 gateway. `en0`(Wi-Fi LAN IP)이 아니다
— en0 은 NAT 밖이라 VM 에서 미도달. host 의 bridge 인터페이스(예: `bridge101`)가 `192.168.64.1`,
VM 이 `192.168.64.2` 라면 RABBITMQ_HOST = `192.168.64.1`.

확인:
- host: `ifconfig | grep "inet 192.168.64"` -> `192.168.64.1` (VM subnet 의 host inet = gateway)
- VM: `(Get-NetIPConfiguration).IPv4DefaultGateway.NextHop`
- win-pipeline.sh `resolve_host_ip` 가 VM IP 의 /24 에서 host bridge inet 을 자동 채택 (en0 은 3순위 fallback)

docker compose 의 rabbitmq/web 은 `0.0.0.0` 바인딩 (포트 매핑 `5672:5672`·`8000:8000`)이라 gateway IP 로
VM 에서 직접 도달. agent 의 `WORKER_DOWNLOAD_ALLOWED_HOSTS` 에 host IP 추가 의무 (ZDM mock fetch).

## agent (Windows) 빌드 — macOS host cross-compile (확정)

Windows agent 빌드는 agent repo (`assessment-agent/windows-agent`) 책임. 본 engine repo 의
`ensure_agent_binary` 는 Linux arm64 만 확보 — Windows .exe 는 아래 별도 흐름.

빌드 경로는 macOS host cross-compile 로 확정. 당초 후보였던 "VM 안 `build-windows.ps1`"
(MSYS2 self-bootstrap)는 ARM64 Windows 11 에서 불가 — MSYS2 installer 가 x64 emulation 에서
빈 디렉토리만 만들고 실패하는 [알려진 이슈](https://github.com/msys2/msys2-installer/issues/96)
(`Install size: 0 components`). host cross-compile 은 emulation 을 통째로 회피한다.

### 사전: mingw-w64 toolchain

```bash
brew install mingw-w64   # x86_64-w64-mingw32-gcc / -ar / -windres (GCC 15.x)
```

### vendor 5종 + agent 빌드

```bash
cd ../assessment-agent/windows-agent

# 1. vendor 소스 clone (cJSON·rabbitmq-c·curl·openssl·zlib)
make vendor-fetch

# 2. OpenSSL·zlib — cross prefix 로 그대로 빌드 (cmake 아님, 표준 cross 지원)
#    cJSON·rabbitmq-c·curl — cmake. macOS cmake 가 CMAKE_SYSTEM_NAME 미지정 시 Darwin 빌드로
#    오판해 cross gcc 에 `-arch arm64` 를 주입 → mingw gcc 거부. 아래 wrapper 로 우회.
cat > /tmp/cmake-cross.sh <<'EOF'
#!/bin/sh
# configure(-S/-B)엔 cross 옵션 주입, --build 모드면 그대로 통과 (-D 와 --build 공존 불가).
for a in "$@"; do
  if [ "$a" = "--build" ]; then exec "$(which cmake)" "$@"; fi
done
exec "$(which cmake)" -DCMAKE_SYSTEM_NAME=Windows -DCMAKE_RC_COMPILER=x86_64-w64-mingw32-windres "$@"
EOF
chmod +x /tmp/cmake-cross.sh

make vendor-build \
  CC=x86_64-w64-mingw32-gcc AR=x86_64-w64-mingw32-ar \
  OPENSSL_TARGET=mingw64 OPENSSL_CROSS=--cross-compile-prefix=x86_64-w64-mingw32- \
  CMAKE=/tmp/cmake-cross.sh

# 3. agent.exe 링크
make CC=x86_64-w64-mingw32-gcc AR=x86_64-w64-mingw32-ar
```

산출물 `assessment-agent.exe` (PE32+ x86-64, 정적 링크, ~6.5MB). Win11 ARM 의 내장 x64
emulation(Prism)으로 실행. cmake build 단계가 `-j` 인자 때문에 `-D` 와 충돌하므로 wrapper 가
`--build` 면 옵션을 안 붙이는 게 핵심.

### 배포·서비스 등록

`deploy/install.ps1` 은 `env-setup.ps1` 대화형 프롬프트를 호출해 비대화형 ssh 에서 멈춘다.
`dev/win-pipeline.sh` 가 agent.env 를 어차피 덮어쓰므로, 최초 등록은 install.ps1 대신 핵심만 직접
실행하는 게 자동화에 맞다 (exe 복사 + `New-Service` + `sc.exe failure` recovery). win-pipeline.sh
가 host 에서 ssh 로 수행. agent 가 읽는 env 위치·키 카탈로그는 Linux 와 동일 (`dev/agent.env.example`),
`RABBITMQ_HOST` 만 host IP (host.docker.internal 불가, 아래 네트워크 절).

dist 패키징 (install.ps1 정식 경로를 쓸 경우): `dist/assessment-agent.exe` + `SHA256SUMS`
(`shasum -a 256 assessment-agent.exe > SHA256SUMS`) 를 VM 으로 scp 후 install.ps1. 단 env-setup
대화형 회피 위해 agent.env 를 미리 완성 배치해야 한다.

## 서비스 설치 (IIS native + redis 크로스플랫폼)

IIS — `Enable-WindowsOptionalFeature` 후 `RestartNeeded=True`. 재부팅해야 W3SVC 가 활성화된다
(재부팅 전엔 W3SVC 서비스 미등록):

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName IIS-WebServerRole -All -NoRestart
Restart-Computer -Force   # 재부팅 후 W3SVC = Running. sshd Automatic·방화벽 off·SSH키 영구라 자동 복구
```

redis — Memurai 는 winget(`Memurai.MemuraiDeveloper`)이 비대화형 ssh 에서 silent 설치 실패(확정,
exit 0 이나 미설치) + 서비스명이 `memurai` 라 `service_classifier` 의 `redis` 패턴에 매칭도 안 된다.
대신 tporadowski/redis (Windows 포트, GitHub release MSI) 채택 — 서비스명이 `redis` 라 분류 직결:

```bash
# host 에서 받아 VM scp (GitHub release, host 네트워크 안정)
curl -L -o /tmp/Redis-x64.msi \
  https://github.com/tporadowski/redis/releases/download/v5.0.14.1/Redis-x64-5.0.14.1.msi
scp /tmp/Redis-x64.msi test@<vm-ip>:C:/Users/test/AppData/Local/Temp/Redis-x64.msi
# VM 에서 무인 설치 (서비스 redis 등록·시작)
ssh test@<vm-ip> "Start-Process msiexec.exe -ArgumentList '/i','C:\Users\test\AppData\Local\Temp\Redis-x64.msi','/qn','/norestart' -Wait"
```

설치 후 agent 가 SCM 에서 `W3SVC`·`Redis`·`assessment-agent` service unit 수집 → inventory 발행.
`Redis` 는 소문자화 매칭으로 `cache` 분류되지만, `W3SVC`(IIS)는 `service_classifier._PATTERNS` 에
`w3svc`/`iis` 키워드가 없어 분류 안 됨 — 카탈로그 확장 별도 결정 (#E7, UI 작업 단계).

## utmctl 라이프사이클 (자동화 가능 부분)

VM 생성·설치 후 lifecycle 은 utmctl 로 자동화. `utmctl` 은 `/opt/homebrew/bin/utmctl` symlink (UTM.app 번들).

```bash
utmctl list                          # VM 목록·상태
utmctl status win-server-01          # 전원 상태 (started/stopped) — 게스트 에이전트 불필요
utmctl start win-server-01           # 기동
utmctl ip-address win-server-01      # guest IP — QEMU 게스트 에이전트(SPICE 도구) 설치 시에만 동작
utmctl stop win-server-01            # 종료
utmctl exec win-server-01 -- <cmd>   # guest 명령 (게스트 에이전트 필요) — OpenSSH 권장
```

UTM 앱 자체는 열려 있어야 함 (`open -a UTM`). headless 는 VM display device 제거 의미이지 앱
daemon 아님.

게스트 에이전트 미설치 시 `utmctl ip-address` 가 `OSStatus error -2700` 으로 실패한다 (확정). 이때
VM IP 는 host ARP (`arp -an | grep 192.168.6`)로 확인하거나 win-pipeline.sh `WIN_VM_IP` env 로 직접
지정. win-pipeline.sh 는 utmctl 실패 시 ARP fallback 을 자동 시도한다.

## 부분 자동화 (dev/win-pipeline.sh)

VM 생성·Windows 설치·최초 서비스 등록은 1회 수동 (위 절), 이후 lifecycle 은 `dev/win-pipeline.sh` 가
자동. OrbStack 의 `pipeline-up.sh` 와 분리 — Windows 는 가상화(UTM)·채널(OpenSSH)·네트워크(host IP)가
달라 별도 스크립트.

```
[1회 수동]  UTM VM 생성·설치(GUI) -> OpenSSH+방화벽off(VM 안 직접)
[host ssh]   SSH키 등록(expect) -> agent.exe cross-build(macOS) -> scp -> 서비스 등록 -> IIS/redis
[자동 반복]  ./dev/win-pipeline.sh
              1. utmctl start win-server-01 (UTM 앱 미기동 시 open -a UTM)
              2. VM IP (utmctl, 실패 시 ARP) + host IP (VM subnet gateway)
              3. agent.env 생성 (dev/agent.env + RABBITMQ_HOST=host IP) -> scp 덮어쓰기
              4. 서비스 restart (Restart-Service assessment-agent)
```

env override:

| 변수 | 기본 | 용도 |
|------|------|------|
| `WIN_VM_NAME` | `win-server-01` | UTM VM 이름 (utmctl 식별자) |
| `WIN_SSH_USER` | `test` | OpenSSH 계정 (dev VM — 다른 환경은 override) |
| `WIN_VM_IP` | (자동) | VM IP 직접 지정 (게스트 에이전트 미설치로 utmctl 실패 시) |
| `WIN_HOST_IP` | (자동) | host IP 직접 지정 (bridged network 등 비표준) |
| `WIN_HOST_IFACE` | `en0` | 3순위 fallback (비-NAT 환경에서만 유효) |
| `UTMCTL` | `utmctl` | utmctl 경로 (symlink 미설정 시 풀 경로) |

전제: VM 안 OpenSSH 에 host 의 SSH 공개키 등록 (비대화형 scp/ssh). VM 미존재 시 스크립트가 수동 절차
안내 후 종료 (UTM VM 생성은 GUI 라 자동화 불가). `dev/agent.env` 의 `RABBITMQ_*`·`WORKER_*` 키 재사용
(`pipeline-up.sh` `load_agent_env`) — Linux agent 와 동일 secret 채널.

왜 매 실행 agent.env 덮어쓰기: host IP 가 네트워크(Wi-Fi/Ethernet·DHCP)마다 바뀌므로. install.ps1 의
idempotent env 보존과 달리 win-pipeline.sh 는 host 가 master (Linux `/etc/assessment-agent.env` heredoc 대응).

## 검증

1. VM IP 확인 (`arp -an | grep 192.168.6` 또는 `WIN_VM_IP`).
2. agent 서비스 확인: `ssh test@<vm-ip> "Get-Service assessment-agent,W3SVC,Redis"`.
3. host RabbitMQ 관리 콘솔 (http://localhost:15672) 에서 win-server-01 메시지 적재 확인.
4. web UI (http://localhost:8000/servers/) 에서 win-server-01 등록 + `os_family=windows` 표시.
5. inventory 의 service 분류 (redis=cache) + Windows nullable 필드 (load_avg·swap 의미) UI 표시 확인.

engine 미기동 상태에선 agent 서비스가 Running 이라도 RABBITMQ 연결 실패(재시도) 가 정상 — engine
(`./dev/pipeline-up.sh`) + `./dev/win-pipeline.sh`(완전한 agent.env 주입) 후 발행 확인.

## 한계 / 주의

- Windows 설치·VM 생성은 GUI 수동 — 완전 자동화 불가 (라이선스·설치 마법사). OpenSSH+방화벽off 까지만 VM 안
  직접, 이후는 host ssh.
- ARM64 Windows Server 공식 ISO 부재 -> Windows 11 ARM. Server-only role 미사용이라 검증 커버.
- ARM Windows 에서 MSYS2 빌드 불가 -> macOS host cross-compile (위 빌드 절). emulation 회피.
- host IP = UTM gateway(VM subnet `.1`). en0(Wi-Fi LAN)은 NAT 밖이라 VM 미도달. DHCP·네트워크 전환 시
  subnet 바뀌면 win-pipeline.sh 가 매 실행 재추론 후 agent.env 갱신.
- x64 emulation(Prism) 위 agent 실행 — CPU jiffies 타이밍 정밀도가 native x86_64 와 미세 차이 가능.
  스키마·페이로드·UI 검증엔 무영향, 성능 마이크로벤치마크엔 부적합.
- IIS(`w3svc`)/MSSQL(`mssqlserver`) 등 Windows native 서비스 분류는 `service_classifier` 카탈로그
  확장 필요 (#E7). redis(tporadowski, 서비스명 `redis`)는 현재도 분류됨.
