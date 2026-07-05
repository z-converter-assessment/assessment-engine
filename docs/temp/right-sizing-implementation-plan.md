# 자원 적정성 분류 재설계 — 구현 실행 플랜 (ADR 0052)

성격: 임시 실행 플랜(docs/temp). 장기 자율 작업용. 구현 완료·wrap-up 후 삭제.

목표: ADR 0052 의 결정(전제 기반 유도 + USE 5자원 + tier 근거 + 신뢰도 4종 + 근본원인 종합)을 코드로 구현. 설계 단일 진실 = ADR 0052 + `right-sizing-principle.md`(초안).

## 의존·전제

- 에이전트 신규 신호: `../assessment-agent-temp/docs/right-sizing-signals-request.md` 로 요청 전달됨. 스키마·DTO 는 그 명세로 선행 가능, 실데이터는 에이전트 갱신 후.
- Windows 디스크 await 5대 미측정: 별도 infra/ETW 트랙(`../assessment-infra-temp/docs/etw-diskio-verification-request.md`). 엔진은 "포화 미관측"으로 처리 — 이 플랜과 독립.
- feature 브랜치 위에서만. base 는 프로젝트 규약대로.

## Phase B — 도메인 로직 (recommendation.py 재구성)  [완료 2026-07-05 — 51 신규 단위 테스트, ruff clean, 회귀 0]

완료분(additive strangler — 기존 assess/classify 무손상, Phase E 에서 제거):
- ResourceStats 확장(신 신호 전부 default) · 신 임계 상수 16개(계층·출처 주석) · steal 충실도 편향
- ResourceAssessment/HostAssessment/ConfidenceNote(4종) · assess_cpu/memory/disk_capacity/disk_io/network
- 사이징(CPU ceil(부하/70) max 포화 · 메모리 수요/0.70) · rollup_host(근본원인) · host_status(idle/shutdown 포함) · network_congested · downsize_prescribable · RS_*_LABEL_KO
- per-core 는 스칼라 cpu_percore_p95_max(최댓값만) 사용 -> CP2 "배열 vs JSONB" 결정 소멸(스칼라 컬럼).

CP1 사용자 결정 대기: (1) 호환 wrapper strangler 방식 OK? (2) 보류분 — steal 은 반영, idle/shutdown·라벨도 반영 완료. 남은 건 없음(Phase B 완결).

가장 자기완결적이고 합성 단위 테스트로 완결 가능. 실데이터·에이전트·사용자 무관.

- B1. `ResourceStats` 확장: per-core p95, procs_blocked, pswpin/pswpout, await_p95, inode(f_ffree), net drops/retrans, sockstat/conntrack 등.
- B2. tier 근거 임계를 명명 상수로 — 각 상수에 (계층, 출처) 주석:
  - CPU_UTIL_UNDER=70(계층2 큐잉+계층3 AWS Balanced) · CPU_SIZING_TARGET=70 · CPU_SAT_RATIO=0.7 · CPU_PERCORE_HOLD=85
  - MEM_UNDER=90(계층3 Azure) · MEM_SIZING_TARGET=70(계층3 AWS 30% headroom)
  - DISK_RUNWAY_DAYS=30 · DISK_STATIC_GUARD=85(계층3) · DISKIO_AWAIT_MS=20(계층3 VMware/SQL)
  - NET_RETRANS_PCT=1 · NET_DROP_PCT=0.5(계층3) · CONFIDENCE_MIN_HOURS=30(계층3 AWS insufficient-data)
- B3. 자원별 assess: `assess_cpu` / `assess_memory` / `assess_disk_capacity` / `assess_disk_io` / `assess_network` -> 자원별 Assessment(분류·triggers·unmeasured·confidence).
- B4. 사이징: CPU ceil(부하/70) max 포화(0.7) · 메모리 수요/0.70 · 디스크 용량 runway(Theil-Sen). 디스크 I/O·네트워크는 표시(사이징 없음).
- B5. 신뢰도 4종: 통계 정밀도(표본·분산 + 30h floor) / 커버리지(unmeasured) / 충실도(virtio·근사 편향) / 정상성(추세).
- B6. 종합·근본원인: swap 발생·procs_blocked·await 판별로 root 자원 -> root 만 처방, 하류 재평가. iowait 미사용.
- B7. 다운사이즈 규칙: 과다 분류는 항상 발화 + "처방"만 게이트(신뢰도 높음 AND 상승추세 아님).
- 호환: 옛 `classify`/`assess` 호출처 회귀 — wrapper 유지 여부는 CP1 체크포인트(사용자 결정).
- 검증: 단위 테스트(합성 stats) — 자원별·분기별·근본원인·신뢰도·사이징. [실데이터 무관 -> 자율 가능]

## Phase A — 신 신호 source 매핑 (정밀 계약, F9 체인 대상)  [ingest 완료 2026-07-05 — testcontainer 검증]

