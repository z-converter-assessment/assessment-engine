# wire v2 마이그레이션 — 세션 핸드오프

새 세션이 컨텍스트를 이어받기 위한 self-contained 문서. 이거 + 코드 + ADR 0053 + `docs/reference/right-sizing-thresholds.md`
만으로 이어갈 수 있게 작성. (temp 의 나머지 planning 문서는 본 핸드오프로 통합·삭제됨.)

전제: 에이전트가 v2 로 본격 배포되면 실 데이터가 흘러 남은 표시 계층을 의미검증(/verify)과 묶어 마무리한다. 지금은
그 배포 전 pause 지점.

정본 계약 = `docs/reference/contracts/agent-data.md` + `wire.schema.v2.json` + `v2-example-messages.json`(frozen).
브랜치 = `refactor/layer-audit`. 엔진 compose down. v2 agent 아직 없음(의미검증 대기).

## 무엇이 v2 (wire) 마이그레이션인가

wire 데이터 계약을 OpenTelemetry system.* 정렬(envelope + datapoint-array)로 전환. 단위 canonical(시간 s·크기 By),
device/iface/mount 안정키(device_id/iface_id/mountpoint), 신 신호(PSI·paging·inode·disk error·steal). 프로덕션
없어 clean-cut(구 스키마/데이터 폐기). 흐름 = ingest -> DB 저장 -> cagg 집계 -> 분류(recommendation) -> 표시.

## 완료·검증 (커밋됨 + 이번 WIP)

write 경로 (실 TimescaleDB 검증):
- consumer schemas/mappers/handlers v2 (datapoint-array 파싱·task_policy·hostname 제거·agent_id fallback).
- inbound DTO·collect_repository v2 (s/By·device_id/iface_id/mountpoint 자연키·member 정규화).
- DB 모델 v2 (server_metrics cpu_*_s/mem_*_bytes·server_disk_io device_id·server_net_io iface_id·server_filesystem
  개명·server_pressure/server_disk_error 신설·inventory block_devices/net_interfaces/lvm_vgs JSONB).
- Alembic 단일 baseline `53df4c2132fd`(v1 38 revision squash) + T3 cagg `a2f4c6e8d0b1`(5종). alembic check drift 0,
  라운드트립, counter_agg delta·util·await·used_pct 산출값 검증.
- 21 테스트(tests/unit/test_v2_wire_schemas·test_v2_mappers) 통과.

진단모델 (Gate0 = ADR 0053, 확정):
- 판정은 근거 있는 고전 신호로만. PSI 는 저장만·판정 Deferred(collect-now-classify-later).
- recommendation.py: 메모리 목표 80%·메모리 포화 dual-gate(util AND paging_major) 배선·검증. steal 인과분리·load 폐기 등.
- 임계·근거·처방 정본 = `docs/reference/right-sizing-thresholds.md`(영구).

async 정확성 (검증, 대조 포함):
- consumer SIGTERM graceful — `asyncio.run` 이 SIGTERM 미처리 -> `loop.add_signal_handler`(asyncio-native, signal.signal
  아님)로 stop_event set -> graceful unwind. (수정 전: SIGTERM 즉사 exit 143.)
- report_worker 루프 격리 — `_process_one` 의 finish_* DB 실패가 워커를 영구 죽이던 비대칭을 task_reaper 와 대칭으로.

db-query read 계층 v2 (import 클린, 런타임은 데이터 검증 대기):
- outbound DTO, query/types(dispatch·drop swap/load/disk.queue 차트), query/metric(dashboard·latest_saturation 단순화·
  metric_trend), query/server, query/report(report_aggregate v2 cagg->ResourceStats·disk await 를 op_time 로·mount->
  filesystem·mem_bytes). web import 복구됨.

## 남은 것 (에이전트 배포 후 실 데이터로 /verify 묶어서)

표시 계층 (web/services, ~130 v1 참조 / 12 파일 — import 는 통과, 런타임 v2 미완):
- `device_filters.py` + 소비 매퍼(server·report·attention·export·right_sizing_api): 단순 rename 아님. v2 스토리지 모델
  재해석 필요 (아래 미확정 결정).
