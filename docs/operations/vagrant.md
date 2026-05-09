# Vagrant

## 사용 맥락

Vagrant는 에이전트 E2E 테스트를 위해 사용한다.

엔진(docker-compose)은 호스트 머신에서 실행된다. Vagrant는 에이전트를 실제 Linux 환경에서 돌릴 VM을 띄운다. 에이전트가 RabbitMQ에 메시지를 발행하면 consumer가 소비해 DB에 저장하고, web UI에서 결과를 확인하는 전체 파이프라인을 검증한다.

```
[VM: cache-server-01]  →  assessment-agent (systemd)
[VM: app-server-01  ]  →  assessment-agent (systemd)   →  RabbitMQ (호스트)  →  consumer  →  DB  →  web UI
[VM: web-server-01  ]  →  assessment-agent (systemd)
```

세 VM이 다른 박스(Ubuntu·Rocky·Debian)를 쓰는 이유: OS 계열별 패키지 관리자(apt/dnf) 분기와 systemd·SELinux 환경 차이를 동시에 검증하기 위해서다. 단일 박스만 쓰면 RPM/SELinux 측 회귀 버그를 놓친다.

---

## VM 매트릭스

`Vagrantfile` `VMS` 배열에 3개 VM이 정의되어 있다.

| VM | 박스 | 패키지 관리자 | 설치 서비스 | extra_mounts | ext_ip | 검증 의도 |
|----|------|---------------|------------|--------------|--------|----------|
| `cache-server-01` | `bento/ubuntu-22.04` | apt | redis-server | — | — | "cache" 카테고리 뱃지 + Ubuntu LTS 회귀 |
| `app-server-01` | `bento/rockylinux-9` | dnf | (없음) | `/data` | — | "unknown" 뱃지 + RPM 계열 + SELinux + 마운트 추가 |
| `web-server-01` | `bento/debian-12` | apt | nginx | — | `203.0.113.10` | "web" 카테고리 + 외부 IP 오버라이드 + Debian 회귀 |

리소스 (VM 공통): 1024MB RAM / 2 CPU / VirtualBox 오디오·USB·VRAM 최소화 (`Vagrantfile` provider 블록).

설정 분기:
- `family: :deb` 또는 `:rpm` → Step 1의 패키지 매니저 분기.
- `services: :redis | :nginx | :none` → Step 1-1 추가 서비스 설치.
- `extra_mounts: ["/data"]` → 추가 마운트 (현재 코드에서는 마운트 마운트 자체는 하지 않고 정의만 있음 — 실제로는 호스트 박스 기본 디스크 사용).
- `ext_ip: "203.0.113.10"` → `/etc/assessment-agent.env`에 `AGENT_EXTERNAL_IP=203.0.113.10` 주입. 에이전트가 외부 IP로 보고하면 web UI에 "External IP" 컬럼에 노출.

---

## 네트워크 구조

VM은 VirtualBox NAT 네트워크를 사용한다. VM → 호스트 방향은 NAT 게이트웨이 주소 `10.0.2.2`를 통한다.

```
VM (assessment-agent)
  RABBITMQ_HOST=10.0.2.2  →  호스트:5672 (docker-compose rabbitmq 포트 매핑)
```

에이전트 `.env`에 `RABBITMQ_HOST=10.0.2.2`를 고정으로 주입한다. 엔진 `.env`의 `RABBITMQ_HOST`("rabbitmq" 도커 서비스명)와 다르며, `Vagrantfile` 상단에 별도 상수로 분리되어 있다.

```ruby
# Vagrantfile:7
RABBITMQ_HOST = "10.0.2.2"
```

이 값을 호스트의 docker-compose 포트 매핑(`${RABBITMQ_PORT:-5672}:5672`)이 받아 컨테이너로 포워딩한다.

VM 간 통신은 사용 안 함. 각 VM은 독립적으로 동작하며 서로의 존재를 모름. 모든 통신은 호스트의 RabbitMQ를 경유한다.

---

## Provisioning 단계

`vagrant up` 시 각 VM에서 아래 순서로 실행된다.

