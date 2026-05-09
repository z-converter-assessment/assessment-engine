# 에이전트 스키마 계약

C 기반 에이전트가 RabbitMQ에 발행하는 메시지 스키마. `agent_version` 필드가 계약 버전 역할을 한다.

> 정식 정의는 별도 레포의 `assessment-agent/docs/payload-schema.md`. 본 문서는 엔진 측 핸들링 관점 요약 + 엔진의 책임/무시 항목.
> 현재 엔진이 호환하는 스키마: v3 (2026-05-06) — `services[]` / `listen_ports[]` / `error.{retry_count, first_failed_at, recovered_at}` 옵셔널 필드 포함.

---

## 메시지 타입

3가지 routing key로 분기. 모든 메시지에 공통 메타데이터 포함.

### 공통 메타데이터

| 필드 | 설명 |
|------|------|
| `message_type` | "inventory" / "metrics" / "error" |
| `machine_id` | `/etc/machine-id` 기준. 표준 Linux 32 hex, 가상화 환경 UUID(하이픈 포함 36자) 가능. DB max 64자 |
| `agent_version` | 빌드 시 정의. 계약 버전 역할 |
| `collected_at` | ISO 8601 UTC |
| `hostname` | 보조 식별자 (가변) |
| `message_id` | UUID v4 (멱등성 키) |

### server.inventory (기동 시 1회)

정적 인프라 정보.

| 필드 | 설명 |
|------|------|
| OS·kernel | `os_id`, `os_version`, `os_codename`, `kernel_version` |
| CPU | `cpu_cores`, `cpu_model` |
| 메모리·스왑 | 총량 (KB 단위) |
| `disks[]` | `{name, size_bytes, type}` |
| `mounts[]` | `{mount, fstype, total_bytes, free_bytes, avail_bytes}` — `free_bytes`/`avail_bytes`는 포털에서 무시, 동적 사용량은 metrics에서 별도 수집 |
| `ip_internal[]` / `ip_external[]` | IP 주소 목록 |
| `boot_time` | 부팅 시각 |
| `services[]` | `{unit, sub}` — systemctl 수집. non-systemd 호스트는 `null` |
| `listen_ports[]` | `{proto, addr, port, uid, pid, comm}` — 수집 실패 시 빈 배열 |

### server.metrics (1분 주기)

모두 raw 누적값. 포털이 연속 2회 readings의 차(delta)로 CPU%·IOPS·kBps를 계산한다.

| 필드 | 설명 |
|------|------|
| `cpu_stat` | user/system/idle/iowait/… jiffies |
| 메모리·스왑 | 총량·free·available·buffers·cached |
| `load_1m` / `load_5m` / `load_15m` | Load Average |
| `disk_io[]` | per device — reads/writes_completed, sectors |
| `mounts[]` | per mount — 현재 사용량 (total/avail bytes) |
| `net_io[]` | per interface — rx/tx bytes·packets·errors |

### server.error

에이전트 측 수집·발행 실패.

| 필드 | 타입 | 설명 |
|------|------|------|
| `error_code` | string | 분류 코드 (예: `COLLECT_MEMINFO_FAILED`, `PUBLISH_RETRY_EXHAUSTED`) |
| `error_message` | string | 사람이 읽을 수 있는 상세 |
| `failed_component` | `"collect"` / `"publish"` | 실패 단계 |
| `retry_count` | int \| null | 재시도 요약 보고 시점에만 |
| `first_failed_at` | datetime \| null | 재시도 요약 보고 시점에만 |
| `recovered_at` | datetime \| null | 복구 보고 시점에만 |

엔진 처리: 파싱 + 멱등성 체크 후 로깅만 수행. DB 저장 없음. 재시도 컨텍스트(`retry_count` 등)도 같이 로그 (`src/assessment_engine/consumer/handler.py` `make_error_handler`).

### 규약

- 단위: 메모리 = `kb`, 디스크/네트워크 = `bytes` (`/proc` 출력 관례)
- 옵셔널 필드: 수집 실패 시 `null` 전송. 수집 실패와 데이터 없음 미구분
- counter reset: 재부팅·에이전트 재시작 시 카운터 0 리셋. 포털은 delta < 0이면 `None` 처리 (UI에서 "—"). boot_time 기반 delta 건너뛰기는 미구현

### 엔진이 받지만 사용하지 않는 필드

다음 필드는 에이전트가 발행하지만 엔진 핸들러/매퍼에서 의도적으로 무시한다 (Pydantic `extra=ignore` 또는 매퍼에서 명시적 drop). 다음 agent_version 협의 때 제거 또는 활용 여부 결정.

