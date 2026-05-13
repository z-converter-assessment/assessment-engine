# 에이전트 스키마 계약

정책: CLAUDE.md #B. 본 문서는 엔진 핸들링 관점 메시지 스키마 단일 진실 — `agent_version` 필드가 계약 버전 역할.

정식 정의는 별도 레포 `assessment-agent/docs/payload-schema.md`. 엔진 호환 스키마: v3 — `services[]` / `listen_ports[]` / `error.{retry_count, first_failed_at, recovered_at}` 옵셔널 필드 포함.

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

### task.result (agent → engine, 작업 결과 보고)

원격 작업(현재 `zconverter_install`) 실행 후 결과를 engine에 보고. routing key `task.result`.

| 필드 | 타입 | 설명 |
|------|------|------|
| 공통 메타 | — | `MessageBase`와 동일 (machine_id, agent_version, collected_at, hostname, message_id, agent_started_at, boot_time) |
| `message_type` | `"task_result"` | Literal |
| `task_public_id` | UUID | engine이 RPC reply에 담아 보낸 값을 그대로 회신 (correlation 식별자) |
| `status` | `"success"` \| `"failed"` | 실행 결과 |
| `result_message` | str \| null (max 4000) | 실패 사유·로그 요약. success도 채울 수 있음 |

엔진 처리: 멱등성 → DB UPDATE (status·completed_at·result_message) → Redis `task:pending:{machine_id}` DEL.
public_id 미존재 시 silent ack — 운영자가 task 삭제했을 가능성, DLQ 부적합.

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
- counter reset: 재부팅·에이전트 재시작 시 카운터 0 리셋. 포털은 1순위로 두 시점 boot_time 비교(`web/services/metrics_calculator._is_counter_reset`) → 시스템 재부팅이면 delta 건너뛰기(None). 옛 데이터(boot_time NULL)는 2순위로 `delta < 0` 휴리스틱 fallback (UI에서 "—"). agent_started_at만 다르면 에이전트 재시작이고 /proc 카운터는 그대로라 정상 delta

### 엔진이 받지만 사용하지 않는 필드

다음 필드는 에이전트가 발행하지만 엔진 핸들러/매퍼에서 의도적으로 무시한다 (Pydantic `extra=ignore` 또는 매퍼에서 명시적 drop). 다음 agent_version 협의 때 제거 또는 활용 여부 결정.

| 필드 | 위치 | 무시 사유 |
|------|------|-----------|
| `mounts[].major/minor` | metrics | 시계열 테이블(`server_mount_usage`)에 컬럼 없음. inventory의 동일 필드만 활용 (mount-disk 조인). 시계열에서도 활용 시 스키마 변경(`down -v`) 필요 |
| `disk_io[].major/minor` | metrics | 동일 — `server_disk_io` 시계열 테이블에 컬럼 없음. compute_disk_io의 분류는 device 이름 정규식 유지 |
| `mounts[].free_bytes/avail_bytes` | inventory | 인벤토리는 정적 정보(total_bytes)만 저장. 동적 사용량은 `server_mount_usage` 시계열로 분리 (`src/assessment_engine/consumer/mappers.py:to_inventory_create`) |
| `boot_time` (error) | error | error 메시지는 로깅 외 활용처 없음 — counter reset 식별과 무관 |
| `agent_started_at` (error) | error | 동일 — error 메시지에서만 미사용 |

### 활용 중인 필드

| 필드 | 위치 | 활용 방식 |
|------|------|----------|
| `disks[].major/minor` | inventory | `src/assessment_engine/web/services/device_filters.find_parent_disk()`에서 mount-disk 조인 키 |
| `mounts[].major/minor` | inventory | `src/assessment_engine/web/services/mappers.to_storage_detail`에서 disks 리스트와 매칭 → `MountUsageItem.device_name` 채움 → storage.html "Device" 컬럼 |
| `boot_time` (inventory) | inventory | `server_inventory.boot_time` 컬럼 저장 + `server_inventory_history` append 시 비교 대상. detail.html / performance.html에 KST 표시 |
| `agent_started_at` (inventory) | inventory | `server_inventory.agent_started_at` 컬럼 저장 + history 변경 trigger. 에이전트 재시작 이벤트 감지의 1차 단서 |
| `boot_time` (metrics) | metrics | 시계열 4개 테이블 모두(`server_metrics`·`server_disk_io`·`server_net_io`·`server_mount_usage`) `boot_time` 컬럼 저장 — 메타데이터 일관성. metrics·disk_io·net_io는 `web/services/metrics_calculator._is_counter_reset`이 두 시점 비교 → 시스템 재부팅 시 delta 건너뛰기 (counter reset 정밀 식별의 1차 신호). mount_usage는 시점값이라 calculator 직접 활용 없으나 운영 디버깅·미래 활용 위해 보존. CLAUDE.md C1 + B1 |
| `agent_started_at` (metrics) | metrics | 동일 4개 테이블 컬럼 저장. boot_time과 함께 비교 — boot_time 동일·agent_started_at만 다름 → 에이전트 재시작 (counter는 /proc 기반이라 그대로 → 정상 delta) |

---

## Task RPC piggyback (engine → agent)

용도: engine이 등록한 작업 명령을 agent에게 전달. 별도 polling endpoint·task queue 불필요 — 기존 `server.metrics` 발행 응답 채널에 piggyback.

### 흐름

