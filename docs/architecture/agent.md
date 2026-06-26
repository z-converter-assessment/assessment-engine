# Agent (메시지 데이터 계약)

정책: CLAUDE.md #B. 본 문서는 엔진이 송수신하는 메시지의 데이터 형식 단일 진실. 외부 호스트 발행 / 엔진 수신 두 방향 모두.

외부 SoT 동기화 상태: agent repo (`../assessment-agent/`) 의 `docs/payload-schema.md` 는 payload v3.4 (`ip_internal` CIDR 전환 + `boot_time` 소스 `/proc/stat btime` 전환 포함) 까지 반영. 단 v4 (composite_id 라우팅 전환) 는 여전히 미반영 — `task.install` 본문에 `machine_id` 필드가 남아 있고 라우팅 키가 `task.install.<machine_id>` 로 서술됨. 실제 양 OS agent C 코드 (Linux `src/main.c` + Windows `windows-agent/src/main.c`) 와 본 문서는 `composite_id` 기반으로 정렬됨. agent repo docs 갱신은 agent repo 측 책임 — 본 문서가 엔진 입장의 단일 진실이고, 발행 측 실제 코드가 본 문서와 일치해야 함 (코드 = 1순위, agent docs = 2순위).

---

## 공통 메타데이터 (`MessageBase`)

모든 메시지에 공통 포함.

| 필드 | 타입 | 설명 |
|------|------|------|
| `message_type` | string | 본 문서 메시지 타입별 Literal |
| `composite_id` | string max=64 | 호스트 식별 단일 키 — SHA-256 composite hash (`machine_id` + 정렬·dedup MAC 들, agent v4). DB UNIQUE·URL public_id 매핑·task 라우팅 모두 본 값. `task.result` 한정 `null` 허용 (worker 컨텍스트 — `task_id` 로 매칭) |
| `machine_id` | string\|null max=64 | raw machine-id (Linux `/etc/machine-id` 32 hex, Windows `MachineGuid`). 표시 전용 — 식별·라우팅 미사용. 옛 agent 미발행 호환 nullable |
| `agent_version` | string max=32 | 발행 측 빌드 버전 |
| `collected_at` | datetime (ISO 8601 UTC) | 수집 시각 |
| `hostname` | string max=255 | 보조 식별자 (가변) |
| `message_id` | UUID v4 | 멱등성 키 |
| `boot_time` | datetime UTC | 시스템 부팅 시각. Linux agent 소스는 `/proc/stat` 의 `btime` 라인 (agent payload v3.4+, 이전 `/proc/uptime` + `CLOCK_REALTIME` 산출). wire 형식·의미 동일 (ISO 8601 UTC) — 엔진 무영향. 프로세스 시작 시 1회 read 후 캐시 (NTP 보정 흔들림 제거). `task.result` 한정 `null` 허용 (수집 컨텍스트 분리). Windows agent 는 캐시 산출 실패 시 빈 문자열 발행 가능 -> engine inventory/metrics/error required datetime reject -> DLQ |
| `agent_started_at` | datetime UTC | 발행 프로세스 기동 시각. `task.result` 한정 `null` 허용. Windows agent 의 `cache_process_times` 가 `time()` 실패 시 빈 문자열 발행 가능 — 동일 reject 경로 |
| `os_family` | `"linux"` \| `"windows"` \| null | OS family — 모든 메시지 진입 시점 OS 분기 단일 진실 (`MessageBase` 정식 등록). 양 OS agent 가 발행. nullable 사유: (a) 옛 agent minor bump 호환 (b) task.result Linux worker 미발행 비대칭 (Windows worker 는 `"windows"` 발행 / Linux worker 미발행) 흡수. inventory 한정 추가 활용: `server_inventory.os_family` 저장 후 `task.install` dispatch 단일 진실 (ADR 0020). metrics/error/task.result 는 현재 시계열 테이블 컬럼 미추가 — 시점별 활용처가 명확해질 때 별도 결정 |

