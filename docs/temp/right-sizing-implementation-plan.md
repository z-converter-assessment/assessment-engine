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
- per-core(cpu_percore_p95_max)는 defer — 도메인은 스칼라 최댓값만 소비하나 agent 가 raw 배열 발행 + per-core util 델타 필요라 미저장. 도메인 None graceful (아래 field-source 매핑).

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


### field-source 매핑 (Phase C 배선 참조 — 현황)

신 ResourceStats 필드가 어디서 오나. 커밋된 신 컬럼(migration e6b8d0f2a4c7)은 전부 raw 카운터/gauge — 엔진이 델타·비율·p95를 산출한다(agent 는 raw만 발행). 필드명은 agent 실코드(assessment-agent-temp/src/collect.c·windows-agent)와 일치 확인됨.

Phase C 에서 집계할 것 (신 raw 컬럼 -> ResourceStats):
- `procs_blocked_p95` <- server_metrics.procs_blocked (gauge, p95)
- `disk_await_p95_ms` <- server_disk_io (time_reading_ms+time_writing_ms) 델타 / (reads_completed+writes_completed) 델타, per-disk -> worst -> p95 (counter_agg). Windows await 는 saturation.disk_queue[] 시간필드인데 ETW 트랙·미저장이라 별도
- `mem_swap_paging` <- server_metrics.pswpout 델타 > 0 (Linux). Windows 는 mem_pages_input rate
- `net_retrans_pct` <- server_metrics.tcp_retrans_segs 델타 / 세그먼트 (counter_agg)
- `net_drop_pct` <- server_net_io.rx_drops/tx_drops 델타 / packets 델타 (counter_agg)
- `disk_inode_runway_days` <- server_mount_usage.inodes_free 추세 (Theil-Sen, 엔진)

기존 컬럼/엔진 산출 (신 컬럼 불요, agent 무관):
- `cpu_steal_p95_pct` <- server_metrics.cpu_steal(기존) -> steal% p95
- `mem_total_mb` <- server_metrics.mem_total_kb(기존)/1024
- `history_hours`(윈도우 표본 span) · `cpu_burst_ratio`(cpu p95/median) · `util_trend_rising`(cpu·mem util Theil-Sen slope > 0) · `disk_capacity_runway_days`(worst mount used 추세) — 전부 query·service 산출

미배선 (defer):
- `cpu_percore_p95_max` <- agent 는 `cpu_per_core[]` raw 배열 발행하나 미저장 — per-core util 은 델타 필요라 배열 저장(JSONB)이 필요, Windows 미구현. 도메인은 None 이면 단일스레드 판정 skip(graceful)
- Windows disk await — 구세대 viostor 5대 IOCTL 미부착, ETW 트랙

Phase C 배선 관문: report_aggregate 가 server_metrics_5m·server_disk_io_5m·server_net_io_5m continuous aggregate(cagg)를 탄다. 신 raw 컬럼이 그 cagg 에 없어서 counter_agg 로 집계하려면 cagg 재생성 마이그레이션이 필요하다(#C4·#C5) + testcontainer 검증. 되돌리기 어렵고 intricate — 사용자 검토 하에 진행.

## Phase C — 쿼리·집계

- `report_aggregate` SQL 에 신규 포화 컬럼 cagg 집계. `ReportRowRaw`·`build_resource_stats` 배선.
- 서버목록·도넛·attention 이 동일 `build_resource_stats` 소비(#E3 정합 — 화면 간 idle/shutdown/root 일치).

## Phase D — 서비스·표시

- mapper 권고(root-cause 반영)·attention 자원 부족 카드·confidence 마커.
- `cache_serializer._DETAIL_DISPLAY_FIELDS` 동기화(#F9).
- ViewModel·템플릿·외부 JS: 5자원 판정·신뢰도·root-cause 표시. `right_sizing_thresholds.html`·`_thresholds_reference.html` 신 임계·판정 순서.

## Phase E — 테스트·문서 (wrap-up)

- 단위·통합 테스트(recommendation·ingest·report_serializer 라운드트립·query dispatch).
- `docs/reference/right-sizing.md` 신 모델로 재작성 — 구현 완료 후(코드가 진실, F12). CLAUDE.md #E3·#F9·#F10 갱신. tradeoffs T14.
- temp 초안 삭제: `right-sizing-principle.md` · `right-sizing-handoff.md` · 본 플랜(격상 완료 시).

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