- `metrics_calculator.py`: 실시간 대시보드 compute — MetricPairRaw/DiskIoRaw v2(cpu_*_s·mem_*_bytes·device_id·io_bytes) delta.
- `mappers/*`·`cache_serializer.py`·query wrapper(metric/environment/report)·template·static JS(drop 차트·v2 필드).

미확정 결정 (표시 계층 재작성 착수 전 확인 필요):
- v2 스토리지 표시 모델. v2 는 inventory `mounts`(폐기)·`kind`·major/minor 가 없다. 대신:
  block_devices(정적 트리, type=disk/part/lvm/swap + mountpoint + size_bytes) / server_filesystem(사용량 시계열, device_id
  조인) / lvm_vgs(확장여력). 제안 해석: disk 총량 = block_devices type=disk 합(Windows PhysicalDrive 도 block_device 라
  mounts fallback 불요). 스토리지 상세 "마운트별 표시" = block_devices(정적) + server_filesystem(사용량) 조인. find_parent_disk
  (major/minor) -> device_id -> block_device.id 조인. is_data_volume(kind) -> fstype 기반. 이 해석 확정 후 device_filters +
  매퍼 일괄.

물리/가상 필터 caveat: query/types.py `_PHYS_DISK/_PHYS_IFACE_SQL_FILTER` 현재 no-op(TRUE) — 전체 집계. 실 데이터로
agent 발행 granularity 확인(partition/lvm/bond-member 발행 시 collapse 이중집계). 필요 시 inventory(block_devices.type/
net_interfaces.kind)에서 물리 device_id/iface_id resolve 재사용 CTE 추가.

query 재설계(부분 적용, 남은 것): single/batch 중복(report_mount_usage/memory_breakdown/cpu_breakdown 단건)은 유지 중
— batch([id]) N=1 특수화라 인터페이스에서 제거 가능(선택). latest_saturation·metric_trend 는 v2 로 단순화 완료.

## doc-sync 대상 (v2 완료 후 wrap-up/ship 일괄)

F9: 문서 동기화는 wrap-up 일괄. 규율(사용자 강조): 과거잔재 확실 삭제(v1 토큰·회고형 서술 grep 0, F12) · 중복 확실 삭제
(같은 사실 1곳, docs/README 원칙2) · 현황 선언적 명시. 대상 라이브 문서(현재 v1 서술):
- docs/reference/db/models.md · timescaledb.md · repositories.md · dtos.md (v2 모델·cagg·자연키·DTO).
- docs/reference/right-sizing.md (Gate0 확정 반영, 임계 수치는 right-sizing-thresholds.md pointer).
- docs/reference/consumer.md (SIGTERM graceful·v2 handler).
- docs/reference/web/* (read 재배선·drop 차트·v2 필드).
- docs/guides/migrate.md (Alembic squash·v2 cagg).
- .claude/CLAUDE.md (#C 데이터 계층 v2 · #F11 consumer graceful 은 asyncio 자체 처리 아님->add_signal_handler · #E3/#F10 진단모델 v2).

## 다음 스텝 순서

1. v2 스토리지 표시 모델 확정 (위 미확정 결정).
2. device_filters + 매퍼 + metrics_calculator + query wrapper + template/JS v2 재배선 (import 검증).
3. 에이전트 v2 배포 후 실 데이터로 /verify (숫자·분류 의미 검증) — util%·await·runway·분류 정합.
4. doc-sync 패스 (위 규율).
5. /ship (ADR 0053 은 이미 승격됨 — 인덱스 등재 완료).

## 검증 재현 (throwaway DB)

마이그레이션·cagg 검증용:
```
docker run -d --name ae_v2_migtest -e POSTGRES_DB=assessment_v2 -e POSTGRES_USER=ae_mig \
  -e POSTGRES_PASSWORD=mig_pw_strong_123 -p 5433:5432 timescale/timescaledb-ha:pg16
# 대기 후: POSTGRES_HOST=localhost POSTGRES_PORT=5433 ... alembic upgrade head
```
timescaledb_toolkit(counter_agg)은 image 내장 — cagg 마이그레이션이 CREATE EXTENSION.