| 필드 | 위치 | 무시 사유 |
|------|------|-----------|
| `mounts[].major/minor` | metrics | 시계열 테이블(`server_mount_usage`)에 컬럼 없음. inventory의 동일 필드만 활용 (mount↔disk 조인). 시계열에서도 활용 시 스키마 변경(`down -v`) 필요 |
| `disk_io[].major/minor` | metrics | 동일 — `server_disk_io` 시계열 테이블에 컬럼 없음. compute_disk_io의 분류는 device 이름 정규식 유지 |
| `mounts[].free_bytes/avail_bytes` | inventory | 인벤토리는 정적 정보(total_bytes)만 저장. 동적 사용량은 `server_mount_usage` 시계열로 분리 (`src/assessment_engine/consumer/mappers.py:to_inventory_create`) |
| `boot_time` | metrics·error | inventory 본문(공통 메타 격상 후) 경로만 사용 — `server_inventory.boot_time` 컬럼 + `server_inventory_history` 변경이력. metrics counter reset 정밀 식별(`metrics_calculator.py`에서 prev↔curr boot_time 비교) 미구현. error는 로깅 외 활용처 없음 |
| `agent_started_at` | metrics·error | 동일 — inventory 본문에서만 사용(`server_inventory.agent_started_at` + history). metrics에서 에이전트 재시작 sub-1h 감지 가능하지만 현재 미구현. error는 로깅 외 활용처 없음 |

### 활용 중인 필드 (이전엔 무시였음)

| 필드 | 위치 | 활용 방식 |
|------|------|----------|
| `disks[].major/minor` | inventory | `src/assessment_engine/web/services/device_filters.find_parent_disk()`에서 mount↔disk 조인 키 |
| `mounts[].major/minor` | inventory | `src/assessment_engine/web/services/mappers.to_storage_detail`에서 disks 리스트와 매칭 → `MountUsageItem.device_name` 채움 → storage.html "Device" 컬럼 |
| `boot_time` (inventory) | inventory | `server_inventory.boot_time` 컬럼 저장 + `server_inventory_history` append 시 비교 대상. detail.html / performance.html에 KST 표시 |
| `agent_started_at` (inventory) | inventory | `server_inventory.agent_started_at` 컬럼 저장 + history 변경 trigger. 에이전트 재시작 이벤트 감지의 1차 단서 |

---

## Listen 포트 수집

에이전트가 `/proc/net/tcp`, `/proc/net/udp` (IPv4/IPv6)를 파싱해 listening 상태 소켓만 수집한다. `ss -tlnp`와 동일한 커널 소켓 테이블을 읽는다.

### 필드 의미

| 필드 | 설명 |
|------|------|
| `proto` | tcp / udp |
| `addr` | 바인딩 주소 (`0.0.0.0` = 모든 인터페이스, `127.0.0.1` = 루프백) |
| `port` | 포트 번호 |
| `uid` | 소켓 소유 유저 ID (0 = root) |
| `pid` | 소켓을 열고 있는 프로세스 ID. 소켓 액티베이션 시 null |
| `comm` | 프로세스명 (`/proc/<pid>/comm`). pid가 null이면 null |

### 데몬 모델별 동작

fork 모델 (Apache prefork, sshd): 마스터 프로세스가 소켓을 열고 listen. `fork()`로 자식을 생성해 처리. 연결 수만큼 프로세스가 늘어난다.

이벤트/스레드 모델 (nginx, redis, Node.js): 단일 프로세스(또는 고정 worker)가 이벤트 루프로 다수 연결 처리. 연결이 와도 프로세스 수는 변하지 않는다.

소켓 액티베이션 (systemd socket activation): systemd가 소켓을 열어두고, 첫 연결 시점에만 데몬을 기동. 평소에는 프로세스가 없어 `pid` / `comm`이 null. 주로 가끔 호출되는 시스템 서비스(cups, avahi 등)에 쓰인다. postgresql, redis 같은 상시 고성능 서비스는 사용하지 않는다.

### UI 표현

- `port ≤ 1024`: well-known 포트 (root 전용 바인딩). 서버 상세 페이지에서 강조 표시.
- `pid` / `comm` null: 소켓 액티베이션 소켓. UI에서 "—" 표시.

---

## 물리 디스크·마운트 필터링

### 현황

에이전트가 `/sys/block/` 또는 `/proc/diskstats` 스캔 시 물리·가상 디바이스를 구분하지 않고 전부 전송한다. Ubuntu + snapd 환경에서는 snap 패키지 수만큼 `loop0`, `loop1`, ... 가 인벤토리에 포함된다.

포털 `device_filters.py`에서 allowlist regex(`sd*|vd*|nvme*n*|mmcblk*` 등)와 가상 fstype 블록리스트로 임시 필터링한다. 에이전트 업데이트 주기가 포털보다 길어 포털에서 즉시 노이즈를 제거하는 임시 방편이다.

### loop 디바이스 I/O 이중 집계

