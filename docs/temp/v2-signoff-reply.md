# wire v2 계약 — 엔진 sign-off 회신 (엔진 -> 에이전트)

> 성격: 협의 회신. self-contained. 삭제 자유.
> 대상: `v2-contract-handoff.md` + `wire.schema.v2.json` + `v2-example-messages.json` (에이전트 발행).
> 전제: 프로덕션 fleet 없음(GA 전). 엔진은 v1 레거시 우회를 미련 없이 폐기하고 v2가 여는 더 나은 방법을 채택한다.

## 0. 결론 — 조건부 sign-off

계약 승인. MUST-FIX 6신호·envelope 보존·device 안정키·E축 완전체·PSI·스토리지 그래프 전부 반영 확인. 예시 4종이 스키마로 검증되어 datapoint-array 인코딩으로 엔진 inbound DTO/mapper 확정 가능하다.

락 조건 = 2절 2건(task.result/error body 명세 · device 자연키 정책) 확정. 나머지(3절)는 수용, 구현 시 정합.

## 1. 에이전트 sign-off 질문 3건 회신

1.1 datapoint-array 인코딩(`{type,unit,points:[{attr,value}]}`)으로 확정 가능 — YES.
   엔진 소비 방식: 네임스페이스 -> metric명 -> points 순회, attr(device/direction/state/resource/scope/window/cpu)로 시계열 컬럼·차원에 매핑. counter는 boot_time/agent_started_at 게이트 + counter_agg 델타, gauge는 직접. 값 null = 미측정(0과 구분).

1.2 미결 3건 결정:
   - loadavg: 폐기 확정. 엔진 CPU saturation 분류는 이미 run queue(procs_running) 기반이고 loadavg는 미소비 잔재다. v2에선 `cpu.run_queue` + `pressure.stall`(PSI cpu)로 완결 — loadavg는 와이어에서 빼도 된다.
   - swap: 동의. 스왑/pagefile은 `block_devices` type=swap/pagefile 노드(용량=스토리지 스펙). 메모리 압박 신호는 `paging.operations`(page-out)로 분리. 스왑 "사용량 바이트"는 불요(page-out이 더 나은 압박 신호).
   - device id 자연키: 디스크는 안정 id 채택. 단 2.2(id non-null 보장 / 네트워크 name 정책) 확정 전제.

1.3 E축 소비 배선 수용:
   - `network.tcp.retransmits` -> 네트워크 품질(기존 net_retrans 축, tx 세그먼트 분모 델타).
   - `network.conntrack.usage/.limit` -> 네트워크 saturation(연결테이블 고갈 ratio).
   - disk fault(`disk.errors` mdraid/btrfs/ext4/ioerr) + `memory.hardware_corrupted`/`cpu.mce` -> (a) confidence 오염 게이트(steal 패턴처럼 통계 신뢰도 하향) (b) attention 카탈로그(운영 신호 노출). 사이징 분류 자체는 오염 안 시키고 신뢰도·경보로.

## 2. 락 전 확정 필요 (2건)

2.1 task.result / error 메시지 body 명세.
   v2 스키마는 metrics(system.*)·inventory(block_devices) body만 정의했고, `message_type` enum의 `task.result`/`error` body는 미명세다. 엔진 task lifecycle(마감 reaper·시그널 종료 SIG 라벨·task_policy 우선판정)이 이 필드에 의존한다. 확정 요청:
   - task.result: `exit_code`/`signal_no`(int|null 상호배타 — 정상종료=exit_code/시그널종료=signal_no/미포착 둘 다 null, Windows signal_no 항상 null), `task_policy`(bool|null, exit_code보다 우선), 그리고 이 메시지 타입에 한해 `boot_time`/`agent_started_at` nullable override(발행 워커 컨텍스트가 수집 캐시와 분리).
   - error: `failed_component`(자유 문자열).
   두 body는 v2 envelope 아래 v1 구조 그대로 유지 = system.* 재설계 대상 아님임을 명시해 스키마에 박아줘. 안 잡히면 task 처리·상세 표시가 깨진다.

