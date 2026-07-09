# 통합 자원 데이터 모델 — 엔진 피드백 (엔진 -> 에이전트)

> 성격: 협의 회신(엔진 -> 에이전트). self-contained. 삭제 자유.
> 대상: `unified-resource-data-model.md`(에이전트 발행 v2 정본). 이 피드백대로 조정 후 구현 착수 합의용.
> 판단 기준: 엔진 right-sizing(USE Method + rollup_host 인과 모델)이 실제 소비하는 신호를 하나도 잃지 않는 것 + 고객사 에이전트의 비대칭 배포 안전.

## 0. 총평 — 방향 승인

전면 재설계 방향은 승인한다. 특히 아래는 그대로 간다:

- OTel system.* 네이밍/속성/단위/카운터 타이핑 정렬 — 정석. 우리도 이 관례로 소비 코드를 짠다.
- 2레이어 분리(wire=raw / engine=USE 해석) — 맞다. USE 는 이미 `recommendation.py`가 하는 일이고 wire 에 USE 를 넣으면 파편화된다.
- source attribute 로 OS 신호원 비대칭 노출 — 엔진이 os_family 분기 대신 source 로 판별/랭킹하는 게 맞다. 지금 엔진이 `COALESCE(await, win_await)` 식으로 OS 분기하는 걸 source 라벨로 걷어낼 수 있다.
- null=측정불가 / 값=실측(0 포함) — 계약으로 못박는 거 동의. 엔진은 이미 이 의미론에 의존한다.
- counter(monotonic) / gauge 타이핑 명시 — 엔진이 델타를 낼 때 counter reset 처리와 직결(3절 참조).
- base 단위(seconds/bytes/ratio) 정규화 — jiffies/sectors/100ns/% 를 에이전트에서 걷어내는 거 승인. 엔진 저장/집계 단위는 우리가 대응한다.

아래 1절(누락 신호)이 이 피드백의 핵심이다 — 재정규화 과정에서 엔진 인과 모델이 쓰는 신호 몇 개가 표에서 빠졌다. 이건 반드시 복원해야 한다.

## 1. MUST-FIX — 재정규화 중 누락된 엔진 소비 신호

엔진 `recommendation.ResourceStats`(33필드)와 rollup_host 인과 판정이 실제로 쓰는데 v2 표에 없는 신호. 빠지면 해당 판정이 죽는다. 각 항목에 "v2 어디에 넣을지"까지 제안한다.

| 누락 신호 | 엔진 용도 (없으면 죽는 판정) | v2 배치 제안 |
|---|---|---|
| procs_blocked (D-state 수) | 인과 근본원인 분리 — disk I/O 발 CPU 로드를 "디스크가 root, CPU 는 증상"으로 억제(`rollup_host` disk_io->cpu 게이트). PSI io 가 있으면 대체되나 PSI 없는 커널/Windows 에선 이게 유일 신호 | `system.cpu` 에 `cpu.blocked` g {tasks} source(procs_blocked) 추가 (Linux /proc/stat `procs_blocked`; Windows 등가 없으면 null) |
| conntrack count/max | 네트워크 포화 — 연결테이블 고갈 임박(`conntrack_ratio`). 네트워크는 링크속도 null(virtio)이 흔해 이게 실질 포화 신호 | `system.network` 에 `network.conntrack.usage` g {connections} + `network.conntrack.limit` g (Linux nf_conntrack count/max; 없으면 null) |
| TCP 재전송 (RetransSegs) | 네트워크 품질/혼잡 — `net_retrans_pct`. NIC errors(CRC/frame)와 다른 축(TCP 레벨 재전송) | `system.network` 에 `network.tcp.retransmits` t {segments} (Linux /proc/net/snmp Tcp RetransSegs; Windows MIB_TCPSTATS dwRetransSegs). NIC `network.errors` 와 별개 metric |
| filesystem inodes | 디스크 용량 판정 2번째 축 — inode 소진(`disk_inode_used_pct`·`disk_inode_runway_days`). 바이트 85% 가드와 대칭. 바이트 여유인데 inode 고갈로 쓰기 실패하는 케이스 포착 | `system.filesystem` 에 `filesystem.inodes.usage` g {inodes} state(used/free) (Linux statvfs f_files/f_ffree; Windows N/A -> null) |
| VM 레벨 OOM | 메모리 under 사후 증거(`oom_occurred`). cgroup.memory.events 는 컨테이너 전용 — VM(전용 커널)엔 안 옴 | `system.memory` 에 `memory.oom_kill` t {events} (Linux /proc/vmstat oom_kill; Windows 등가 없으면 null). cgroup 것과 별개 |
| per-CPU 이용률 | 단일스레드 병목 보호 — `cpu_percore_p95_max`(가장 뜨거운 코어). 다운사이즈 시 코어 수 줄여도 되는지 판단에 hottest core 필요 | `cpu.time` attr 에 `cpu`(logical index) 추가 — OTel 정본이 이미 `cpu` attribute 지원. state x cpu 2차원 |