엔진 처리는 `consumer/handlers/`의 `_check_idempotent`(Redis SET NX 24h)로 message_id 중복 차단 후 본문 처리. fail-open 보장은 시계열 4테이블 자연키 UNIQUE(#C1)가 흡수 (#D2).

---

## server.inventory (호스트 -> 엔진, 정적 인프라 정보)

routing key `server.inventory`. 기동 시 1회 + 주기 재발행.

| 필드 | 타입 | 설명 |
|------|------|------|
| `os_family` | `"linux"` \| `"windows"` \| null | OS family — task.install dispatch 단일 진실 (ADR 0020). nullable (Linux agent minor bump 호환 단계). 미수신 시 engine 측 fallback `"linux"` |
| OS / kernel | string\|null | `os_id` / `os_version` / `os_codename` / `kernel_version` |
| CPU | int\|null / string\|null | `cpu_cores` / `cpu_model` |
| 메모리 / 스왑 | int\|null (KB) | `mem_total_kb` / `swap_total_kb` |
| `disks[]` | object | `{name, size_bytes, type, major, minor}`. Windows: `name="PhysicalDriveN"`, `type="disk"` 고정, `major=0` 평탄 (POSIX major 부재 placeholder), `minor=`drive index (`enumerate_physical_disks` 0..31 시도 순서). mount-disk 조인은 Windows 한정 major 무의미 -> minor 단독 매칭 (disks[].minor 와 mounts[].minor 가 `STORAGE_GET_DEVICE_NUMBER.DeviceNumber` 동일 키로 정렬) |
| `mounts[]` | object | `{mount, fstype, total_bytes, free_bytes, avail_bytes, major, minor}`. Windows: `mount=`drive letter (`"C:\\"`, `"D:\\"` 등), `fstype` lowercase (`GetVolumeInformationW` 실패 시 null), `major=0`, `minor=DeviceNumber` |
| `ip_internal[]` | list[string]\|null | 내부 IP 를 CIDR 표기 문자열로 발행 (agent payload v3.4+, 이전 bare IP). 예: `"10.0.1.15/24"`. Linux 는 `ifa_netmask` 에서 prefix 환산(IPv4 only), Windows 는 `OnLinkPrefixLength` 직사용(IPv4+IPv6). netmask 부재(point-to-point 등)·non-contiguous mask 시 prefix `0` 가능. loopback 제외. 인바운드는 `consumer/schemas.py::validate_ip_list` 가 `ip_interface` 로 형식만 검증(bare·CIDR 모두 허용 — 옛 agent 하위호환) 후 `server_inventory.ip_internal` 에 raw 문자열 그대로 저장. 소비: detail 표시(`mappers/server._to_ip_addrs` 도 `ip_interface` 파싱) + 대시보드 네트워크 토폴로지(`mappers/topology.build_network_topology` — subnet 공동소속 그래프) |
| `ip_external[]` | list[string]\|null | 외부 IP 목록 (CIDR 아님, bare IP). 클라우드 메타데이터 미접근 시 `null` |
| `mac_addresses[]` | list[string] | NIC MAC 목록 (lowercase·정렬·dedup, 빈 배열 가능, agent payload v3.3+). 받아 `server_inventory.mac_addresses` 저장 — 다중 NIC 라 식별 미사용(composite_id 가 sha256 으로 흡수). clone collision 감지는 미구현 (raw 보존만) |
| `services[]` | object\|null | `{unit, sub}`. `null` = Linux non-systemd 또는 Windows SCM 접근 실패 / 빈 배열 = 서비스 0개 (Windows SCM 성공). `sub` 의 OS별 의미 차이: Linux = systemd sub 컬럼 가변값 (`running`/`exited`/`failed`/`dead` 등 — 상태 분기 의미), Windows = SCM RUNNING 항목만 발행하므로 항상 `"running"` 고정 (상태 분기 의미 부재 — UI badge 가 sub 값 분기 시 Windows 호스트는 항상 동일 표시) |
| `listen_ports[]` | object | `{proto, addr, port, uid, pid, comm}` — 수집 실패 시 빈 배열 |

---

## server.metrics (호스트 -> 엔진, 1분 주기)

routing key `server.metrics`. 모두 raw 누적값. 엔진이 연속 2회 readings 의 차(delta) 로 CPU% / IOPS / kBps 계산.

| 필드 | 타입 | 설명 |
|------|------|------|
| `cpu_stat` | object | `user/nice/system/idle/iowait/irq/softirq/steal` jiffies. Windows: user/system/idle 만 측정 (nice/iowait/irq/softirq/steal = `0` 발행) |
| 메모리·스왑 | int\|null (KB) | `mem_*_kb` / `swap_*_kb` |
| `load_1m` / `load_5m` / `load_15m` | float\|null | Load Average. Windows 항상 `null` (OS 부재) |
| `disk_io[]` | object | `{device, reads_completed, writes_completed, sectors_read, sectors_written, major, minor}`. Windows: `device="PhysicalDriveN"`, `major=0`, `minor=`drive index. engine `device_filters._PHYS_DISK_RE` 가 `PhysicalDrive\d+` 포함 -> Windows 통과 허용 |
| `mounts[]` | object | `{mount, total_bytes, free_bytes, avail_bytes, major, minor}`. Windows: `mount=`drive letter |
| `net_io[]` | object | `{interface, rx_bytes, tx_bytes, rx_packets, tx_packets, rx_errors, tx_errors}`. Windows: `interface=Win32 friendly name` (Alias, 한글 가능 — `"이더넷"` 등 UTF-8 그대로) |

---

## server.error (호스트 -> 엔진)

routing key `server.error`. 호스트 측 수집·발행 실패 보고.

| 필드 | 타입 | 설명 |
|------|------|------|
| `error_code` | string max=64 | 분류 코드 (예: `COLLECT_MEMINFO_FAILED`, `PUBLISH_RETRY_EXHAUSTED`) |
| `error_message` | string | 사람이 읽을 수 있는 상세. 양 OS agent 모두 NULL 인자 시 `""` 발행 가능 (Linux `src/collect.c:1803`, Windows `windows-agent/src/collect.c:1022`). engine 측 `min_length` 미적용 — 빈 문자열 흡수 후 로깅 시 `"(empty)"` fallback 표기 (`consumer/handlers/error.py`). 발행 측이 의미 있는 메시지 박는 것은 운영 권장이지 contract 강제 아님 |
| `failed_component` | `"collect"` \| `"publish"` | 실패 단계. Linux NULL 인자 default `"collect"` (engine enum 통과). Windows NULL 인자 default `"agent"` (`windows-agent/src/collect.c:1023`) — engine `Literal` reject 후 DLQ. 호출자가 항상 두 값 중 하나 박을 의무 (현재 코드 경로상 호출자가 명시 박는 자리만 사용되어 fallback 미트리거, fallback 자체가 잠재 위험) |
| `retry_count` | int\|null | 재시도 요약 보고 시점에만 |
| `first_failed_at` | datetime\|null | 재시도 요약 보고 시점에만 |
| `recovered_at` | datetime\|null | 복구 보고 시점에만 |

엔진 처리: 파싱 + 멱등성 체크 후 로깅(`make_error_handler`). DB 저장 없음.

`collected_at` 정밀도 비대칭: Linux agent 만 본 메시지 한정 millisecond 정밀도 ISO 8601 UTC (`iso8601_utc_ms` `src/collect.c:1798-1800`). Windows agent 는 본 메시지도 다른 메시지와 동일 second 정밀도 (override 없음). 다른 모든 메시지 (`server.inventory` / `server.metrics` / `task.result`) 는 양 OS 모두 second 정밀도. engine datetime parse 는 두 정밀도 모두 흡수.

---

## task.install (엔진 -> 호스트, 작업 명령)

엔진이 운영자 요청을 받아 발행 (`web/services/task_service.py`).

라우팅:
- Exchange: `assessment.tasks` (direct, durable, collector exchange 와 분리)
- Routing key: `task.install.<composite_id>` — broker 가 해당 머신 전용 큐로만 배달
- 수신 큐: `agent.tasks.<composite_id>` (durable, `x-message-ttl=3600000`, `x-max-length=100`, `x-overflow=reject-publish`). 큐는 엔진이 task 발행 시점에 동적 declare — 수신 측은 declare 권한 없음. agent 도 v4(#11)에서 큐 이름을 composite_id 기반으로 전환 — 라우팅 키와 정확히 일치.

본 메시지는 엔진 발행이라 `MessageBase` 공통 메타와 별개로 다음 필드만 사용:

| 필드 | 타입 | 설명 |
|------|------|------|
| `message_type` | `"task.install"` | Literal |
| `task_id` | string (UUID v4) | 작업 고유 ID. `task.result` 회신·중복 검출·로그 추적 키. 엔진의 `Task.public_id` 그대로 |
| `composite_id` | string | 타겟 호스트 식별 (SHA-256 composite hash). 수신 큐 라우팅과 동일 값 |
| `issued_at` | datetime (ISO 8601 UTC) | 발행 시각 |
| `download.url` | string | HTTP/HTTPS URL. 운영자 입력 ZDM host + `ZDM_PACKAGE_PATH` env 로 엔진이 조립 (`http://{ZDM_IP}{ZDM_PACKAGE_PATH}`). agent 측 host whitelist (`WORKER_DOWNLOAD_ALLOWED_HOSTS`) 통과 필요 |
| `download.sha256` | string (hex 64) | 다운로드 파일 sha256. 엔진이 publish 직전 ZDM 에서 HEAD + (cache miss 시) GET full 로 동적 산출. ETag 기반 Redis cache (`HttpZdmPackageResolver`). ZDM 측 패키지 갱신 시 ETag 자동 변경 → cache miss → 자동 재계산 |
| `download.size_bytes` | int | 예상 크기 (byte). 엔진이 HEAD Content-Length 로 산출 + GET 실측과 일치 검증 |
| `install.type` | `"shell"` \| `"direct_exec"` \| `"msi"` | 처리 방식 enum. `shell` = archive extract 후 script 실행 (Linux .tar.gz), `direct_exec` = extract 없음, 다운로드 파일 직접 실행 (Windows .exe), `msi` = extract 없음, `msiexec /i {path} /quiet` (Windows .msi). agent 가 자기 OS 아닌 type 수신 시 `failure_reason="unsupported_install_type"` reject |
| `install.script` | string \| null | `type=shell` 일 때만 의미 — tar 추출 후 work dir 기준 실행 스크립트 경로. `ZDM_PACKAGE_SCRIPT` env 그대로 (default `zconverter_install_source/install.sh` — ZDM 본체 패키지 layout). `direct_exec` / `msi` 일 때 null |
| `install.args` | list[string] | 스크립트 / 실행 파일 인자. 운영자 입력 ZDM 좌표를 `["-s", ZDM_IP, "-u", ZDM_USER]` 형태로 전달. OS 무관 동일 convention (Linux install.sh / Windows .exe 양쪽 `-s` `-u` 인자 받음) |
| `install.timeout_sec` | int | wall-clock timeout. `WebSettings.install_timeout_sec` (dev default 600) |

### Download URL 조립 contract

- ZDM 본체 패키지가 `http://{ZDM_IP}{ZDM_PACKAGE_PATH}` path 에 호스트되어야 한다. ZDM 측 contract (engine repo 밖) — path 가 안정해야 하고, sha256·size 는 엔진이 자체 산출하므로 ZDM 측 매니페스트 endpoint 불필요.
- 운영자가 모달에 입력한 zdm_ip 허용 형식: IPv4 / IPv4:port / hostname / FQDN / hostname:port / http(s) URL. 엔진이 scheme·path strip 해서 host[:port] 만 추출 (`task_service._extract_zdm_host`) → download.url 조립 시 host[:port] 사용. agent `download_url_extract_host` 가 `':'` 도 host 종료 문자로 처리해 host-only 매칭. validator 매트릭스 단일 진실: `web/routers/tasks.py::_validate_zdm_ip` + `_is_valid_host_or_host_port`. IPv6 (raw / bracket) 는 agent 측 한계로 미지원.
- agent 측 download.c 가 host whitelist(`WORKER_DOWNLOAD_ALLOWED_HOSTS`) 강제. 운영자가 박은 ZDM host 가 등록되지 않았으면 `failure_reason="url_not_allowed"` reject. agent config 는 deploy 시점 고정 — 새 ZDM host 도입 시 agent 재배포 필요.
- ZDM 좌표 default 는 `ZDM_DEFAULT_IP` / `ZDM_DEFAULT_USER` env. 패키지 메타는 `ZDM_PACKAGE_PATH` / `ZDM_PACKAGE_SCRIPT` env (`docs/operations/env.md`). 엔진은 실 ZDM 호스트에서 직접 메타를 fetch 한다.

### sha256·size 동적 산출 (HttpZdmPackageResolver)

`src/assessment_engine/web/services/task_service.py` 단일 진실. 흐름:

1. HEAD `http://{ZDM_IP}{ZDM_PACKAGE_PATH}` — `ETag`(또는 fallback `Last-Modified`) + `Content-Length` 추출.
2. Redis cache 키 `cache:zdm_package:sha256:{host}:{etag}` 조회.
   - hit: cached sha256 + HEAD Content-Length 반환 (수십 ms, GET 안 함).
   - miss: GET full 다운로드 + streaming sha256 계산 + cache set + 반환.
3. HEAD Content-Length 와 GET 실측 byte count 일치 검증 — 다르면 `ZdmPackageMetaError` (ZDM 측 정합성 보장).
4. 메타 fetch 실패 (HEAD 404·connect timeout·size mismatch) 시 publish 차단 → 503 (`TaskNotConfigured`).

cache 동작:
- ZDM 패키지 갱신 → Apache 가 inode-size-mtime 기반 ETag 자동 변경 → cache miss → 자동 재계산. 운영자 개입 0.
- TTL 6h default — ETag 자체가 invalidation 이라 길어도 안전. ETag/Last-Modified 둘 다 없는 비표준 응답이면 cache skip + 매 publish 마다 GET full.

---

## task.result (호스트 -> 엔진, 작업 결과 보고)

원격 호스트가 install 완료 시점(성공·실패 무관) 에 발행. routing key `task.result` -> 엔진 큐 `worker.result` (TTL 24h, max-length 100,000).

본 메시지는 수집 컨텍스트와 분리된 worker 프로세스에서 발행되어 `composite_id` / `boot_time` / `agent_started_at` 가 `null` 가능 (worker 는 composite hash 미산출) — 엔진은 `TaskResultInput` 에서 세 필드를 nullable 로 override. 결과 매칭은 `task_id` 로 하므로 composite_id 불필요. (agent worker 는 현재 `machine_id` 키로 발행하나 엔진 `extra=ignore` 로 무시. `os_family` 도 동일 케이스 — Linux worker 미발행 / Windows worker `"windows"` 발행 비대칭, 양쪽 다 engine `extra=ignore` 흡수.)

| 필드 | 타입 | 설명 |
|------|------|------|
| `message_type` | `"task.result"` | Literal |
| `task_id` | UUID | `task.install` 의 동일 값 회신. 엔진 `Task.public_id` 매칭 키 |
| `status` | `"success"` \| `"failure"` | 실행 결과 |
| `failure_reason` | string\|null max=32 | 실패 분류. 알려진 값: `url_not_allowed` / `download_failed` / `sha256_mismatch` / `extract_failed` / `script_not_found` / `script_failed` / `script_timeout` / `insufficient_disk` / `internal_error` / `already_done` / `unsupported_install_type`. 성공 시 null. 알려지지 않은 값은 silent pass (DB 저장은 그대로) |
| `exit_code` | int\|null | install.sh 종료 코드. 실행 전 실패 시 null |
| `os_version` | string\|null max=64 | OS 버전 식별자. Windows worker = `CurrentBuildNumber` (예 `"20348"` = Server 2022). Linux worker 미발행 → null (`task.result` 한정 비대칭, `extra=ignore` 흡수). 엔진 success exit code 보정 정책(`task_policy`)의 매칭 키 |
| `duration_ms` | int (>=0) | 다운로드 + 추출 + install 합계 |
| `stdout_tail` | string max=4096 | install.sh stdout 끝부분 4 KB. agent `exec.c` 의 `out_storage[4096]` circular tail buffer 단일 진실. 미실행 시 `""` |
| `stderr_tail` | string max=4096 | install.sh stderr 끝부분 4 KB. agent `exec.c` 의 `err_storage[4096]` 단일 진실. 미실행 시 `""` |

엔진 Inbound DTO (`consumer/schemas.py` `TaskResultInput`) 의 `max_length=8192` 는 over-provision — agent minor bump 로 tail cap 이 늘어도 (#B "minor bump silent 호환") 엔진 무수정 흡수. 현재 wire 상한은 agent 측 4 KB.
| `completed_at` | datetime (ISO 8601 UTC) | 처리 완료 시각. `Task.completed_at` 컬럼에 그대로 저장 |

엔진 처리: 멱등성 -> 성공 보정 정책(`task_policy.effective_task_result`) -> DB UPDATE (`status` / `completed_at` / `failure_reason` / `exit_code` / `duration_ms` / `stdout_tail` / `stderr_tail`). `task_id` 미존재 시 silent ack — 운영자가 task 삭제했을 가능성, DLQ 부적합.

성공 보정 정책: 일부 Windows 버전에서 ZConverter installer 가 설치 성공임에도 exit code 2 로 종료해 worker 가 `status=failure`(`script_failed`) 로 보고하는 케이스 대응. `status=failure` & `failure_reason=script_failed` & `os_family=windows` & `os_version` 이 allowlist 키와 일치 & `exit_code` 가 허용 목록에 포함일 때만 effective `status` 를 `success` 로 전환(`failure_reason`=null). `exit_code` 는 raw 보존(감사용), `status`/`failure_reason` 만 보정 저장. allowlist = `ConsumerSettings.task_install_success_exit_codes` (기본 `{"20348": [2]}` = Server 2022 / exit 2, env `TASK_INSTALL_SUCCESS_EXIT_CODES` JSON override). 보정은 Server 2022 한정 — 다른 버전·exit code 는 미보정. 운영자 가시성: 보정 태스크는 `success` 배지로 표시되며 별도 안내 라벨은 현재 없음(상세의 `exit_code=2` + 서버 로그 `task_result status remapped` 가 단서).

`boot_time` / `agent_started_at` 가 null 이라 `_log_time_invariants` 검증은 본 메시지에서 호출 안 함.

운영자 가시성: list.html "최근 작업" column (행별 마지막 task badge + polling 갱신) / detail.html "최근 작업" 섹션 (timeline 최근 10건 + row 클릭 modal) / Web API `GET /api/tasks/{task_id}` 단일 + `GET /api/tasks?server_public_id=...` 서버별 cursor pagination. 단일 진실: `web/services/mappers/task.py::to_task_summary` / `to_task_detail` + base.html `.rec-success`/`.rec-failure`/`.rec-pending`/`.rec-unknown`. failure_reason 한글 라벨은 `mappers/task.py::_FAILURE_REASON_LABEL` 카탈로그 (11 enum).

success 경로: agent worker 가 download.url 에서 패키지 fetch → install.sh exec → task.result success 발행 → consumer 6 컬럼 UPDATE → list UI badge `success` 전이. sha256·size 는 `HttpZdmPackageResolver` 가 실 ZDM 호스트에서 HEAD/GET 으로 동적 산출하므로 별도 env 박을 필요 없음. agent download.c 는 http·https 둘 다 허용 (CURLOPT_PROTOCOLS_STR="https,http"), host whitelist (`WORKER_DOWNLOAD_ALLOWED_HOSTS`) 매칭. 메타 fetch 실패 (ZDM 도달 불가·HEAD non-200 등) 시 publish 503 차단.

---

## 규약

- 단위: 메모리 = `kb`, 디스크 / 네트워크 = `bytes` (`/proc` 출력 관례)
- canonical 단위 = Linux `/proc` 모델 단일 진실 (jiffies·kB·bytes·loadavg). 모든 OS agent 가 자기 raw 값을 canonical 로 변환 발행, 엔진은 OS 무관 단일 공식으로 계산 (CPU%·mem%·swap·IOPS·kBps 동일 경로).
- Windows agent 변환 계약 (raw Win32 -> canonical):
  - 단위: cpu FILETIME 100ns `/100000` -> 10ms tick (HZ=100 호환), disk bytes `/512` -> sectors, mem bytes `/1024` -> kB. net/mount 은 bytes 그대로.
  - CPU jiffies (GetSystemTimes idle/kernel/user): `cpu_idle`=idle, `cpu_user`=user, `cpu_system`=kernel `-` idle (Win32 kernel time 은 idle 포함 → 차감 의무, 미차감 시 CPU% 왜곡). idle/user/system 누적 단조증가.
  - 메모리(GlobalMemoryStatusEx): `mem_total_kb`=ullTotalPhys, `mem_available_kb`=ullAvailPhys, `mem_free_kb`=ullAvailPhys (Linux 의 free vs available 구분이 Windows API 에 부재 — 두 키에 동일값 발행. Linux `mem_free`(완전 해제) vs `mem_available`(재할당 가능 cache 포함) 구분을 가정한 엔진 계산은 Windows 호스트에서 used = total - free 가 cache 포함된 used 가 됨. 현재 engine 표시는 mem_available 기준이라 영향 적음). swap(pagefile): `swap_total_kb` = `(ullTotalPageFile - ullTotalPhys)/1024`, `swap_free_kb` = `(ullAvailPageFile - ullAvailPhys)/1024` (pagefile total/avail 은 phys 포함 합산이라 phys 차감 의무). pagefile 의미는 saturation 신호 아님 — `recommendation.swap_saturation(os_family, swap_used)` helper 가 Windows 제외 처리 (ADR 0029).
- canonical 불변식 (agent 발행 의무 + 엔진 2차 강제): 누적 카운터 단조 비감소(reset 은 `boot_time` 변경으로 표현), `mem_available_kb <= mem_total_kb`, `swap_free_kb <= swap_total_kb`(used 음수 금지), per-field >= 0.
- Windows 플랫폼 부재 필드: `load_1m/5m/15m`·`mem_buffers_kb`·`mem_cached_kb`·`listen_ports[].uid` = `null` (0 날조 금지 — 미측정 의미 보존), `cpu_stat.{nice,iowait,irq,softirq,steal}` = `0`. `os_family="windows"` 분기.
- 엔진 정규화 경계 (defense in depth, `consumer/metric_normalize.py`): agent 미신뢰 — 수집 진입에서 canonical 불변식 재강제. `swap_free`/`mem_available` 가 total 초과 시 total 로 클램프(used 음수 차단) + warning. CPU% 의 `cpu_idle` 은 NULL→0 날조 안 함 (idle 부재 reading 제외, report·환경 활용률 동일 규칙).
- 옵셔널 필드: 수집 실패 시 `null` 전송. 수집 실패와 데이터 없음 미구분
- counter reset: 재부팅 / 발행 프로세스 재시작 시 카운터 0 리셋. 엔진은 1순위로 두 시점 `boot_time` 비교 (`web/services/metrics_calculator._is_counter_reset`) -> 시스템 재부팅이면 delta 건너뛰기 (None). 옛 데이터 (`boot_time` NULL) 는 2순위로 `delta < 0` 휴리스틱 fallback (UI 에서 "—"). `agent_started_at` 만 다르면 발행 프로세스 재시작이고 /proc 카운터는 그대로라 정상 delta

---

## 엔진이 받지만 사용하지 않는 필드

다음 필드는 발행 측이 보내지만 엔진 핸들러 / 매퍼에서 의도적으로 무시 (Pydantic `extra=ignore` 또는 매퍼에서 명시적 drop). 활용 결정 시점에 mapper read + inbound DTO 필드 추가가 의무.

| 필드 | 메시지 | drop 사유 |
|------|--------|-----------|
| `disk_io[].major/minor` | metrics | `server_disk_io` 시계열 테이블에 컬럼 없음. compute_disk_io 분류는 device 이름 정규식 |
| `mounts[].free_bytes/avail_bytes` | inventory | 인벤토리는 정적 정보 (`total_bytes`) 만 저장. 동적 사용량은 `server_mount_usage` 시계열로 분리 (`consumer/mappers.py:to_inventory_create`) |
| `boot_time` / `agent_started_at` | error | error 는 로깅 외 활용처 없음 — counter reset 식별과 무관 |

`task.result` 신규 6 필드 (`failure_reason` / `exit_code` / `duration_ms` / `stdout_tail` / `stderr_tail` / `completed_at`) 는 모두 `Task` 테이블 컬럼으로 저장. 현재 ViewModel·UI 표시는 미연결 — 운영자 요구 도달 시 ViewModel 매퍼 + 템플릿 추가 (#F9 영향도 체크리스트).

## 활용 중인 필드

| 필드 | 메시지 | 활용 방식 |
|------|--------|----------|
| `disks[].major/minor` | inventory | `web/services/device_filters.find_parent_disk()` 에서 mount-disk 조인 키 |
| `mounts[].major/minor` | inventory | `web/services/mappers.to_storage_detail` 에서 disks 와 매칭 -> `MountUsageItem.device_name` -> storage.html "Device" 컬럼 |
| `mounts[].major/minor` | metrics | `server_mount_usage.major`/`.minor` 컬럼 저장 (`consumer/mappers.py:to_metric_create`). data-volume 판단 단일 신호 — `device_filters.is_data_volume`(major==0 = 블록 디바이스 없는 가상 fs) + 집계 SQL `_DATA_VOLUME_SQL_FILTER` |
| `boot_time` (inventory) | inventory | `server_inventory.boot_time` 컬럼 저장 + `server_inventory_history` append 시 비교. detail.html / metrics.html 에 KST 표시 |
| `agent_started_at` (inventory) | inventory | `server_inventory.agent_started_at` 컬럼 저장 + history 변경 trigger. 발행 프로세스 재시작 이벤트 감지의 1차 단서 |
| `boot_time` (metrics) | metrics | 시계열 4 테이블 모두 (`server_metrics`·`server_disk_io`·`server_net_io`·`server_mount_usage`) `boot_time` 컬럼 저장. metrics·disk_io·net_io 는 `metrics_calculator._is_counter_reset` 이 두 시점 비교 -> 시스템 재부팅 시 delta 건너뛰기. mount_usage 는 시점값이라 calculator 직접 활용 없으나 메타데이터 일관성 위해 보존 (CLAUDE.md C1 + B) |
| `agent_started_at` (metrics) | metrics | 동일 4 테이블 컬럼 저장. boot_time 동일·agent_started_at 만 다름 -> 발행 프로세스 재시작 (counter 는 /proc 기반 그대로 -> 정상 delta) |
| `ip_internal` (inventory) | inventory | CIDR 파싱(`ip_interface`) -> IP & prefix 로 network address 산출 -> `mappers/topology.build_network_topology` 가 subnet 공동소속 그래프(노드=subnet/host, 엣지=소속) 도출. 대시보드 네트워크 토폴로지 카드(Cytoscape.js). 가상망(docker/virbr·동일 host IP 중복·단독 subnet)·IPv6·link-local 은 mapper 에서 제외 — 실측 아닌 추론 토폴로지 |

---

## Listen 포트 수집

발행 측 수집 경로 OS별:
- Linux: `/proc/net/tcp` / `/proc/net/udp` (IPv4/IPv6) 파싱 — `ss -tlnp` 와 동일한 커널 소켓 테이블.
- Windows: `GetExtendedTcpTable(TCP_TABLE_OWNER_PID_LISTENER)` / `GetExtendedUdpTable(UDP_TABLE_OWNER_PID)` API 호출. `QueryFullProcessImageNameW` 로 pid -> exe 경로 -> basename 만 추출 (`comm`).

### 필드 의미

| 필드 | 설명 |
|------|------|
| `proto` | tcp / tcp6 / udp / udp6 |
| `addr` | 바인딩 주소 (`0.0.0.0` = 모든 인터페이스, `127.0.0.1` = 루프백) |
| `port` | 포트 번호 |
| `uid` | 소켓 소유 유저 ID (0 = root). Linux 만 값 — Windows agent 는 POSIX uid 미존재로 `null` (엔진 nullable 수용) |
| `pid` | 소켓을 열고 있는 프로세스 ID. Linux: 소켓 액티베이션 시 `null` (`/proc/net/tcp` inode -> /proc 매칭 실패). Windows: `GetExtendedTcpTable` 의 `dwOwningPid` 그대로 — `pid=0` = System Idle Process (Linux 의 소켓 액티베이션 의미와 다름). UI 가 `pid=0` 분기 시 OS 별 의미 해석 필요 |
| `comm` | 프로세스명. Linux: `/proc/<pid>/comm`. Windows: `QueryFullProcessImageNameW` exe basename (`PROCESS_QUERY_LIMITED_INFORMATION` 권한 실패 시 `null`). pid 가 null 이면 null |

### 데몬 모델별 동작

fork 모델 (Apache prefork, sshd): 마스터 프로세스가 소켓을 열고 listen. `fork()` 로 자식을 생성해 처리. 연결 수만큼 프로세스가 늘어난다.

이벤트 / 스레드 모델 (nginx, redis, Node.js): 단일 프로세스 (또는 고정 worker) 가 이벤트 루프로 다수 연결 처리. 연결이 와도 프로세스 수는 변하지 않는다.

소켓 액티베이션 (systemd socket activation): systemd 가 소켓을 열어두고, 첫 연결 시점에만 데몬을 기동. 평소에는 프로세스가 없어 `pid` / `comm` 이 null. 주로 가끔 호출되는 시스템 서비스 (cups, avahi 등) 에 쓰인다.

### UI 표현

- `port <= 1024`: well-known 포트 (root 전용 바인딩). 서버 상세 페이지에서 강조 표시.
- `pid` / `comm` null: Linux 는 소켓 액티베이션 소켓 / Windows 는 권한 부족으로 exe 미해상. 둘 다 UI 에서 "—" 표시.
- `pid=0` 은 OS 별 의미 분기: Linux 평시 미발현 (pid 가 0 이면 보통 null 로 mapping), Windows 는 System Idle Process. 분기 명시 안 하면 UI 가 "PID 0" 으로 동일 표기 -> 오인.

---

## 물리 디스크·마운트 필터링

본 절은 Linux agent 발행 기준. Windows agent 는 발행 시점 1차 필터 정책이 다름 (아래 "Windows 한정" 절).

### 현황 (Linux)

Linux 발행 측이 `/sys/block/` 또는 `/proc/diskstats` 스캔 시 물리·가상 디바이스를 구분하지 않고 전부 전송한다. Ubuntu + snapd 환경에서는 snap 패키지 수만큼 `loop0`, `loop1`, ... 가 인벤토리에 포함된다.

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

엔진측 `device_filters.py` (`is_data_volume()`, `is_physical_disk()`) 는 유지 — defense in depth (옛 발행 버전 비대칭 배포 대응, major==0 가상 fs hot-fix, 발행 측 필터 버그 시 최종 방어선).

### Windows 한정

Windows agent 는 `enumerate_physical_disks` (`IOCTL_DISK_GET_DRIVE_GEOMETRY_EX` + `IOCTL_DISK_PERFORMANCE`) 결과를 그대로 발행 — 1차 필터 가상/물리 구분 없음. 발행되는 device 이름은 `"PhysicalDrive0"`, `"PhysicalDrive1"`, ... 형식.

엔진 `device_filters._PHYS_DISK_RE` 에 `PhysicalDrive\d+` 포함 — Windows device 명을 1차 통과 허용. metrics 표 갱신 시 동일 device 이름이 metrics `disk_io[].device` 와 inventory `disks[].name` 양쪽에서 매칭. 가상 디바이스 제외 책임은 Windows agent 의 1차 필터 (`enumerate_physical_disks` 가 물리만 enumerate) 에 위임.

마운트 필터링은 Windows 도 발행 측이 `GetLogicalDriveStringsW` + `DRIVE_FIXED` 만 발행 — 가상 mount 자체 부재라 별도 필터 불필요.

---

## 운영 / 디버깅

agent 발행 측 상태(agent 가 설치된 호스트에서):
```bash
sudo systemctl status assessment-agent --no-pager
sudo journalctl -u assessment-agent --no-pager -n 50
```

end-to-end 추적: (1) 발행 측 로그 -> (2) broker 큐 적재 (`rabbitmqctl list_queues`) -> (3) consumer 처리 로그 -> (4) DB 행 -> (5) web 표시. 끊긴 단계가 원인.

발행 측 재기동: `sudo systemctl restart assessment-agent`.
