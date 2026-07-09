# 표현/서비스/쿼리 레이어 감사 — 발견·리팩토링 계획

성격: 내부 리팩토링 추적 메모(삭제 자유). 엔진 코드 경로 명시. 판단 기준 = 정석·컨벤션 우세(문서 원칙보다).
4축 병렬 감사(레이어 정합·기준 모호·표현계층 성능·레포 인터페이스) 종합. 심각도·확신·수정 리스크 순.

## 배치 1 — 완료 (안전·격리)

- [x] B7 procs_blocked 매직넘버 -> `PROCS_BLOCKED_DSTATE_SATURATION=1.0` 상수화 (recommendation.py). 값 동일, 동작 불변.
- [x] B5 캐시/스냅샷 복원 `saturation_donuts` nested 누락 -> `report_serializer._overview_from_dict` 복원 추가.
- [x] C3-주석 recommendation.py "over=노랑" -> 실제 파랑(#1d4ed8)로 현황 정정(F12).
- 검증: compile·ruff·pytest 15(serializer+API)·API 인과·전 페이지 200.

## 배치 2 — 사용자 노출 결함 (완료)

- [x] A1 디스크 GB 단위 base drift — 메모리(1024)와 디스크(1000) 공유하던 `disksize_styled` 를 분리. 디스크용 `storagesize_styled`(1000, decimal) 신설 + 필터 등록, 디스크 필드 3곳(`_env_report_body`·`_environment_overview`·`single_report` 의 `total_disk_gb`/`disk_total_gb`) 교체. 메모리는 `disksize_styled`(1024) 유지.
- [x] A2 진단 텍스트 vs 배지 divergence — `_build_diagnosis` 비-under 꼬리(idle/여유)를 `host.host_status`(idle=활동3축·over=target<cores) 파생으로 단일화 (raw p95 고정선 제거). 오도 상수 `CPU/MEM_DOWNSIZE_P95_PCT` -> `BURST_PEAK_FLOOR_CPU/MEM_PCT` 재명명(실제 용도=burst peak 유의미 하한, 다운사이즈 판정 아님).
- [x] A3 디스크 "30일 임박" 이중 산식 통합 — `report_mount_worst`(14일창 worst-by-usage) 폐기. `mount_runway` CTE `t` 서브쿼리에 구동 마운트 이름(`disk_capacity_driving_mount`) 노출. 임박 표시(요약 불릿·capacity_imminent·right-sizing API `days_until_full`) 전부 분류 신호(구동 마운트·runway·`RS_DISK_RUNWAY_DAYS`)로 단일화. `worst_mount_used_pct` 는 most-full 이용률 KPI 로 유지(`mm.worst_used_pct` 단일). `_CAPACITY_IMMINENT_DAYS` 폐기.
- 검증: import·ruff·데이터클래스 배선·`_extract_capacity_imminent` 게이트 실동작 OK. API 예시/필드 문서(api_reference) 정정.
- wrap-up 동기화 대기(#F9): 단위테스트(`test_mappers_report._raw` worst_mount/worst_days 인자·filling 케이스, `test_mappers_environment_report`·`test_diagnostic_service` 의 `_CAPACITY_IMMINENT_DAYS` 참조) + 통합테스트(`test_query_repository_report` 의 `report_mount_worst` 3케이스 제거·구동 마운트 검증으로 대체).

## 배치 3 — #F9/#E3 체인 (레포 dataclass·성능, 신중)

- [x] A4 baseline 무명 6-tuple -> dataclass. `DiskIoBaselineRaw`/`NetIoBaselineRaw`(outbound.py) 신설, abstract·concrete 반환 + 언패킹 3사이트(_assemble_report_raws·get_inventory_export·`_base._inject_net_baseline`)를 필드명 대입으로. 순서 취약 제거. 런 검증: reports·right-sizing·assessment·realtime·export POST 전부 200.
  - 놓칠 뻔한 3번째 사이트 `_base._inject_net_baseline`(environment·server·assessment 공유) — dataclass 언패킹 불가라 안 고쳤으면 런타임 break. grep 전수로 포착.
- [x] A5 `latest_saturation` 무타입 dict -> `SaturationRaw`(기본 None 필드로 빈 sentinel). abstract·concrete + 소비 2사이트(get_latest_metric·_assemble_realtime) 속성 접근. 런 검증: latest 메트릭 await 396.9(Linux)·Windows 필드 null 정상, realtime 200.
- [x] B1 즉시분 — `build_action_targets`의 `classify_host` 별도 호출 제거, `to_capacity_warning_item(raw).classification` 재사용(요청당 rollup 2회 -> 1회, #E3 동일 산식 무영향). ruff·값 매칭 검증.
- B1 잔여 — `rollup_host`/`build_resource_stats` 가 여전히 report row·under·도넛·attention 경로에서 raw당 수회 산출. 요청 스코프 memo화(server_id -> HostAssessment 1회)로 통합 (별도, 신중 — 여러 매퍼 경유).
- [x] B2 환경 보고서 aggregate 2회 -> 1회. `get_attention_signals`에 optional `raws` 추가 — 보고서 경로(env·selection)가 이미 산출한 report_aggregate raws 전달. attention은 os_eol(os_id/version/kernel)·agent(public_id/hostname)만 읽어 창 독립이라 정합. 런 검증: raws 재사용 vs 자체 aggregate의 os_eol 34대·agent·gap 완전 일치.
- [x] B3 right-sizing 필터 pushdown. 전체 report_aggregate 후 Python 필터 -> 경량 inventory(get_servers)로 매칭 server_ids 확정 후 그 부분집합만 aggregate. ServerDetail(hostname/public_id/interfaces)이 raw와 동형이라 필터 의미 동일. ambiguous/unresolved는 전체 inventory 기준 유지. 런 검증: 무필터 69대(회귀0)·hostname 1대·pair 해석·bogus unresolved 정상.
- [x] B4 `_assemble_realtime` per-server latest_saturation 중복 제거. 벌크 sat_map을 `get_latest_metric(saturation=)`로 주입 -> 캐시 미스 시 per-server 재조회 생략(그 결과는 realtime이 쓰지도 않음). online 서버는 dense 표본이라 창 차이(ttl vs 10min) 무영향. 런 검증: realtime·개요·목록 200, 에러 0.
- [x] B8 `metric_trend` 캡슐화 — 서비스가 `_BUCKET_INFO[bucket]`로 bi/bucket_td 파생해 넘기던 걸(metric_chart는 이미 내부 파생, 불일치) `bucket: BucketSize`만 받고 repo 내부 파생으로 통일. abstract·concrete·서비스 2호출부. 서비스 계층 `_BUCKET_INFO` import 0(repo 전용).

## 배치 4 — 정리·설계 (SQL 무관 안전 정리분 완료)

- C1 (보류 — 제품 결정) 계산만 하고 미렌더: `attention_hosts`·`capacity_imminent`·`base.summary_bullets`(build_report_summary_bullets 출력, 템플릿은 summary_bullets_env 만) 3건 렌더 0 확인. 엔지니어 보고서에 렌더할지 삭제할지 사용자 결정 대기 — 임의 삭제 안 함.
- [x] C2 net 필드 `net_avg_kbps` -> `net_avg_kbytes_per_s` 재명명(실제 kB/s, 8배 오독 위험 제거). `IDLE_STRONG_NET_KBPS` -> `IDLE_STRONG_NET_KBYTES_PER_S`. recommendation.py 5곳 + report.py 배선 1곳. (DB 컬럼 `net_rx_kbps` 등 SQL 별칭은 이번 SQL-무관 스코프 밖 — 별건.)
- [x] C3-잔여 죽은 코드 제거: `_SWAP_DANGER_PCT`(+ setup.py 등록 + base.html data-attr, JS 미소비)·`workload_dist`(미렌더 막대)·`evaluated_count`+`insufficient_count`(미렌더). 스냅샷 호환 legacy-pop 추가.
- [x] C4 `base_diagnostic_repository` 계층 역전 해소 (예상보다 큰 문제였음): (a) `CLASSIFICATION_LABEL_KR` = 사용처 0 죽은 코드 -> 삭제(실제 분류 라벨은 recommendation.LABEL_KO). (b) `DiagnosticTimeRange` = `query/types.TimeRange` 와 완전 동일 Literal -> #F10 단일 진실 위반. `TimeRange` 로 일원화(어노테이션 전 교체). (c) `DIAGNOSTIC_RANGE_DAYS`(TIME_RANGE_TD 파생) + `DIAGNOSTIC_DEFAULT_TIME_RANGE` -> `query/types.py` 이동. repo 인터페이스 계층에 표시/윈도우 상수 0. 8 importer(routers 3·services 2·setup·report_generator + shared 주석) 갱신. ruff·import·파생값 검증.
- C5 collect God interface(inventory+task+metric)·mixin 다중상속 session 공유·`metric_snapshots`가 MetricSeries에 value/dim=None(거짓 DTO) — 설계 정합. (미착수 — 설계.)
- C6 목록 서버사이드 pagination(service/os_distro/search SQL pushdown) — E2 정책 실현. (SQL — option 2 스코프 밖.)
- C7 배지 위험선(90) vs 분류 under선(CPU 70) 비대칭 UI 노출 — 서버 상세 시점 배지에 분류선 병기. (미착수 — UI 제품 판단.)
- C8 swapless Linux 메모리 포화 사각 — measured=True 오도. PSI 대체 신호는 통합 데이터모델(`unified-resource-data-model.md` 2.6절 PSI)로 근본 해소 예정 -> 데이터모델 확정 후 처리.
- [x] C9 CPU 포화 Linux 1.0 vs Windows 2.0 비대칭 — 재검토 결과 오류 아님(모집단 다름: Linux procs_running=실행 포함 R-state / Windows queue=대기만). 주석에 근거 명시. reference/right-sizing.md 근거 동기화는 wrap-up.
- [x] C10 `_UTIL_DONUT_CIRC=263.89` 하드코딩 -> `_DONUT_RADIUS=42`에서 `2*math.pi*r` 유도. 주석 `≈` 비키보드 유니코드 2곳 정리(+shared.py `수요≈0`).

## 런 검증 결과 (dev compose, 실데이터 69서버·메트릭 최신)

- A3 정합 확인 — right-sizing API disk.capacity 에서 status·worst_mount(구동 마운트)·days_until_full 이 같은 마운트로 정합. 경계 정확: days 0/3 -> filling, 36/39 -> capacity_ok (RS_DISK_RUNWAY_DAYS=30). Windows C:\ 도 정상. divergence 제거 실증.
- A1 확인 — 디스크 총량 "75 GB"(storagesize_styled 정수·1000 base) 렌더. 메모리 "5.9 GB"(disksize_styled) 와 분리 정상.
- C10 확인 — 환경 개요 도넛 mapper 파생 dash_length(8.21/51.43/187.40 등 비례값) 렌더.
- B8 버그 발견·수정 — `metric_chart`(repo 내부)가 `metric_trend` 를 옛 시그니처(bi,bucket_td)로 위임해 서버 상세 차트 전부 500. 놓친 내부 호출부. `bucket` 직접 전달로 수정 -> 전 차트 타입(cpu/mem/swap/disk/net/fs/load) 200 복구. (런 검증이 아니었으면 shipped broken.)
- C2/C3/C4 간접 확인 — 앱 기동 import 에러 0 + right-sizing API(net_avg_kbytes_per_s)·환경 보고서(C3 제거)·라우터(C4 TimeRange 통합) 전부 200.
- 발견(C1 계열): `build_report_summary_bullets` 출력(base.summary_bullets)도 미렌더 — 템플릿은 summary_bullets_env 만 표시. A3 에서 고친 "디스크 채움 임박" 불릿 + capacity_imminent 는 스냅샷 직렬화만·화면 미노출(렌더되면 정합, 회귀 아님). C1 에 base.summary_bullets 추가.

## wrap-up 테스트 동기화 대기 (배치 2-4 누적, #F9)

- 배치 2: `test_mappers_report._raw`(worst_mount/worst_days 인자·filling 케이스)·`test_mappers_environment_report`·`test_diagnostic_service`(`_CAPACITY_IMMINENT_DAYS`)·통합 `test_query_repository_report`(`report_mount_worst` 3케이스 -> 구동 마운트 검증).
- 배치 4: `test_right_sizing_model`·`test_mappers_report`의 `net_avg_kbps=` -> `net_avg_kbytes_per_s=`.
- B8: 통합 `test_query_repository`의 `metric_trend(..., bi, td, ...)` 호출 -> `metric_trend(..., bucket, ...)` (bucket 문자열 전달).
- C4: 테스트가 `base_diagnostic_repository`에서 상수 import 하면 `query.types`로 (현재 참조 0 확인됨).
- A4: 통합 `test_query_repository_report`의 baseline tuple 인덱스 접근(`io_map[sid][0]` 등) -> dataclass 속성(`.iops_baseline` 등). A5: `latest_saturation` dict 키 검사 있으면 속성으로.

## 참고 — 감사에서 "결함 아님"으로 확인된 것

- 분류(rollup_host) 자체는 단일 소스 — 서버목록·도넛·보고서·attention·API 전부 build_resource_stats->rollup_host 경유, drift 없음. (문제는 분류가 아니라 진단 텍스트 A2·이중 산식 A3.)
- SQL 안전성(text()+bound param, partition pruning 술어) 정공대로 준수.
- AUTO_BUCKET backend/frontend 값 일치. 네트워크/처리량 byte 변환 양측 1024 일관(1000 base는 디스크 storagesize A1 하나뿐).
- 캐시 역직렬화는 saturation_donuts(B5) 외 전 필드 복원 정상.