추가로 확인만(누락은 아니나 매핑 확정 필요):
- loadavg(`cpu_load_15m_max`) — `cpu.run_queue`(procs_running 순간값)로 대체하려는 의도면 명시해줘. 엔진은 지금 load 15m 도 본다. run_queue p95 로 갈지, loadavg 도 실을지 결정 필요.
- swap 할당 크기 — 프로비저닝 스펙(디스크에 배정된 swap 용량, Linux swap partition/file == Windows pagefile)은 메모리가 아니라 스토리지다. `block_devices[]` 에 swap 파티션/pagefile 노드로 잡히면 거기서 파생 가능. type=swap 또는 mountpoint=`[SWAP]` 같은 라벨로 식별 가능하게 해줘. (page-out 사용 신호는 paging.operations 로 이미 커버.)

## 2. 메시지 envelope 보존 (명시 요구)

v2 표는 metric 네임스페이스만 기술하는데, 메시지 봉투(envelope) 메타는 현행 계약 그대로 유지해야 한다. 이건 엔진의 멱등성·counter reset·식별의 근간이라 협상 불가:

- `agent_id`(불변 UUID) — 식별·라우팅·upsert 단일 키. 유지.
- `message_id` — 멱등성 1단(Redis SET NX) + 2단(DB UNIQUE) 키. 유지.
- `composite_id`(SHA-256, nullable) / `machine_id`(nullable) — 감사·clone collision 진단. 유지.
- `collected_at` — 시계열 자연키·partition pruning 술어. 유지.
- `boot_time` / `agent_started_at` — counter reset 정밀 식별(3절). 시계열 4테이블 공통 메타. 유지 필수.

즉 v2 는 "envelope(불변) + system.* payload(재설계)" 구조다. envelope 를 payload 재설계에 휩쓸어 바꾸지 말 것.

## 3. counter reset 계약 — 확정 요청

"agent=raw counter, engine=derive delta" 는 좋은데, 엔진이 델타를 낼 때 counter 가 언제 리셋되는지가 계약으로 못박혀야 한다. 엔진은 값-감소 기준 counter_agg + boot_time/agent_started_at 게이트로 리셋을 흡수한다. 확인할 것:

1. 재부팅 시 커널 counter(cpu.time·disk.io·network.io 등)는 0 부터 재시작 -> 엔진은 `boot_time` 변경으로 감지. 보장돼?
2. 에이전트 재시작 시, 에이전트가 프로세스 내부에 counter baseline 을 들고 있다면 재시작에 리셋될 수 있음 -> 엔진은 `agent_started_at` 변경으로 감지. 에이전트는 raw 커널 누적값을 그대로 싣고(내부 baseline 빼지 말고), 델타는 엔진이 두 스냅샷 차로 내는 게 맞다. 확정해줘.
3. wraparound(32bit counter 랩) — base 단위(bytes/seconds float)로 가면 상당수 완화되나, 남는 랩은 counter_agg 가 값-감소로 처리. counter 로 타이핑된 필드는 monotonic 을 계약으로.

device 이름 안정성도 같이: 시계열 자연키가 (server_id, device, collected_at) 라 `device` attribute 는 메시지 간 안정해야 한다. 특히 Windows perflib 인스턴스 이름(예: `0 C:`)이 재부팅/디스크추가에 바뀌지 않는지, 바뀌면 `id`(안정 조인 키)를 device 정규화의 1차 키로 쓸지 정해줘.

## 4. 마이그레이션 — flag-day 반대, 엔진-first dual-read

v2 의 "schema_version 으로 에이전트-엔진 동시 전환(flag-day)" 은 우리 배포 모델과 안 맞는다. 고객사 내부망 에이전트는 우리가 일괄 재배포 못 하고 자기 페이스로 올라온다. 동시 전환은 전환 순간 혼재 fleet 을 깨뜨린다.

대안(표준 비대칭 안전):

1. 엔진이 먼저 v2-capable 로 배포 — v1·v2 둘 다 읽는 reader. `schema_version` 으로 메시지가 자기 버전 선언.
2. 에이전트는 자기 페이스로 v2 로 전환. 전환 안 된 에이전트는 v1 계속 발행, 엔진이 v1 reader 로 처리.
3. 엔진 입력 모델 `extra=ignore` 는 유지 — v2 메시지에 엔진이 모르는 신규 필드 와도 안전.

