# payload cutover 엔진 작업 상태

에이전트 payload breaking cutover(전 에이전트 교체 + DB 초기화 전제)의 엔진 측 구현 상태. 계약 단일 진실은 `docs/architecture/agent.md`(엔진) + agent repo `docs/payload-contract.md`(에이전트). 본 문서는 진행 상태만 담으며 마무리 후 삭제한다.

## 구현 완료

- 계약 전환: interfaces 구조화(ip_internal 대체) · device kind · services pid join · task.result os · task.install 발행 publish-then-commit.
- device kind 필터 전환: `device_filters` 정규식 catalog 삭제 -> `kind` 판정. `query/types.py` SQL·cagg 를 `kind='physical'`/`kind='data'` 로. 메트릭 DTO(`DiskIoRaw`/`NetIoRaw`/`MountUsageRaw`/`MetricSeries`)·query·호출처(`metrics_calculator`/`query_service`/`mappers`) kind 전달.
- saturation: `SaturationInfo{disk_queue 디스크별 배열, cpu_run_queue, mem_paging_rate}`. consumer 가 `disk_queue` 를 per-device max 로 축약해 `server_metrics.sat_disk_queue` 저장. `recommendation.disk_io_saturated` os-aware(Linux iowait / Windows disk_queue>=2) — Windows 디스크 saturation 축 소비. Windows `cpu_iowait` 더미 0 무시.
- gateway: `InterfaceInfo.gateway`. `topology` 가 같은 서브넷·다른 gateway 를 분리(중복 사설 대역 disambiguation), null 안전(단일 gateway 서브넷 합류·모호 서브넷 제외).
- collection_interval_sec: 필드 수용. sufficiency 는 5분 버킷(288/day) 기반이라 주기<=5분이면 무관 — 필드는 저장, 느린 주기 정밀화는 미배선.
- `agent.md` 현행화(계약 흡수) + 교환 문서(요청/회신/결정) 삭제.

## 마이그레이션 체인 (head `d7f9b1c3e5a2`)

`d1f3b5a7c2e4` -> `f2a4c6e8b1d3`(interfaces) -> `a1b3c5d7e9f2`(시계열 kind) -> `b4d6f8a0c2e3`(cagg kind 필터) -> `c5e7a9b1d3f4`(server_metrics saturation 컬럼) -> `d7f9b1c3e5a2`(server_metrics_5m cagg disk_queue).

## 검증

전체 `ruff check src/ migrations/` clean. 즉석 실행 확인(임시 스크립트)으로 kind 필터·disk_queue max 축약·gateway 분리·assess os-aware 동작 확인. 정식 pytest 미작성(#F9 wrap-up 시점).

## 남은 작업

- item 4 (식별·라우팅 agent_id 전환): 에이전트 완료(worker 큐 `agent.tasks.{agent_id}`), 엔진 미완(현재 composite_id). 미완 시 task.install 배달 어긋남(수집은 정상). 엔진 짝 = consumer 식별·`task_service` 큐/라우팅·DB UNIQUE 를 agent_id 로 + ADR(0027/0044 supersede) + `_relink_rebooted_host` 제거.
- item 2 값: 에이전트 `cpu_run_queue`/`mem_paging_rate` null(perflib 실기 검증 대기). 값 발행되면 엔진 cpu_saturation/mem_saturation 축 소비 추가 + 임계.
- wrap-up(#F9): 테스트 작성, cutover ADR 신설, CLAUDE.md #B·#C·#E·#F 관련 동기화, 폐기 토큰(`ip_internal` 등 코드·문서) `rg` 0 검증, `right-sizing.md`(disk_queue 임계 `DISK_QUEUE_PER_DISK_SATURATION`) 갱신.
