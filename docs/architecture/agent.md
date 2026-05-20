# 메시지 데이터 형식

정책: CLAUDE.md #B. 본 문서는 엔진이 송수신하는 메시지의 데이터 형식 단일 진실. 외부 호스트 발행 / 엔진 수신 두 방향 모두.

---

## 공통 메타데이터 (`MessageBase`)

모든 메시지에 공통 포함.

| 필드 | 타입 | 설명 |
|------|------|------|
| `message_type` | string | 본 문서 메시지 타입별 Literal |
| `machine_id` | string max=64 | 호스트 식별자 (`/etc/machine-id` 기준 32 hex 또는 UUID 36자) |
| `agent_version` | string max=32 | 발행 측 빌드 버전 |
| `collected_at` | datetime (ISO 8601 UTC) | 수집 시각 |
| `hostname` | string max=255 | 보조 식별자 (가변) |
| `message_id` | UUID v4 | 멱등성 키 |
| `boot_time` | datetime UTC | 시스템 부팅 시각. `task.result` 한정 `null` 허용 (수집 컨텍스트 분리) |
| `agent_started_at` | datetime UTC | 발행 프로세스 기동 시각. `task.result` 한정 `null` 허용 |

엔진 처리는 `consumer/handler.py`의 `_check_idempotent`(Redis SET NX 24h)로 message_id 중복 차단 후 본문 처리. fail-open 보장은 시계열 4테이블 자연키 UNIQUE(#C1)가 흡수 (#D2).

---

## server.inventory (호스트 -> 엔진, 정적 인프라 정보)

routing key `server.inventory`. 기동 시 1회 + 주기 재발행.

| 필드 | 타입 | 설명 |
|------|------|------|
| OS / kernel | string\|null | `os_id` / `os_version` / `os_codename` / `kernel_version` |
| CPU | int\|null / string\|null | `cpu_cores` / `cpu_model` |
| 메모리 / 스왑 | int\|null (KB) | `mem_total_kb` / `swap_total_kb` |
| `disks[]` | object | `{name, size_bytes, type, major, minor}` |
| `mounts[]` | object | `{mount, fstype, total_bytes, free_bytes, avail_bytes, major, minor}` |
| `ip_internal[]` / `ip_external[]` | list[string]\|null | IP 주소 목록 |
| `services[]` | object\|null | `{unit, sub}` — systemd 미사용 호스트는 `null` |
| `listen_ports[]` | object | `{proto, addr, port, uid, pid, comm}` — 수집 실패 시 빈 배열 |

---

## server.metrics (호스트 -> 엔진, 1분 주기)

routing key `server.metrics`. 모두 raw 누적값. 엔진이 연속 2회 readings 의 차(delta) 로 CPU% / IOPS / kBps 계산.

| 필드 | 타입 | 설명 |
|------|------|------|
| `cpu_stat` | object | `user/nice/system/idle/iowait/irq/softirq/steal` jiffies |
| 메모리·스왑 | int\|null (KB) | `mem_*_kb` / `swap_*_kb` |
| `load_1m` / `load_5m` / `load_15m` | float\|null | Load Average |
| `disk_io[]` | object | `{device, reads_completed, writes_completed, sectors_read, sectors_written, major, minor}` |
| `mounts[]` | object | `{mount, total_bytes, free_bytes, avail_bytes, major, minor}` |
| `net_io[]` | object | `{interface, rx_bytes, tx_bytes, rx_packets, tx_packets, rx_errors, tx_errors}` |

---

## server.error (호스트 -> 엔진)

routing key `server.error`. 호스트 측 수집·발행 실패 보고.

| 필드 | 타입 | 설명 |
|------|------|------|
| `error_code` | string max=64 | 분류 코드 (예: `COLLECT_MEMINFO_FAILED`, `PUBLISH_RETRY_EXHAUSTED`) |
| `error_message` | string min=1 | 사람이 읽을 수 있는 상세 |
| `failed_component` | `"collect"` \| `"publish"` | 실패 단계 |
| `retry_count` | int\|null | 재시도 요약 보고 시점에만 |
| `first_failed_at` | datetime\|null | 재시도 요약 보고 시점에만 |
| `recovered_at` | datetime\|null | 복구 보고 시점에만 |

엔진 처리: 파싱 + 멱등성 체크 후 로깅(`make_error_handler`). DB 저장 없음.

---

## task.install (엔진 -> 호스트, 작업 명령)

엔진이 운영자 요청을 받아 발행 (`web/services/task_service.py`).

라우팅:
- Exchange: `assessment.tasks` (direct, durable, collector exchange 와 분리)
- Routing key: `task.install.<machine_id>` — broker 가 해당 머신 전용 큐로만 배달
- 수신 큐: `agent.tasks.<machine_id>` (durable, `x-message-ttl=3600000`, `x-max-length=100`, `x-overflow=reject-publish`). 큐는 엔진이 task 발행 시점에 동적 declare — 수신 측은 declare 권한 없음.

본 메시지는 엔진 발행이라 `MessageBase` 공통 메타와 별개로 다음 필드만 사용:

| 필드 | 타입 | 설명 |
|------|------|------|
| `message_type` | `"task.install"` | Literal |
| `task_id` | string (UUID v4) | 작업 고유 ID. `task.result` 회신·중복 검출·로그 추적 키. 엔진의 `Task.public_id` 그대로 |
| `machine_id` | string | 타겟 호스트 |
| `issued_at` | datetime (ISO 8601 UTC) | 발행 시각 |
| `download.url` | string | HTTP/HTTPS URL. 운영자 입력 ZDM host + `ZDM_PACKAGE_PATH` env 로 엔진이 조립 (`http://{ZDM_IP}{ZDM_PACKAGE_PATH}`). agent 측 host whitelist (`WORKER_DOWNLOAD_ALLOWED_HOSTS`) 통과 필요 |
| `download.sha256` | string (hex 64) | 다운로드 파일 sha256. `ZDM_PACKAGE_SHA256` env 그대로. 미설정(빈 문자열) 이면 엔진 측이 publish 거부 (503) |
| `download.size_bytes` | int | 예상 크기 (byte). `ZDM_PACKAGE_SIZE_BYTES` env 그대로. 미설정(0) 이면 엔진 측이 publish 거부 (503) |
| `install.script` | string | tar 추출 후 work dir 기준 실행 스크립트 경로. `ZDM_PACKAGE_SCRIPT` env 그대로 (default `zconverter_install_source/install.sh` — ZDM 본체 패키지 layout) |
| `install.args` | list[string] | 스크립트 인자. 운영자 입력 ZDM 좌표를 `["-s", ZDM_IP, "-u", ZDM_USER]` 형태로 전달. install.sh 가 `-s` / `-u` 를 받아 ZDM 서버에서 실제 setup 패키지 fetch + 실행 |
| `install.timeout_sec` | int | wall-clock timeout. `WebSettings.install_timeout_sec` (dev default 600) |

### Download URL 조립 contract

- ZDM 본체 패키지가 `http://{ZDM_IP}{ZDM_PACKAGE_PATH}` path 에 호스트되어야 한다. ZDM 측 contract (engine repo 밖) — path·sha256·size 가 패키지 매니페스트와 일치해야 agent 가 받아들임.
- 운영자가 모달에 입력한 zdm_ip 는 raw IP/hostname 외에도 URL 전체(`http://...`) 형태 허용. 엔진이 scheme·path strip 해서 host 만 추출 (`task_service._extract_zdm_host`) → download.url 조립 시 host 만 사용.
- agent 측 download.c 가 host whitelist(`WORKER_DOWNLOAD_ALLOWED_HOSTS`) 강제. 운영자가 박은 ZDM host 가 등록되지 않았으면 `failure_reason="url_not_allowed"` reject. agent config 는 deploy 시점 고정 — 새 ZDM host 도입 시 agent 재배포 필요.
- ZDM 좌표 default 는 `ZDM_DEFAULT_IP` / `ZDM_DEFAULT_USER` env. 패키지 메타는 `ZDM_PACKAGE_*` env (`docs/operations/env.md`).

---

## task.result (호스트 -> 엔진, 작업 결과 보고)

원격 호스트가 install 완료 시점(성공·실패 무관) 에 발행. routing key `task.result` -> 엔진 큐 `worker.result` (TTL 24h, max-length 100,000).

본 메시지는 수집 컨텍스트와 분리된 worker 프로세스에서 발행되어 `boot_time` / `agent_started_at` 가 항상 `null` — 엔진은 `TaskResultInput` 에서 두 필드를 nullable 로 override.

| 필드 | 타입 | 설명 |
|------|------|------|
| `message_type` | `"task.result"` | Literal |
| `task_id` | UUID | `task.install` 의 동일 값 회신. 엔진 `Task.public_id` 매칭 키 |
| `status` | `"success"` \| `"failure"` | 실행 결과 |
| `failure_reason` | string\|null max=32 | 실패 분류. 알려진 값: `url_not_allowed` / `download_failed` / `sha256_mismatch` / `extract_failed` / `script_not_found` / `script_failed` / `script_timeout` / `insufficient_disk` / `internal_error` / `already_done`. 성공 시 null. 알려지지 않은 값은 silent pass (DB 저장은 그대로) |
| `exit_code` | int\|null | install.sh 종료 코드. 실행 전 실패 시 null |
| `duration_ms` | int (≥0) | 다운로드 + 추출 + install 합계 |
| `stdout_tail` | string max=8192 | install.sh stdout 끝부분. 미실행 시 `""` |
| `stderr_tail` | string max=8192 | install.sh stderr 끝부분. 미실행 시 `""` |
| `completed_at` | datetime (ISO 8601 UTC) | 처리 완료 시각. `Task.completed_at` 컬럼에 그대로 저장 |

엔진 처리: 멱등성 -> DB UPDATE (`status` / `completed_at` / `failure_reason` / `exit_code` / `duration_ms` / `stdout_tail` / `stderr_tail`). `task_id` 미존재 시 silent ack — 운영자가 task 삭제했을 가능성, DLQ 부적합.

`boot_time` / `agent_started_at` 가 null 이라 `_log_time_invariants` 검증은 본 메시지에서 호출 안 함.

운영자 가시성: list.html "최근 작업" column (행별 마지막 task badge + polling 갱신) / detail.html "최근 작업" 섹션 (timeline 최근 10건 + row 클릭 modal) / Web API `GET /api/v1/tasks/{task_id}` 단일 + `GET /api/v1/tasks?server_public_id=...` 서버별 cursor pagination. 단일 진실: `web/services/mappers.py::to_task_summary` / `to_task_detail` + base.html `.rec-success`/`.rec-failure`/`.rec-pending`/`.rec-unknown`. failure_reason 한글 라벨은 `mappers._FAILURE_REASON_LABEL` 카탈로그 (10 enum).

dev 환경 success 경로는 ZDM 측 contract (실제 패키지 + sha256 매니페스트) 가 갖춰져야 한다. agent download.c 는 http·https 둘 다 허용하지만 host whitelist 검증 통과 + sha256·size 일치가 필수. ZDM_PACKAGE_SHA256 / SIZE_BYTES 미설정 시 엔진이 publish 자체를 차단(503) 하므로 task 흐름은 시작도 안 됨.

---

## 규약

- 단위: 메모리 = `kb`, 디스크 / 네트워크 = `bytes` (`/proc` 출력 관례)
- 옵셔널 필드: 수집 실패 시 `null` 전송. 수집 실패와 데이터 없음 미구분
- counter reset: 재부팅 / 발행 프로세스 재시작 시 카운터 0 리셋. 엔진은 1순위로 두 시점 `boot_time` 비교 (`web/services/metrics_calculator._is_counter_reset`) -> 시스템 재부팅이면 delta 건너뛰기 (None). 옛 데이터 (`boot_time` NULL) 는 2순위로 `delta < 0` 휴리스틱 fallback (UI 에서 "—"). `agent_started_at` 만 다르면 발행 프로세스 재시작이고 /proc 카운터는 그대로라 정상 delta

---

## 엔진이 받지만 사용하지 않는 필드

다음 필드는 발행 측이 보내지만 엔진 핸들러 / 매퍼에서 의도적으로 무시 (Pydantic `extra=ignore` 또는 매퍼에서 명시적 drop). 활용 결정 시점에 mapper read + inbound DTO 필드 추가가 의무.

| 필드 | 메시지 | drop 사유 |
|------|--------|-----------|
| `mounts[].major/minor` | metrics | 시계열 테이블 (`server_mount_usage`) 에 컬럼 없음. inventory 동일 필드만 mount-disk 조인에 활용 |
| `disk_io[].major/minor` | metrics | 동일 — `server_disk_io` 시계열 테이블에 컬럼 없음. compute_disk_io 분류는 device 이름 정규식 |
| `mounts[].free_bytes/avail_bytes` | inventory | 인벤토리는 정적 정보 (`total_bytes`) 만 저장. 동적 사용량은 `server_mount_usage` 시계열로 분리 (`consumer/mappers.py:to_inventory_create`) |
| `boot_time` / `agent_started_at` | error | error 는 로깅 외 활용처 없음 — counter reset 식별과 무관 |

`task.result` 신규 6 필드 (`failure_reason` / `exit_code` / `duration_ms` / `stdout_tail` / `stderr_tail` / `completed_at`) 는 모두 `Task` 테이블 컬럼으로 저장. 현재 ViewModel·UI 표시는 미연결 — 운영자 요구 도달 시 ViewModel 매퍼 + 템플릿 추가 (#F9 영향도 체크리스트).

## 활용 중인 필드

| 필드 | 메시지 | 활용 방식 |
|------|--------|----------|
| `disks[].major/minor` | inventory | `web/services/device_filters.find_parent_disk()` 에서 mount-disk 조인 키 |
| `mounts[].major/minor` | inventory | `web/services/mappers.to_storage_detail` 에서 disks 와 매칭 -> `MountUsageItem.device_name` -> storage.html "Device" 컬럼 |
| `boot_time` (inventory) | inventory | `server_inventory.boot_time` 컬럼 저장 + `server_inventory_history` append 시 비교. detail.html / performance.html 에 KST 표시 |
| `agent_started_at` (inventory) | inventory | `server_inventory.agent_started_at` 컬럼 저장 + history 변경 trigger. 발행 프로세스 재시작 이벤트 감지의 1차 단서 |
| `boot_time` (metrics) | metrics | 시계열 4 테이블 모두 (`server_metrics`·`server_disk_io`·`server_net_io`·`server_mount_usage`) `boot_time` 컬럼 저장. metrics·disk_io·net_io 는 `metrics_calculator._is_counter_reset` 이 두 시점 비교 -> 시스템 재부팅 시 delta 건너뛰기. mount_usage 는 시점값이라 calculator 직접 활용 없으나 메타데이터 일관성 위해 보존 (CLAUDE.md C1 + B) |
| `agent_started_at` (metrics) | metrics | 동일 4 테이블 컬럼 저장. boot_time 동일·agent_started_at 만 다름 -> 발행 프로세스 재시작 (counter 는 /proc 기반 그대로 -> 정상 delta) |

---

## Listen 포트 수집

발행 측이 `/proc/net/tcp` / `/proc/net/udp` (IPv4/IPv6) 를 파싱해 listening 상태 소켓만 수집. `ss -tlnp` 와 동일한 커널 소켓 테이블.

### 필드 의미

| 필드 | 설명 |
|------|------|
| `proto` | tcp / tcp6 / udp / udp6 |
| `addr` | 바인딩 주소 (`0.0.0.0` = 모든 인터페이스, `127.0.0.1` = 루프백) |
| `port` | 포트 번호 |
| `uid` | 소켓 소유 유저 ID (0 = root) |
| `pid` | 소켓을 열고 있는 프로세스 ID. 소켓 액티베이션 시 null |
| `comm` | 프로세스명 (`/proc/<pid>/comm`). pid 가 null 이면 null |

### 데몬 모델별 동작

fork 모델 (Apache prefork, sshd): 마스터 프로세스가 소켓을 열고 listen. `fork()` 로 자식을 생성해 처리. 연결 수만큼 프로세스가 늘어난다.

이벤트 / 스레드 모델 (nginx, redis, Node.js): 단일 프로세스 (또는 고정 worker) 가 이벤트 루프로 다수 연결 처리. 연결이 와도 프로세스 수는 변하지 않는다.

소켓 액티베이션 (systemd socket activation): systemd 가 소켓을 열어두고, 첫 연결 시점에만 데몬을 기동. 평소에는 프로세스가 없어 `pid` / `comm` 이 null. 주로 가끔 호출되는 시스템 서비스 (cups, avahi 등) 에 쓰인다.

### UI 표현

- `port <= 1024`: well-known 포트 (root 전용 바인딩). 서버 상세 페이지에서 강조 표시.
- `pid` / `comm` null: 소켓 액티베이션 소켓. UI 에서 "—" 표시.

---

## 물리 디스크·마운트 필터링

### 현황

발행 측이 `/sys/block/` 또는 `/proc/diskstats` 스캔 시 물리·가상 디바이스를 구분하지 않고 전부 전송한다. Ubuntu + snapd 환경에서는 snap 패키지 수만큼 `loop0`, `loop1`, ... 가 인벤토리에 포함된다.

엔진 `device_filters.py` 에서 allowlist regex (`sd*|vd*|nvme*n*|mmcblk*` 등) 와 가상 fstype 블록리스트로 임시 필터링한다.

### loop 디바이스 I/O 이중 집계

loop 디바이스 I/O 는 sda 에도 이미 반영된다 (`앱 read -> loop0(squashfs) -> sda`). `/proc/diskstats` 에서 loop0 과 sda 양쪽에 I/O 가 집계되므로, loop 를 포함하면 이중 집계. 현재 `device_filters.py` 의 `is_physical_disk()` 로 loop 제외.

### 발행 측이 1차 필터 적용 시점

`disks[]`:
- 물리 스토리지 디바이스만 포함
- 제외: `loop*`(snap/ISO), `ram*`(RAM disk), `zram*`(압축 RAM), `sr*`(광학 드라이브)
- 포함: `sd*`, `vd*`, `hd*`, `xvd*`, `nvme*n*`, `mmcblk*`

`mounts[]`:
- 사용자 공간 파일시스템만 포함. 커널·가상 마운트 제외
- 제외 fstype: `proc`, `sysfs`, `devtmpfs`, `devpts`, `squashfs`, `overlay`, `cgroup`, `cgroup2` 등

엔진측 `device_filters.py` (`is_virtual_mount()`, `is_physical_disk()`) 는 유지 — defense in depth (옛 발행 버전 비대칭 배포 대응, 새 가상 fstype 등장 시 hot-fix, 발행 측 필터 버그 시 최종 방어선).

---

## 운영 / 디버깅

Lima VM 발행 측 상태:
```bash
limactl shell <vm> sudo systemctl status assessment-agent --no-pager
limactl shell <vm> sudo journalctl -u assessment-agent --no-pager -n 50
```

end-to-end 추적: (1) VM 발행 로그 -> (2) broker 큐 적재 (`rabbitmqctl list_queues`) -> (3) consumer 처리 로그 -> (4) DB 행 -> (5) web 표시. 끊긴 단계가 원인.

발행 측 재기동: 소스·env 변경 시 `./scripts/pipeline-up.sh` 재실행으로 자동. 단발 재기동은 `limactl shell <vm> sudo systemctl restart assessment-agent`.