loop 디바이스 I/O는 sda에도 이미 반영된다 (`앱 read → loop0(squashfs) → sda`). `/proc/diskstats`에서 loop0과 sda 양쪽에 I/O가 집계되므로, loop를 포함하면 이중 집계된다. 현재 `device_filters.py`의 `is_physical_disk()`로 loop를 제외하고 있다.

### 에이전트 계약 변경 사항 (다음 agent_version 협의 필요)

`disks[]`:
- 물리 스토리지 디바이스만 포함
- 제외: `loop*`(snap/ISO), `ram*`(RAM disk), `zram*`(압축 RAM), `sr*`(광학 드라이브)
- 포함: `sd*`, `vd*`, `hd*`, `xvd*`, `nvme*n*`, `mmcblk*`
- 판별: `/sys/block/<dev>/device/` 디렉토리 존재 여부 또는 `/sys/block/<dev>/loop/` 심링크 부재

`mounts[]`:
- 사용자 공간 파일시스템만 포함. 커널·가상 마운트 제외
- 제외 fstype: `proc`, `sysfs`, `devtmpfs`, `devpts`, `squashfs`, `overlay`, `cgroup`, `cgroup2` 등
- `device` 필드 추가 필요: `/proc/mounts` 첫 번째 컬럼(마운트 소스). 포털에서 파일시스템 ↔ 물리 디스크 연결 표시 및 `device_filters.py` 의존도 감소 가능

개선 방향(P2): 에이전트 업데이트(`device` 필드 추가 + 가상 마운트 필터링) 후 `device_filters.py`의 `is_virtual_mount()`, `is_physical_disk()` 제거.

---

## 운영 / 디버깅

### VM 안 에이전트 상태 확인

```bash
# 단일 VM
vagrant ssh cache-server-01 -c "sudo systemctl status assessment-agent --no-pager"
vagrant ssh cache-server-01 -c "sudo journalctl -u assessment-agent --no-pager -n 50"

# 3대 일괄
for vm in cache-server-01 app-server-01 web-server-01; do
  echo "=== $vm ==="
  vagrant ssh $vm -c "sudo systemctl is-active assessment-agent"
done
```

기대 정상 로그:
```
[agent] cmd lsblk         available
[agent] cmd curl          available
[agent] cmd dbus-uuidgen  available
[agent] machine_id=f1e90cdc43d54cc88d0a42e3de1d409b
[agent] published inventory
[agent] loop mode: interval=60s (Ctrl+C to exit)
```

이후 매 60초마다 publish 로그가 추가되어야 정상.

### 메시지 발행 직접 확인

```bash
# RabbitMQ 관리 UI: http://localhost:15672
# Queues 탭 → server.metrics → Get messages 로 raw 메시지 확인

# 또는 CLI peek (메시지 1건 ack-and-requeue)
docker compose exec rabbitmq rabbitmqadmin -u assessment -p assessment \
  get queue=server.metrics count=1 ackmode=ack_requeue_true
```

`payload`에 JSON 메시지가 들어 있어야 함. routing key별로 inventory/metrics/error 구분.

### 에이전트 재시작 시점

| 상황 | 명령 |
|------|------|
| broker 재기동 후 silent retry | `sudo systemctl restart assessment-agent` (CRITICAL — `docs/operations/vagrant.md` 운영 노트) |
| 에이전트 소스 변경 | `vagrant rsync && vagrant ssh <vm>` → `cd /home/vagrant/assessment-agent && make && sudo cp assessment-agent /usr/local/bin/ && sudo systemctl restart assessment-agent` |
| 환경변수 (`/etc/assessment-agent.env`) 변경 | `vagrant provision` 또는 직접 수정 + `sudo systemctl restart assessment-agent` |
| `AGENT_INTERVAL_SEC` 변경 | 위와 동일 |

### 발행 메시지 추적 (end-to-end)

```bash
# 1. 에이전트가 발행했는지
vagrant ssh cache-server-01 -c "sudo journalctl -u assessment-agent --since '5 min ago' | grep -E 'published|publish'"

# 2. broker에 도달했는지 (큐 적재량 또는 처리량)
docker compose exec rabbitmq rabbitmqctl list_queues name messages_ready message_stats.publish_details.rate

# 3. consumer가 수신·처리했는지
docker compose logs consumer --since=5m | grep -E "stored|dropped"

# 4. DB에 들어갔는지
docker compose exec postgres psql -U assessment -d assessment -c "SELECT machine_id, last_seen_at FROM server_inventory;"
docker compose exec postgres psql -U assessment -d assessment -c "SELECT count(*) FROM server_metrics;"

# 5. web에 보이는지
curl -s "http://localhost:8000/servers/" | grep -E "cache-server|app-server|web-server"
```

각 단계에서 끊긴 곳이 원인.