ingest F9 체인 완료(전부 additive nullable, 옛 경로 무손상):
- 모델 4: server_metrics(host-wide 11) · server_disk_io(await 4) · server_net_io(drops 2) · server_mount_usage(inode 2). 총 19 컬럼.
- Alembic e6b8d0f2a4c7 (nullable add, hypertable 즉시). 검증: testcontainers alembic upgrade head + collect insert 18 통과.
- wire 스키마(MetricsInput·DiskIoInfo·NetIoInfo·MetricsMountInfo) optional · inbound DTO(defaulted) · consumer mapper · collect_repository INSERT · agent.md 계약.
- #B 대로 미저장: cpu_per_core[]·psi_*(agent 발행하나 extra=ignore drop). Windows disk await(saturation.disk_queue[] 시간필드)는 ETW 트랙 대기라 저장 미배선.
- 단위 637 + integration 18 통과, ruff clean, 회귀 0. 커밋 안 함.

남은(엔드포인트까지): Phase C 집계·seam — report_aggregate SQL 이 신 신호 집계(p95·await counter_agg·엔진 산출 steal/burst/trend/runway) -> ReportRowRaw -> build_resource_stats -> 신 ResourceStats. report_aggregate 는 옛 active 모델도 타는 critical 쿼리라 (additive 여도) counter_agg SQL 은 신중. 화면 이관(Phase D)은 CP3(사용자).


정밀 분석 결과 신규 컬럼은 최소다 — 신 ResourceStats 필드 다수가 기존 컬럼 또는 엔진 산출. 기존 sat_* 는 "agent 가 worst/rate 로 축약한 scalar float" 패턴이라 신규도 이를 따른다(per-disk/per-core 배열 불요 -> CP2 소멸).

신규 server_metrics scalar 컬럼 (agent worst/max 축약, sat_* 패턴):
- cpu_max_core_pct (float) — agent 가 per-core util% 중 max 발행. 엔진 p95 -> `cpu_percore_p95_max`.
- procs_blocked (float) — /proc/stat D-state 순간 카운트. 엔진 p95 -> `procs_blocked_p95`.
- disk_await_ms (float) — agent 가 worst-disk await(counter delta 산출) 발행. 엔진 p95 -> `disk_await_p95_ms`.

신규 mount 컬럼 (inode):
- inode_total / inode_free (statvfs f_files/f_ffree) -> `disk_inode_runway_days`(엔진 Theil-Sen).

신규 net_io 컬럼 (품질):
- tcp_retrans_segs/tcp_total_segs · rx_drops/tx_drops/rx_packets/tx_packets -> 엔진 % 산출 -> `net_retrans_pct`/`net_drop_pct`.

기존 컬럼/엔진 산출 (신규 agent 신호·컬럼 불요):
- `mem_swap_paging` <- sat_mem_paging_rate(기존, Linux=swap page-out rate) > 0 AND os != windows. build_resource_stats 산출.
- `mem_total_mb` <- mem_total_kb(기존)/1024. query 가 carry.
- `cpu_steal_p95_pct` <- cpu_steal(기존 jiffies) -> steal% p95. query 집계.
- `history_hours` <- 윈도우 표본 span. query 산출.
- `cpu_burst_ratio` <- cpu p95/median. query 에 median 추가.
- `util_trend_rising` <- cpu·mem util Theil-Sen slope > 0. service 산출.
- `disk_capacity_runway_days` <- worst mount used 시계열 Theil-Sen. service 산출(기존 mount 이력).