```
agent: metrics publish
  props.reply_to       = "amq.rabbitmq.reply-to"   ← RabbitMQ 빌트인 pseudo-queue
  props.correlation_id = <UUID>                    ← agent 생성, reply에서 그대로 회신됨

engine consumer: metrics 처리 후
  - Redis EXISTS task:pending:{machine_id}
  - 있으면 GET → reply publish to props.reply_to (correlation_id 동일)
  - 없으면 reply 생략 — agent는 timeout 두지 않음 (다음 metrics 주기 자연 폴)
```

### Reply 메시지 스키마

```json
{
  "task_public_id": "<UUID>",
  "task_type": "zconverter_install",
  "params": { "source_url": "http://host.lima.internal:8000/zconverter.tar.gz" }
}
```

agent는 `task_type`으로 미리 컴파일된 핸들러 dispatch. 핸들러가 `params` 사용해 실행.

### task_type enum (engine·agent 합의 필수)

| task_type | params 스키마 | agent 동작 |
|-----------|--------------|-----------|
| `zconverter_install` | `{"source_url": str}` | `curl {source_url}` → `tar -xzf` → `bash install.sh`. source_url은 scheme(http/https)·host·port·path 자유 |

새 task_type 도입 시 양쪽 동시 갱신 + agent_version bump. 미지원 task_type 수신 시 agent는 `task.result` `failed` 보고 (`result_message="unknown task_type"`).

### Install bundle endpoint (`GET /zconverter.tar.gz`)

본 엔진은 self-host install 번들 endpoint를 `/zconverter.tar.gz` path로 제공 (`web/routers/payloads.py`). 운영 흐름:

- 운영자가 web UI에서 서버 체크 + `source_url` 입력 (전체 URL). 본 엔진 self-host 시 `http://<engine-fqdn>:8000/zconverter.tar.gz` 입력 — 엔진 endpoint로 들어옴.
- 외부 mirror 호스팅 시 외부 URL(`https://mirror.example.com/dist/zconverter-v1.tar.gz`) 입력 가능 — scheme·port·path 자유. agent는 URL 변환 없이 그대로 curl.
- 엔진 endpoint는 in-memory에서 tar.gz 생성. 안의 `install.sh`는 `mode=0o755`로 메타 박혀서 agent `tar -xzf` 시 실행 권한 그대로 복원.
- `install.sh` 내용은 코드 안 상수(`_INSTALL_SCRIPT`) — 수정 후 web 컨테이너 재기동(또는 uvicorn auto-reload) 시 즉시 반영. mtime=epoch 고정이라 같은 코드면 같은 bytes.
- F13 예외 — 본 endpoint path는 self-host default일 뿐. agent는 URL을 hardcode 안 하고 source_url 그대로 사용.

신규 task_type이 다른 bundle endpoint 사용 시 본 절에 URL 예시 추가.

### Latency·동작 가정

- agent가 reply_to를 명시한 경우만 piggyback 작동. 옛 agent(미구현)는 reply_to 없이 발행 → engine consumer가 reply 생략 (no-op).
- latency = metrics 발행 주기. 즉시성 필요하면 별도 push queue 도입 검토 (현재 미사용).
- engine consumer는 Redis 1 GET만 — 99% no-op. DB 직접 조회 안 함 (hot path 보호).

### 구현 메모

- Reply 채널은 `amq.rabbitmq.reply-to` (RabbitMQ 빌트인 pseudo-queue) 권장. 큐 선언·정리 불필요. `basic.consume`으로 같은 channel 위에서 reply 수신.
- correlation_id 매칭 책임은 agent. engine은 받은 값 그대로 회신만.
- reply 메시지는 `delivery_mode=NOT_PERSISTENT` (transient) — 빠른 처리·broker 디스크 부담 0. agent가 못 받아도 다음 주기에 다시 piggyback.

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

- `port <= 1024`: well-known 포트 (root 전용 바인딩). 서버 상세 페이지에서 강조 표시.
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
- mount-disk 연결은 이미 발행 중인 `major`/`minor` 키로 처리(본 문서 "활용 중인 필드" 절, `find_parent_disk`). `/proc/mounts` 첫 컬럼을 별도 `device` 필드로 발행할 필요 없음 — redundant.

개선 방향(P2): 에이전트가 1차 노이즈 차단(`disks[]`·`mounts[]` 필터링)을 수행하더라도 서버측 `device_filters.py`(`is_virtual_mount()`, `is_physical_disk()`)는 유지. defense in depth — (a) 옛 버전 에이전트 비대칭 배포(#B) 대응 (b) 새 가상 fstype·디바이스 패턴 등장 시 서버 단독 hot-fix 가능 (c) 에이전트 필터 버그 시 서버측이 최종 방어선.

---

## 운영 / 디버깅

VM 에이전트 상태:
```bash
limactl shell <vm> sudo systemctl status assessment-agent --no-pager
limactl shell <vm> sudo journalctl -u assessment-agent --no-pager -n 50
```

end-to-end 추적: ① VM 발행 로그 → ② broker 큐 적재(`rabbitmqctl list_queues`) → ③ consumer 처리 로그 → ④ DB 행 → ⑤ web 표시. 끊긴 단계가 원인.

에이전트 재기동: 소스·env 변경 시 `./dev-up.sh` 재실행으로 자동. 단발 재기동은 `limactl shell <vm> sudo systemctl restart assessment-agent`.