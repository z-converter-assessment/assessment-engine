# Agent (메시지 데이터 계약)

정책: CLAUDE.md #B. 본 문서는 엔진이 송수신하는 메시지의 데이터 형식 단일 진실. 외부 호스트 발행 / 엔진 수신 두 방향 모두. 발행 측 5종 바이너리(Linux x86_64·EL6, Windows 2016·2008R2·2003)가 동일 스키마를 발행하고, OS별 차이는 각 필드 설명에 명시한다.

---

## 공통 메타데이터 (`MessageBase`)

모든 메시지에 공통 포함.

| 필드 | 타입 | 설명 |
|------|------|------|
| `message_type` | string | 본 문서 메시지 타입별 Literal |
| `agent_id` | string (UUID v4) | 호스트 식별자 — 첫 실행 시 생성·영구 저장하는 UUID (MAC/machine_id 재발급과 무관하게 불변). agent worker 큐 키 (`agent.tasks.{agent_id}`). 엔진 식별키를 composite_id 에서 agent_id 로 옮기는 전환은 별도 ADR 대상 (현재 엔진은 composite_id 로 식별) |
| `composite_id` | string max=64 | SHA-256(`machine_id` + 정렬·dedup MAC). 현재 엔진 식별·저장 키 (DB UNIQUE·URL public_id 매핑). `task.result` 한정 `null` 허용 (worker 컨텍스트 — `task_id` 로 매칭) |
| `machine_id` | string\|null max=64 | raw machine-id (Linux `/etc/machine-id`, Windows `MachineGuid`). 표시·감사 전용 |
| `agent_version` | string max=32 | 발행 측 빌드 버전 (스키마 계약 버전 아님 — 릴리즈 정체성. 계약 변경은 필드/큐 구조로 표현) |
| `collected_at` | datetime (ISO 8601 UTC) | 수집 시각 |
| `hostname` | string max=255 | 보조 식별자 (가변) |
| `message_id` | UUID v4 | 멱등성 키 |
| `boot_time` | datetime UTC | 시스템 부팅 시각 (정적 소스 — Linux `/proc/stat btime`, Windows 부팅 시각). 프로세스 시작 시 1회 캐시. `task.result` 한정 `null` 허용 |
| `agent_started_at` | datetime UTC | 발행 프로세스 기동 시각. `task.result` 한정 `null` 허용 |
| `os_family` | `"linux"` \| `"windows"` | OS family — 모든 메시지 진입 시점 OS 분기 단일 진실. 양 OS agent 가 전 메시지에 발행 (required). inventory 저장값이 `task.install` dispatch 단일 진실 (ADR 0020) |