즉 순서는 "엔진 dual-read 배포 -> 에이전트 롤아웃 -> fleet 전부 v2 확인 후 엔진에서 v1 reader 제거". 동시 전환 아님.

전환기 additive 유지 항목:
- `disks[]`/`mounts[]` + `major/minor` — `block_devices[]` ingest 가 엔진에 라이브로 붙고 검증될 때까지 계속 발행. 같은 릴리스에서 disks/mounts 를 걷어내지 말 것("점진 폐기" 방향은 동의, 순서는 엔진 확인 후).
- base 단위 정규화는 cutover 성격이 크다(엔진 counter_agg cagg 컬럼·RS_* 임계·DB 컬럼 단위 동반). 이건 우리가 마이그레이션으로 받되, 위 dual-read 로 v1(구단위)/v2(base단위)를 버전으로 갈라 읽으면 flag-day 없이 흡수 가능.

## 5. 시퀀싱 — 엔진 가치 순위

모델 우선순위(1 Windows perflib, 2 base단위+layer, 3 PSI+link+commit, 4 storage graph, 5 errors+cgroup)에 대해 엔진 관점 조정:

- Windows 디스크 perflib 전환 #1 동의. 이건 라이브 버그다 — 현재 엔진이 구세대 viostor Windows 의 디스크 I/O 포화(await/큐)를 특정 버전 빼고 못 읽는다. 실기 검증(dp-win2012 await 1.5ms/큐 0.78/busy 80%)도 있고 세 요청 교차점이라 먼저 가는 게 맞다. IOCTL revert(레지스트리 영구 변경 + 재부팅 의존)가 부적절하다는 판단도 전적 동의 — 관측 전용 에이전트가 시스템 설정을 영구 변경하면 안 된다.
- 단 PSI 를 #3 로 미루지 말 것. 엔진 right-sizing 가치는 PSI 가 단일 최대다(우리 resource-signals 요청의 최우선). swapless Linux 메모리 포화 사각을 근본 해소하고 cpu/mem/io saturation 을 한 축(ratio)으로 통일한다. perflib 와 병렬로 가거나 바로 다음이어야 한다. base 단위 정규화(#2)보다 PSI 가 판정 정확도에 더 크다.
- 스토리지 그래프(block_devices/lvm_vgs)는 inventory 경로라 metric 재설계와 독립 -> 병렬 진행 가능. 엔진도 ingest 를 따로 붙인다. VG free_bytes + 부모 체인이 3계층(배정/파일시스템/확장여력) 실측을 여는 핵심이라 가치 높다.
- Errors(disk/net err)·cgroup 은 엔진도 후순위 동의(USE E 축은 있으면 좋은 보강).

## 6. 수용된 한계 (에이전트 책임 아님, 기대치 정렬)

- virtio-net link speed null -> 네트워크 U(이용률 %) 산출 불가. OpenStack VM 대다수가 virtio라 네트워크는 사실상 U 없이 errors/drops/retrans + 절대 처리량으로 평가하게 된다. 커널 한계라 수용. (혹시 하이퍼바이저 메타/설정으로 링크속도 주입 가능하면 옵션으로 논의하되 필수 아님.)
- old-kernel(EL6/7·SLES11-12·Debian10) swapless -> PSI null 이라 메모리 포화 사각 잔존. PSI 를 "폴백 대체가 아니라 보강"으로 둔다는 모델 서술과 정합 — 이 군은 util 90% 단독으로 남는다(엔진도 이 한계를 명시 표시).

## 7. 엔진이 줄 것 / 다음 스텝

- 요청: canonical 예시 메시지 1건 — 한 호스트(Linux 1 + Windows 1)의 전 네임스페이스가 실제 채워진 v2 JSON 샘플. datapoint-array 인코딩({attributes..., value})의 구체 shape 를 봐야 엔진 inbound DTO·mapper·시계열 컬럼 매핑을 확정한다.
- 요청: `schema/wire.schema.json` v2 초안 — system.* 섹션 + os_family/커널 조건부 + counter/gauge/단위 명시. 엔진이 리뷰 후 1절 누락 신호 반영 확인.
- 엔진 측 병행: 위 dual-read reader 골격 + 시계열 컬럼/counter_agg 단위 대응 지점(consumer/mappers `_await_fields`·`_max_disk_queue`, recommendation 배선, db 컬럼)을 미리 스코프.

정리하면: 방향 승인 + 1절 6개 신호 복원(procs_blocked·conntrack·tcp retrans·inodes·VM oom·per-cpu) + envelope 보존 + flag-day 대신 엔진-first dual-read. 이 넷이 합의되면 착수 OK.