### Step 1. 빌드 의존성 설치 (family 분기)

| family | 명령 |
|--------|------|
| `:deb` | `apt-get install -y --no-install-recommends gcc make pkg-config libc6-dev librabbitmq-dev libcjson-dev` |
| `:rpm` | `dnf install -y epel-release dnf-plugins-core` → `dnf config-manager --set-enabled crb` → `dnf install -y gcc make pkg-config librabbitmq-devel cjson-devel` |

Rocky 9는 `librabbitmq-devel`이 EPEL/CRB 저장소에 있어 활성화 필수.

### Step 1-1. 추가 서비스 (services 분기)

```bash
# :redis
apt-get install -y --no-install-recommends redis-server
systemctl enable redis-server && systemctl start redis-server

# :nginx
apt-get install -y --no-install-recommends nginx
systemctl enable nginx && systemctl start nginx
```

이렇게 설치된 redis/nginx가 systemd unit으로 동작 → 에이전트가 `services[]`에 `redis-server.service` / `nginx.service`를 포함시켜 발행 → 엔진의 `service_classifier.classify()`가 "cache" / "web" 카테고리로 분류.

### Step 2. `/etc/assessment-agent.env` 생성

```bash
cat > /etc/assessment-agent.env <<EOF
RABBITMQ_HOST=10.0.2.2
RABBITMQ_PORT=5672
RABBITMQ_VHOST=/
RABBITMQ_USER=assessment
RABBITMQ_PASS=assessment
RABBITMQ_EXCHANGE=assessment
RABBITMQ_ROUTING_KEY_INVENTORY=server.inventory
RABBITMQ_ROUTING_KEY_METRICS=server.metrics
RABBITMQ_ROUTING_KEY_ERROR=server.error
AGENT_HOSTNAME_OVERRIDE=cache-server-01     # VM별 다름
AGENT_INTERVAL_SEC=60
AGENT_EXTERNAL_IP=203.0.113.10              # web-server-01만
EOF
chmod 644 /etc/assessment-agent.env
```

`/etc/`에 두는 이유:
- synced_folder(`/home/vagrant/assessment-agent/`)는 호스트와 양방향 → VM별 값(`AGENT_HOSTNAME_OVERRIDE`) 분리 어려움.
- SELinux(Rocky 9)는 systemd가 `/home/vagrant/` 내부 파일을 `EnvironmentFile=`로 읽는 것을 차단.
- `/etc/`는 systemd가 자유롭게 읽을 수 있고 VM 로컬에 격리된다.

환경변수 의미:
- `AGENT_HOSTNAME_OVERRIDE`: hostname 필드 강제 — VM 박스의 기본 hostname 대신 사용. 메시지 hostname이 VM명과 일치하도록.
- `AGENT_INTERVAL_SEC=60`: metrics 발행 주기 60초.
- `AGENT_EXTERNAL_IP`: 클라우드 메타데이터 API 미접근 환경에서 외부 IP를 수동 주입.

### Step 3. 에이전트 빌드

```bash
cd /home/vagrant/assessment-agent && make
```

소스는 rsync synced_folder로 VM에 복사되어 있다 (다음 절). `make` 결과 바이너리가 `/home/vagrant/assessment-agent/assessment-agent`에 생성.

### Step 4. 바이너리 설치 + systemd 등록

```bash
cp /home/vagrant/assessment-agent/assessment-agent /usr/local/bin/
chmod 755 /usr/local/bin/assessment-agent
cat > /etc/systemd/system/assessment-agent.service <<'EOF'
[Unit]
Description=Assessment Agent
After=network.target

[Service]
User=vagrant
EnvironmentFile=/etc/assessment-agent.env
ExecStart=/usr/local/bin/assessment-agent
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable assessment-agent
systemctl start assessment-agent || true
```