엔진 처리는 `consumer/handlers/`의 `_check_idempotent`(Redis SET NX 24h)로 message_id 중복 차단 후 본문 처리. fail-open 보장은 시계열 4테이블 자연키 UNIQUE(#C1)가 흡수 (#D2).

---

## server.inventory (호스트 -> 엔진, 정적 인프라 정보)

routing key `server.inventory`. 기동 시 1회 + 주기 재발행.

| 필드 | 타입 | 설명 |
|------|------|------|
| `os_family` | `"linux"` \| `"windows"` | task.install dispatch 단일 진실 (ADR 0020) |
| OS / kernel | string\|null | `os_id` / `os_version` / `os_codename` / `kernel_version`. Windows `os_version`=DisplayVersion |
| CPU | int\|null / string\|null | `cpu_cores` / `cpu_model` |
| 메모리 / 스왑 | int\|null (KB) | `mem_total_kb` / `swap_total_kb` |
| `disks[]` | object | `{name, size_bytes, type, major, minor, kind}`. `kind` = 물리/가상 분류 태그(하단 taxonomy) — 물리 판정 단일 신호. Windows: `name="PhysicalDriveN"`, `major=0`, `minor=`drive index, `kind="physical"` |
| `mounts[]` | object | `{mount, fstype, total_bytes, free_bytes, avail_bytes, major, minor, kind}`. `kind` = data/boot/image (데이터 볼륨=`kind="data"`). Windows: `mount=`drive letter, `kind="data"` |
| `interfaces[]` | object | `{name, address, prefix, family, kind, gateway}` (IPv4+IPv6). address=bare IP, family=`"ipv4"`\|`"ipv6"`, kind=iface 분류(하단 taxonomy), gateway=default route IP\|null. loopback 제외. 소비: 상세 표시(`mappers/server._to_ip_addrs`) + 네트워크 토폴로지(`mappers/topology.build_network_topology` — kind 로 물리만, gateway 로 중복 대역 disambiguation) |
| `ip_external[]` | list[string]\|null | 외부 IP (bare). 미접근 시 `null` |
| `mac_addresses[]` | list[string] | NIC MAC 목록 (감사용, 식별 미사용 — 식별은 composite_id/agent_id) |
| `services[]` | object\|null | `{unit, sub, pid, exe}`. `null`=비-systemd/SCM 실패, 빈 배열=서비스 0개. `pid`(int\|null)로 `listen_ports[].pid` 와 조인해 unit-포트 귀속 (`service_classifier`). Linux `sub`=systemd 상태 가변값, Windows=항상 `"running"`. EL6/비-systemd·NT5 는 pid/exe `null` |
| `listen_ports[]` | object | `{proto, addr, port, uid, pid, comm}` |

---

## server.metrics (호스트 -> 엔진, `collection_interval_sec` 주기)

routing key `server.metrics`. 모두 raw 누적값. 엔진이 연속 2회 readings 의 차(delta) 로 CPU% / IOPS / kBps 계산.

| 필드 | 타입 | 설명 |
|------|------|------|
| `collection_interval_sec` | int\|null | 설정된 수집 주기(초). 표본 충분성 기준 (없으면 5분 버킷 288/day 가정 — 주기<=5분이면 무관) |
| `cpu_stat` | object | `user/nice/system/idle/iowait/irq/softirq/steal` jiffies. Windows: user/system/idle 만 측정, `iowait` 등 = `0` (더미 — 엔진 disk saturation 은 os-aware 로 `saturation.disk_queue` 사용) |
| 메모리·스왑 | int\|null (KB) | `mem_*_kb` / `swap_*_kb` |
| `load_1m` / `load_5m` / `load_15m` | float\|null | Load Average. Windows `null` (OS 부재) |
| `disk_io[]` | object | `{device, reads_completed, writes_completed, sectors_read, sectors_written, major, minor, kind}`. `kind="physical"` 만 집계. Windows: `device="PhysicalDriveN"` |
| `mounts[]` | object | `{mount, fstype, total_bytes, free_bytes, avail_bytes, major, minor, kind}`. `kind="data"` 만 데이터 볼륨 집계. Windows: `mount=`drive letter |
| `net_io[]` | object | `{interface, rx_bytes, tx_bytes, rx_packets, tx_packets, rx_errors, tx_errors, kind}`. `kind="physical"` 만 집계 (master/member 이중 집계 회피). Windows: `interface`=friendly name (UTF-8) |
| `saturation` | object\|null | Windows USE Method raw 신호 `{disk_queue, cpu_run_queue, mem_paging_rate}`. `disk_queue`=물리 디스크별 `[{device, queue}]` (빈 배열=미측정) — 엔진이 per-device max 로 축약해 disk saturation 판정. `cpu_run_queue`/`mem_paging_rate`=현재 `null` (검증 후 raw 값). Linux 는 iowait/load 사용이라 미발행 |

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
- 수신 큐: `agent.tasks.<composite_id>` (durable, `x-message-ttl=3600000`, `x-max-length=100`, `x-overflow=reject-publish`). 큐는 엔진이 task 발행 시점에 동적 declare — 수신 측은 declare 권한 없음.
- 식별 전환 (미완, ADR 대상): agent worker 는 `agent.tasks.{agent_id}` 를 구독한다. 엔진 라우팅·큐 declare 를 composite_id 에서 agent_id 로 옮기는 것이 짝 — 전환 완료 전까지 task.install 배달이 어긋난다 (수집은 정상).

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

본 메시지는 수집 컨텍스트와 분리된 worker 프로세스에서 발행되어 `composite_id` / `boot_time` / `agent_started_at` 가 `null` 가능 — 엔진은 `TaskResultInput` 에서 세 필드를 nullable override. 결과 매칭은 `task_id` 로 한다. `os_family` / `os_id` / `os_version` 은 양 OS worker 가 발행 (inventory 와 동일 소스) — 성공 보정 정책 매칭 키.

| 필드 | 타입 | 설명 |
|------|------|------|
| `message_type` | `"task.result"` | Literal |
| `task_id` | UUID | `task.install` 의 동일 값 회신. 엔진 `Task.public_id` 매칭 키 |
| `status` | `"success"` \| `"failure"` | 실행 결과 |
| `failure_reason` | string\|null max=32 | 실패 분류. 알려진 값: `url_not_allowed` / `download_failed` / `sha256_mismatch` / `extract_failed` / `script_not_found` / `script_failed` / `script_timeout` / `insufficient_disk` / `internal_error` / `already_done` / `unsupported_install_type`. 성공 시 null. 알려지지 않은 값은 silent pass (DB 저장은 그대로) |
| `exit_code` | int\|null | install.sh 종료 코드. 실행 전 실패 시 null |
| `os_id` | string\|null max=64 | OS 식별자 (Linux distro id, Windows `"windows"`). 성공 보정 정책 매칭 키 |
| `os_version` | string\|null max=64 | OS 버전 (inventory 와 동일 소스 — Windows=DisplayVersion). 성공 보정 정책 매칭 키 |
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
| `disk_io[].major/minor` | metrics | `server_disk_io` 에 컬럼 없음. 물리 판정은 `kind` 컬럼 |
| `mounts[].free_bytes/avail_bytes` | inventory | 인벤토리는 정적 정보 (`total_bytes`) 만 저장. 동적 사용량은 `server_mount_usage` 시계열로 분리 (`consumer/mappers.py:to_inventory_create`) |
| `boot_time` / `agent_started_at` | error | error 는 로깅 외 활용처 없음 — counter reset 식별과 무관 |

`task.result` 신규 6 필드 (`failure_reason` / `exit_code` / `duration_ms` / `stdout_tail` / `stderr_tail` / `completed_at`) 는 모두 `Task` 테이블 컬럼으로 저장. 현재 ViewModel·UI 표시는 미연결 — 운영자 요구 도달 시 ViewModel 매퍼 + 템플릿 추가 (#F9 영향도 체크리스트).

## 활용 중인 필드

| 필드 | 메시지 | 활용 방식 |
|------|--------|----------|
| `disks[].major/minor` | inventory | `web/services/device_filters.find_parent_disk()` 에서 mount-disk 조인 키 |
| `mounts[].major/minor` | inventory | `web/services/mappers.to_storage_detail` 에서 disks 와 매칭 -> `MountUsageItem.device_name` -> storage.html "Device" 컬럼 |
| `mounts[].major/minor` | metrics | `server_mount_usage` 컬럼 저장 — mount-disk 조인 보조. data-volume 판단은 `kind="data"` (`device_filters.is_data_volume` + cagg `kind='data'`) |
| `boot_time` (inventory) | inventory | `server_inventory.boot_time` 컬럼 저장 + `server_inventory_history` append 시 비교. detail.html / metrics.html 에 KST 표시 |
| `agent_started_at` (inventory) | inventory | `server_inventory.agent_started_at` 컬럼 저장 + history 변경 trigger. 발행 프로세스 재시작 이벤트 감지의 1차 단서 |
| `boot_time` (metrics) | metrics | 시계열 4 테이블 모두 (`server_metrics`·`server_disk_io`·`server_net_io`·`server_mount_usage`) `boot_time` 컬럼 저장. metrics·disk_io·net_io 는 `metrics_calculator._is_counter_reset` 이 두 시점 비교 -> 시스템 재부팅 시 delta 건너뛰기. mount_usage 는 시점값이라 calculator 직접 활용 없으나 메타데이터 일관성 위해 보존 (CLAUDE.md C1 + B) |
| `agent_started_at` (metrics) | metrics | 동일 4 테이블 컬럼 저장. boot_time 동일·agent_started_at 만 다름 -> 발행 프로세스 재시작 (counter 는 /proc 기반 그대로 -> 정상 delta) |
| `interfaces` (inventory) | inventory | `kind="physical"` 인터페이스의 IPv4 로 subnet 공동소속 그래프 도출 (`mappers/topology.build_network_topology`, Cytoscape.js). gateway 로 중복 사설 대역 disambiguation. 가상망은 kind 로 제외 — 실측 아닌 추론 토폴로지 |

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

## device 분류 (kind taxonomy)

발행 측 공용 분류기 하나가 각 device/interface/mount 에 `kind` 를 태그한다. 엔진은 정규식·major 추론 없이 `kind` 로만 물리/데이터 판정 (`device_filters` — 화면·집계·용량·cagg 단일 기준, Windows major=0 문제 해소).

| 대상 | kind 값 | 판정 |
|------|---------|------|
| `disks[]` / `disk_io[]` | physical, partition, lvm, raid, virtual | 물리 = `kind="physical"` |
| `interfaces[]` / `net_io[]` | physical, loopback, bridge, veth, bond_master, bond_member, vlan, tunnel, virtual | 물리 = `kind="physical"` (master/member 이중 집계 회피) |
| `mounts[]` | data, boot, image | 데이터 볼륨 = `kind="data"` |

- Windows 는 coarse 분류 (net: physical/loopback/tunnel/virtual; disk/mount 상수 physical/data). Linux 는 세분.
- 발행 측이 lo/loop/ram/sr·가상 fs 등 무의미 항목은 pre-drop 하고, 나머지는 kind 태그로 전송 (엔진이 관측성 유지하며 kind 로 필터).
- `major`/`minor` 는 물리 판정에서 빠지고 mount-disk 조인(`device_filters.find_parent_disk`) 전용으로만 잔존.

---

## 운영 / 디버깅

agent 발행 측 상태(agent 가 설치된 호스트에서):
```bash
sudo systemctl status assessment-agent --no-pager
sudo journalctl -u assessment-agent --no-pager -n 50
```

end-to-end 추적: (1) 발행 측 로그 -> (2) broker 큐 적재 (`rabbitmqctl list_queues`) -> (3) consumer 처리 로그 -> (4) DB 행 -> (5) web 표시. 끊긴 단계가 원인.

발행 측 재기동: `sudo systemctl restart assessment-agent`.