2.2 device 자연키 정책 (비대칭 명확화).
   시계열 자연키를 device로 잡는데 예시상 디스크는 안정 id(`serial:`/`partuuid:`/`md:`), 네트워크는 name(`ens3`)이다. 즉 "안정 id 키"는 디스크 한정, 네트워크는 name(v1 동일). 확정 요청:
   - metric을 싣는 디스크 device의 `id`(attr `device` 값)는 non-null 보장인가. `id_type=name` 폴백이 metric-bearing 디스크에도 나올 수 있으면 시계열 dedup 키(server_id, device, collected_at)가 불안정해진다. metric device는 안정 id non-null을 계약으로 보장 요청(또는 폴백 규칙 명시).
   - 네트워크 인터페이스 키는 name 유지 확정(Windows 인터페이스 name 안정성 — 재부팅/NIC 재발급 시에도 동일 name 인가).
   이건 엔진 시계열 UNIQUE·마이그레이션 설계를 좌우한다.

## 3. 추가 요청 (non-blocking, 구현 정합)

3.1 PSI 집계 소스 합의. `pressure.stall.time`(counter, 누적 초 = 시간적분)을 14일 saturation의 canonical 소스로 쓴다 — `pressure.stall.ratio`(gauge avg10 점표본)보다 창 평균 압박에 정확. 둘 다 발행하니 계약 변경 아님, 소비 합의만.

3.2 per-cpu 페이로드 스케일. `cpu.time`이 cpu_index x state라 고코어(예 64코어 x 상태)면 point 수가 크다. 엔진은 hottest-core p95(단일스레드 병목 보호)에 per-cpu 시계열이 필요해 전체 발행이 맞지만, 상한/다운샘플 정책이 있는지(대형 VM 대비) 확인. 우리 VM 규모면 그대로 수용.

3.3 memory 이용률 분모. `memory.usage` state=available(Linux MemAvailable / Windows Available MBytes) 상시 발행 확정. 엔진은 used% 대신 1 - available/limit로 압박을 잰다(v1 used% 개선). available 미발행 커널만 계산 폴백.

## 4. 엔진이 v2로 폐기·전환하는 레거시 (소비 확정 = 에이전트 구현 우선순위 입력)

프로덕션 없으니 아래는 v2에서 전면 대체한다. 에이전트 관점에선 "엔진이 실제로 소비할 v2 신호" 확정이라 구현 우선순위로 쓰면 된다.

- Windows 디스크 await IOCTL + disk queue 폴백 폐기 -> `disk.operation_time`/`disk.operations`(perflib) 델타 await로 양 OS 통일. "특정 viostor 버전만 파싱" 버그 부류 소멸. -> 에이전트는 perflib 전환에 전면 커밋(IOCTL 성능 경로 revert).
- os-aware saturation 대리신호 비대칭 폐기 -> Linux(현대)는 `pressure.stall`(PSI) cpu/mem/io 한 축, null(구커널·Windows)일 때만 run_queue/commit/await 폴백. PSI-first 단일 경로.
- 스왑리스 Linux 메모리 포화 사각 폐기 -> 현대커널 PSI memory + 구커널 스왑리스는 `memory.commit.usage/.limit`(commit ratio)로 폴백. v1의 "이용률 90% 단독" 사각 축소.
- loadavg 폐기 -> `cpu.run_queue`(+PSI). 와이어에서 빼도 무방.
- 스토리지 major/minor 조인 추론 폐기 -> `block_devices` parent-by-id + `lvm_vgs` free_bytes로 실측 3계층(배정/파일시스템/확장여력). fs->물리디스크 확정 매핑.
- 단위 정규화 수용 -> jiffies/sectors/100ns/% 걷어내고 s/By/ratio. 엔진 counter_agg cagg 컬럼·임계 상수·시계열 단위 대응은 엔진 몫.

## 5. sign-off 이후 흐름

- 2절 2건 확정 회신 -> 계약 락.
- 에이전트: 양 트리 v2 수집 구현 + `schema/wire.schema.json` 정본 교체 + check-contract.sh v2 + testbed 재검증.
- 엔진: v2 마이그레이션 계획(티어) 착수 — ingest DTO/파싱 -> DB 스키마·단위 마이그레이션(device 키·신규 컬럼) -> recommendation 배선(PSI-first) -> 신규 신호 표시. dual-read 불요(pre-prod, flag-day cutover 동의). GA 시점에 schema_version 기반 dual-read 재검토.