F9 체인 (신규 컬럼당): ORM + Alembic(+rate 는 counter_agg cagg #C5·ADR 0043) + MetricsInput/inbound DTO + consumer handler + collect_repository INSERT + agent.md 계약. + report_aggregate SQL 집계 + ReportRowRaw + build_resource_stats 배선.

의존·검증 관문: cpu_max_core_pct·procs_blocked·disk_await_ms·inode·net 품질은 agent 발행 필요(요청 문서 전달됨, 회신 대기). 나머지 7개는 엔진 단독 가능(agent 무관). Alembic 은 testcontainers(#C4)로 검증 후 apply — 자율로 apply 안 함(데이터정합성). 그래서 코드화는 (a) agent 계약 회신 + (b) testcontainer 가용 후.

자율 가능 하위집합(agent 무관, 신규 컬럼 0): 엔진 산출 7필드의 query/service/build 배선 — 단 report_aggregate SQL·ReportRowRaw 확장이라 Phase C 와 맞물림. 신 모델이 아직 active 경로가 아니라 배선해도 dead 이므로, 호출처 이관(CP1 후속)과 함께 진행이 정합적.

### agent 계약 확정 (회신 반영 — right-sizing-signals-agent-response.md)

agent 가 전 신호 구현·실측 검증 완료(Linux 전부, Windows per-core 제외 전부). 확정 wire 필드명·정정:
- CPU: `procs_running`·`procs_blocked`(/proc/stat) · `cpu_per_core[]`(코어별 user..steal raw 배열) · `schedstat_run_wait_ns`.
  - 정정: per-core 는 pre-reduced max 가 아니라 raw 배열. per-core util 은 델타 필요라 배열 저장이 real. 우선순위 낮음 + Windows 미구현이라 defer(도메인 None -> 단일스레드 판정 skip, graceful). 도입 시 JSONB(코어별 jiffies) + query per-core 델타.
- 메모리: `pswpin`·`pswpout`(/proc/vmstat) · `oom_kill`(4.13+) · (Win)`mem_pages_input`(Pages Input/sec, mmap 미혼입).
  - 정정: `mem_swap_paging` <- pswpout delta > 0 (Linux, 신규 raw). 기존 sat_mem_paging_rate 아님. Windows 는 mem_pages_input rate.
- 디스크 용량: `mounts[].inodes_total`·`inodes_free`.
- 디스크 I/O: `disk_io[].time_reading_ms`·`time_writing_ms`·`io_ticks_ms`·`weighted_io_ms` + reads/writes 완료수.
  - await = per-disk (time_reading_ms+time_writing_ms) 델타 / (reads+writes) 델타 -> worst disk. Windows = saturation.disk_queue[].(read_time+write_time)/(read_count+write_count). 엔진 산출(agent 는 raw만).
- 네트워크: `net_io[].rx_drops`·`tx_drops` · `tcp_retrans_segs` · `tcp_tw`(TIME_WAIT) · `conntrack_count`/`conntrack_max`(모듈 없으면 null).
- 관측용(분류 미사용): `psi_cpu/mem/io_some_total`.
- 스키마: 신규 필드 전부 optional(required 아님) — 엔진 inbound DTO 도 optional 수용(없으면 null).

계약 확정으로 Phase A 착수 관문 (1)agent 회신 해소. 남은 관문 (2)Alembic testcontainers 검증만 — 마이그레이션 작성 후 #C4 라운드트립으로 검증(Docker 필요).

## Phase C — 쿼리·집계

- `report_aggregate` SQL 에 신규 포화 컬럼 cagg 집계. `ReportRowRaw`·`build_resource_stats` 배선.
- 서버목록·도넛·attention 이 동일 `build_resource_stats` 소비(#E3 정합 — 화면 간 idle/shutdown/root 일치).

## Phase D — 서비스·표시

- mapper 권고(root-cause 반영)·attention 자원 부족 카드·confidence 마커.
- `cache_serializer._DETAIL_DISPLAY_FIELDS` 동기화(#F9).
- ViewModel·템플릿·외부 JS: 5자원 판정·신뢰도·root-cause 표시. `right_sizing_thresholds.html`·`_thresholds_reference.html` 신 임계·판정 순서.

## Phase E — 테스트·문서 (wrap-up)

- 단위·통합 테스트(recommendation·ingest·report_serializer 라운드트립·query dispatch).
- `docs/architecture/right-sizing.md` 신 모델로 재작성 — 구현 완료 후(코드가 진실, F12). CLAUDE.md #E3·#F9·#F10 갱신. tradeoffs T14.
- temp 초안 삭제: `right-sizing-principle.md` · `resource-models-summary.md` · 본 플랜(격상 완료 시).

## 순서·자율성

- 권장 자율 순서: B(도메인+단위테스트) -> A(스키마·마이그레이션) -> C -> D -> E.
- B 는 완전 자율(외부 의존 0, 합성 테스트 완결). A~D 는 에이전트 실데이터 없이도 스키마·로직·표시까지 구현 가능(실데이터 검증만 후속).

## 규율 (자율 실행 중 준수 — 위반 금지)

- feature 브랜치 위에서만. 진입 시 `git branch --show-current` 확인, main/master 직접이면 중단·보고.
- commit·PR 자동 금지 — 사용자 명시 전까지.
- pytest 자동 실행 금지 — 실행 명령에 명시 authorization 있을 때만(합성 단위 테스트 포함). 없으면 테스트 "작성"까지만, 실행은 체크포인트에서.
- F9 동시 갱신 체인 의무(DTO·mapper·cache_serializer·템플릿·JS). 자동화 변환 후 F5 자가검증 4항.
- 계층 원칙 P1~P4(#E1). 임계 상수는 도메인 모듈(`recommendation.py`) 단일 — web/consumer 는 import.
- fail-close/open·timeout·로깅 규약(#F6·F7) 유지.

## 체크포인트 (사용자 복귀 시 검토 — 여기서 정지·보고)

- CP1: Phase B 도메인 + 단위 테스트 완료 -> 분류 결과·호환 wrapper 존폐 검토.
- CP2: Phase A 마이그레이션 -> 스키마·per-core 저장 형식 검토.
- CP3: Phase D 표시 -> 화면 검토.
- CP4: 전체 완료 -> wrap-up(5 Stage) + commit·PR 결정.

## 명령 예시 (사용자가 자리 비우며 내릴 수 있는 형태)

- "Phase B 자율 구현하고 단위 테스트까지 작성·실행해. CP1 에서 멈춰." (pytest authorization 포함)
- "B -> A 순서로 가고 CP2 에서 멈춰. 커밋은 하지 마."
- "플랜 전체를 CP 마다 멈추며 진행해."