바이너리를 `/usr/local/bin/`으로 복사하는 이유:
- VirtualBox 공유 폴더(vboxsf)는 SELinux(Rocky 9) 환경에서 systemd가 직접 실행 불가.
- rsync로 VM 내부에 들어왔지만 `/home/vagrant/` 권한·SELinux 컨텍스트가 systemd 실행에 부적합.
- `/usr/local/bin/`은 표준 실행 경로로 SELinux/AppArmor 모두 통과.

`Restart=on-failure RestartSec=10`:
- 비정상 종료(exit != 0) 시 10초 후 재시작.
- 에이전트가 broker 연결을 끝까지 포기하고 exit하면 재시작이 도움.
- 그러나 broker 재연결을 silent하게 포기(exit 안 함)할 때는 systemd가 재시작 트리거를 못 받음 — 운영 노트 참조.

---

## `.env` 연동

`Vagrantfile`은 루트 `.env`를 직접 파싱해 RabbitMQ 접속 정보를 읽는다.

```ruby
dot_env = {}
File.foreach(".env") do |line|
  line = line.strip
  next if line.empty? || line.start_with?("#")
  key, val = line.split("=", 2)
  dot_env[key] = val
end
RABBITMQ_USER = dot_env.fetch("RABBITMQ_USER", "assessment")
```

`.env` 변경 시 VM 환경변수 파일(`/etc/assessment-agent.env`) 갱신 트리거:

| 변경 시나리오 | 필요 명령 |
|---------------|-----------|
| 첫 기동 | `vagrant up` |
| `.env` 키만 변경 (Vagrantfile 변경 없음) | `vagrant provision` |
| `Vagrantfile` 자체 변경 (VM 추가/리소스 변경) | `vagrant reload --provision` 또는 `vagrant destroy && vagrant up` |
| 에이전트 소스만 변경 | `vagrant rsync && vagrant ssh <vm> -c "cd /home/vagrant/assessment-agent && make && sudo cp assessment-agent /usr/local/bin/ && sudo systemctl restart assessment-agent"` |

---

## synced_folder

```ruby
node.vm.synced_folder "../assessment-agent", "/home/vagrant/assessment-agent",
  type: "rsync",
  rsync__exclude: [".git/", "*.o", "*.a", "assessment-agent"]
```

`rsync` 타입 선택 이유:
- `vboxsf` (기본): SELinux 차단으로 systemd가 vboxsf 경유 바이너리 실행 못함.
- `nfs`: macOS 호스트에서 권한 설정 번거로움.
- `rsync`: `vagrant up` 시점 1회 단방향 복사. 이후 호스트 변경은 `vagrant rsync` 또는 `vagrant rsync-auto`로 동기화.

제외 패턴:
- `.git/`: 불필요 + 큰 디렉토리.
- `*.o`, `*.a`: 호스트 빌드 산출물 — VM에서 다시 빌드함.
- `assessment-agent`: 호스트 바이너리 (macOS Mach-O) — VM 리눅스에서 못 씀. VM에서 빌드한 ELF 바이너리를 호스트 바이너리로 덮어쓰지 않기 위해서.

---

## dev-up.sh / dev-down.sh

### dev-up.sh
```
[1/3] docker compose up -d --build         # 엔진 기동
[2/3] until web 헬스체크 (최대 120s)        # 스키마 생성 완료 대기
[3/3] vagrant up                            # VM + 에이전트
```

`vagrant up`이 web 헬스체크 통과 후에 호출되는 이유: 에이전트가 처음 inventory를 발행할 때 RabbitMQ는 healthy여야 하고, consumer는 web 헬스체크 통과 후 시작하므로 inventory 처리 가능 상태가 되어야 함.

### dev-down.sh
```
[1/2] vagrant destroy -f                    # VM 제거 (VM이 존재할 때만)
[2/2] docker compose down -v                # 엔진 + 볼륨 제거
```

순서 중요: VM을 먼저 죽여야 broker가 사라진 뒤 에이전트가 silent하게 publish 실패 로그를 쌓지 않는다.

---

## 운영 노트 / 트러블슈팅

### broker 재기동 시 에이전트 수동 재시작 (CRITICAL)

증상: docker compose의 RabbitMQ를 down/up 또는 `down -v` 후 재기동하면 VM 안 C 에이전트가 broker 재연결을 silent하게 포기. systemd 상태는 `active(running)`이지만 publish 로그가 끊김.

대응:
```bash
for vm in cache-server-01 app-server-01 web-server-01; do
  vagrant ssh $vm -c "sudo systemctl restart assessment-agent"
done
```

원인: C 에이전트 publish 루프에 `connect_robust` 같은 자동 재연결 없음. exit하지 않고 silent retry만 하므로 systemd `Restart=on-failure`도 트리거되지 않음.

### VM 시간 동기화

`collected_at` 필드는 VM 로컬 시각이다. 호스트와 VM이 어긋나면 차트의 시간축이 맞지 않음. VirtualBox는 기본적으로 호스트와 시간 동기화하지만, 장시간 절전·suspend 후 재개 시 어긋날 수 있다.

```bash
# 모든 VM의 시간 강제 동기화
for vm in cache-server-01 app-server-01 web-server-01; do
  vagrant ssh $vm -c "sudo systemctl restart systemd-timesyncd 2>/dev/null || sudo systemctl restart chronyd"
done
```

### 에이전트 로그 확인

```bash
vagrant ssh cache-server-01 -c "sudo journalctl -u assessment-agent --no-pager -n 50"
```

기대 로그 (정상):
```
[agent] cmd lsblk         available
[agent] cmd curl          available
[agent] cmd dbus-uuidgen  available
[agent] machine_id=f1e90cdc43d54cc88d0a42e3de1d409b
[agent] published inventory
[agent] loop mode: interval=60s (Ctrl+C to exit)
```

이후 60초 주기로 publish 로그가 추가되어야 정상. 로그가 멈춰 있으면 broker 재연결 실패 의심.

### 첫 기동 시간

| 단계 | 예상 시간 |
|------|-----------|
| `docker compose up --build -d` (첫 빌드) | 60–120s |
| web 헬스체크 통과 | 10–20s |
| `vagrant up` (VM 3대, 박스 다운로드 포함) | 5–15분 |
| `vagrant up` (박스 캐시된 후) | 2–5분 |
| 에이전트 첫 inventory 도달 | 즉시 |
| 첫 metrics 차트 그려짐 (delta 계산용 2회 readings) | 60–90초 |

### 흔한 트러블

| 증상 | 원인 | 해결 |
|------|------|------|
| `vagrant up` 중 box 다운로드 실패 | 네트워크 / Vagrant Cloud 일시 오류 | 재시도 |
| `librabbitmq-devel` not found (Rocky) | EPEL/CRB 저장소 미활성화 | provisioning Step 1이 자동 처리 — `vagrant provision` 재실행 |
| 에이전트 publish 실패 로그 (CONNREFUSED) | 호스트 docker rabbitmq가 안 떠 있음 | `docker compose ps rabbitmq` 확인 후 기동 |
| consumer가 metrics를 받지만 server_inventory가 비어 있음 | inventory 메시지 유실 (broker 재기동 등) | VM 안 에이전트 `systemctl restart assessment-agent` |
| `vagrant rsync-auto`가 동작 안 함 | 호스트 inotify 라이밋 | 한 번씩 `vagrant rsync` 수동 실행 또는 OS 한계 조정 |
| Apple Silicon에서 `bento/rockylinux-9` 부팅 실패 | bento arm64 이미지가 이전엔 없었음 | VirtualBox 7.1+ 사용 (ARM 지원) |

### 개별 VM 조작

```bash
vagrant up cache-server-01              # 단일 VM만 기동
vagrant ssh app-server-01               # SSH 접속
vagrant halt web-server-01              # 종료 (제거하지 않음)
vagrant destroy -f cache-server-01      # 제거
vagrant provision app-server-01         # 프로비저닝만 재실행
vagrant reload --provision              # 재부팅 + 프로비저닝
```

단일 VM 시나리오가 필요하면 `Vagrantfile`의 `VMS` 배열에서 일부 항목을 주석 처리하거나 위 명령으로 개별 기동.