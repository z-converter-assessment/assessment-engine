# 핸드오프 — 프론트엔드 리뉴얼 이어가기 (2026-07-12)

성격: 내부 세션 인수인계 (외부 공유 아님). docs/temp 의 self-contained 규약은 외부 공유 문서 대상이고, 본 문서는
우리 다음 세션이 repo 를 그대로 들고 이어받는 용도라 코드 경로·CLAUDE.md 절 참조를 의도적으로 포함한다. 채택된
결정은 wrap-up 때 영구 문서로 격상 후 본 파일 삭제.

브랜치: `feature/engine-hardening`. 작업 트리에 대량 uncommitted 변경 누적 (아직 커밋 안 함 — 사용자 요청 시에만).

---

## 0. 지금 바로 이어서 할 일 (in-progress)

서버 세부(상세) 페이지 리뉴얼 진행 중. 두 갈래가 남음:

1. 서버 세부 개요(`servers/detail.html`) 요약화 — 사용자 확정("요약형으로"). 현재 개요가 각 하위탭(cpu/memory/
   storage/network) 최상단 "현재 상태" 스냅샷 블록과 중복(자원별 상세 테이블·per-device 디스크 I/O·per-interface
   네트워크·breakdown). 개요를 요약으로: 도넛 3개 + 자원별 포화 상태 한 줄(실행 큐·페이징·await·재전송, 분류 축 정합)
   + 에러 fleet + 상세 링크만. 원시 breakdown·per-device/interface 테이블은 하위탭으로(이미 있음). 미구현.

2. 스토리지 레이아웃 트리 뷰 신규 — 사용자 확정(방향 승인). 설계는 아래 3절. 미구현.

순서: (스토리지 원칙 완료) -> 레이아웃 트리 매퍼 조립 -> 스토리지 상세탭 렌더 -> 개요 요약화. 사용자가 "구도/배치를
스크린샷으로 직접 확인하며 반영"하라 했음 -> 매 변경 후 `node scripts/screenshot.mjs <out> --settle N <url>` 로 확인.

---

## 1. 스토리지 측정 원칙 (확정·명문화 완료)

사용자 핵심 요구: "무엇을 보더라도 원칙이 존재해야 한다". Windows·Linux 통틀어 무엇을 어느 계층에서 재는지 단일 규칙.
`src/assessment_engine/web/services/device_filters.py` 모듈 docstring 에 명문화 완료. 요지:

- 배정 용량 / 레이아웃 루트 = 물리 디스크 (block_devices `type=="disk"`). Linux vda / Windows PhysicalDrive0.
  디바이스 특성(rotational=HDD/SSD·sector_size·serial)도 이 계층.
- 파일시스템 용량 = 마운트된 데이터 볼륨 (mountpoint 有 + is_data_volume). Linux part/LV / Windows volume.
- 사용량 = 파일시스템(마운트) 계층 (server_filesystem, df/Get-Volume) — 2축: bytes(used/free) + inode(inodes_used/free).
  물리 디스크가 분모 아님 (fullness 는 파일시스템 속성, raw 디스크는 채우는 대상 아님).
- I/O (IOPS·처리량·await 포화) = 물리 디스크 (server_disk_io `type=="disk"`). LV/파티션 통과분 이중집계 회피.
- 확장 여력 = (a) lvm_vgs.free_bytes(VG 미할당, LV 확장 정밀치) + (b) 물리 디스크 미파티션 갭(배정−파티션 합).

사용자가 "기존 쿼리 노출분에 갇히지 말고 DB 원본 필드 전체를 종합하라" 지시 -> block_devices 노드에 있는 전체 필드
확인함(현재 화면 미노출 다수):
- lvm_vgs.free_bytes = 정밀 확장 여력 (지금 "배정−파일시스템" 추정보다 정확)
- server_filesystem.inodes_used/inodes_free = inode 고갈 축 (bytes 여유해도 쓰기 실패)
- block_devices.rotational = HDD/SSD
- lvm_vg·lvm_lv·lvm_segtype·lvm_stripes·raid_level·raid_metadata·crypt_type·partition_table·part_type·part_flags·
  part_start_bytes·mount_options·fs_label·fs_uuid·serial·block_size·sector_size — 논리 계층·파티션·식별 메타

전 서버 block_device type 분포: part 218 · disk 73 · lvm 39 · swap 36 · volume 25 · raid 2 · crypt 1.
복잡 스택 캡처 확인됨 — 서버 `raidlvmluks` 가 disk->raid->lvm->crypt->fs 풀스택. 임의 깊이 트리 표현 가능.
정직한 한계: 다중 부모(RAID span 여러 디스크, striped VG 여러 PV)는 DAG 라 순수 트리 불가 -> 디스크별 그룹으로 노출.

---

## 2. 스토리지 계층 실측 (OS별 차이 — 트리 조립 시 필수 이해)

```
Linux (plain)              Linux LVM                    Windows
disk vda (30GiB)           disk vdb                     disk PhysicalDrive0 (40GB)
 part vda2 vfat /boot/efi    part vdb1 (PV)               part PhysicalDrive0-part1..4 (fstype 없음)
 part vda4 xfs  /            lvm  rhel-root xfs /         volume C: ntfs C: (39GB)  <- fs 계층은 volume
 swap vda3 swap [SWAP]     lvm_vgs: rhel free_bytes      swap pagefile.sys
```

- Linux: fs 계층이 part 또는 lvm 노드(fstype+mountpoint 가 그 노드에).
- Windows: fs 계층이 별도 volume 노드(drive letter). part 노드는 fstype 없음. 미마운트 volume(\\?\Volume{})은
  mountpoint 없어 데이터 볼륨서 자동 제외. 같은 저장소가 part(raw)+volume(fs) 병렬 표현이라 미할당은 disk−파티션,
  fs 용량은 volume 로 계층 안 섞어야 이중집계 없음.
- 사용량 소스 server_filesystem 은 mountpoint 별 used/free/inode — Linux df, Windows Get-Volume 통일.

---

## 3. 레이아웃 트리 뷰 설계 (미구현, 승인됨)

현재 스토리지 표현은 flat 파일시스템 표(허술). 계층 트리로 교체. 데이터는 DB 에 다 있으니 매퍼가 block_devices 그래프
+ lvm_vgs + server_filesystem(inode 포함) 를 트리 ViewModel 로 조립.

```
vda   30GB  SSD  GPT                         <- 물리 디스크: rotational→SSD/HDD · partition_table
├─ vda2  200MB  vfat  /boot/efi
├─ vda4  28.8GB xfs   /     [용량 6% / inode 1%]   <- 마운트 fs: bytes+inode 2축 바
└─ 미할당 1.2GB

vdb  40GB  HDD
└─ vdb1 (PV → VG rhel)
   └─ rhel-root  LV(linear)  37GB  xfs  /  [용량 43% / inode 2%]
   VG rhel  여유 0GB                          <- lvm_vgs.free_bytes = 확장 여력
```

- 각 계층 노드에 그 계층 속성만: 디스크=HDD/SSD·partition_table, 파티션=fstype·mount, fs=2축 사용량 바(bytes+inode),
  LV=segtype·소속 VG, VG=free_bytes, RAID=raid_level, crypt=LUKS 표식.
- 스토리지 상세탭(`storage.html`) 현재 flat "현재 상태" 표를 이 트리로 대체. 개요엔 압축본(디스크별 요약 + 상세 링크).
- 사용자가 "빼거나 더할 축 있으면" 물었을 때 응답 대기 중이었음 — 없으면 매퍼->렌더->개요 순 진행.

---

## 4. 이번 세션 완료 변경 (검증됨, uncommitted)

### 서버 목록 (`list-table.js`, mappers/server.py)
- 뒤로가기 선택 desync/버튼 먹통 수정: 브라우저 history 복원이 체크박스만 되살리고 change 이벤트 없어 버튼/카운트
  desync. `pageshow` 에서 `resetSelectionOnShow()` — 선택 초기화 + 모달 닫기 + 발행버튼 재활성 + refreshInstallButton.
  결정: 뒤로 오면 항상 깨끗한 목록(Gmail/GitHub 관습). playwright 6 시나리오로 검증.
- 메모리 사양 소수 2자리(`_mem_spec`). 디스크는 정수 유지(사용자가 소수 취소).

### 실시간 현황 (`_environment_realtime.html`, mappers/attention.py, query/environment.py, list_page.py)
- 운영 신호 카드 제거(`_operational_signals.html` 삭제 + 라우터 attention 주입·컨텍스트 제거). 서비스
  `get_attention_signals` 는 report 가 쓰므로 유지. `get_selection_attention` 은 orphan 됨(wrap-up 제거 검토).
- 디스크 용량 제거(도넛 7->6). 현재 부하 상위 탑3->탑5, 6축 3열2행. 도넛 6개와 동일 신호 매핑.
  snapshot 에 paging_rate·net_kbps 추가, disk_pct·fs_used/total 제거.
- "온라인 N·오프라인 N·표본" 설명 문구 제거.

### 환경 성능 추이 (`environment-metrics.js`, environment_metrics.html, types.py, db/query/metric.py)
- 재구성: CPU(사용률·분류·압박PSI) / 메모리(사용률·압박PSI) / 스토리지(사용량TB·IOPS·처리량·await포화) /
  네트워크(I/O·재전송율). 제거: 실행큐·메모리구성·파일시스템%. 추가: cpu.psi·mem.psi·fs.used_bytes.
- EnvironmentMetricType Literal 재정의(신규 3 추가, run_queue·mem.available/cached/buffers·fs.usage_percent 제거).
  fs.usage_percent 는 report 가 metric_trend 직접 호출로 계속 씀(Literal 무관).
- 엄밀성 버그 2개 발견·수정 (사용자 "값이 맥락에 맞게 엄밀하게 연산됐나" 요청):
  1. fs.used_bytes: 수집 staggered(collected_at 당 1-3서버)라 순간 SUM 이 undercount(~4.67GB vs 진짜 202GB).
     server+mount 별 bucket 내 last() 후 SUM(전 함대)으로 수정. 검증 201GB.
  2. `_RATE_PER_DIM` collapse=True(환경 합산): 같은 staggered 로 "합산" 아닌 "서버당 평균"(총량/N)을 그려서 disk/net
     rate 수십배 undercount. server+device 별 bucket avg -> 전 함대 SUM 으로 수정. net 3.7->143.5 kB/s, disk 5->212
     MB/s 로 실측 일치. 서버 상세(collapse=False per-device)·보고서는 경로 달라 무영향 확인.
  - cpu.psi 검증(API 11.2% vs 수동 11.7%), disk await(1000-6000ms 는 device io_time util 94-97% 완전 포화라 실측
    정상), cpu/mem 사용률(비율이라 staggered 무관).
- chart-desc 전부 간결화(중복·편집자적 서술 제거, "Windows 미지원"·"20ms 포화"·"1% 영향" 등 caveat 유지).
- 보고서 `_metric_definitions.html` 디스크 정의 "게스트 블록 디바이스(virtio)" -> "물리 디스크" 통일.

### 환경 자원 평가 (assessment.html, mappers/attention.py, mappers/shared.py, recommendation.py, _resource_tables.html)
- 서버별 자원 적정성 표: 네트워크 3칼럼(재전송·드롭·상태) -> 상태 1칼럼(host under/over 분류 축 아닌 부가신호).
  6 핵심 축(CPU 이용률·포화 / 메모리 이용률·포화 / 디스크 용량·I/O)은 유지. 설명 트리밍.
- advisory 색 구분: `_metric(advisory=True)` 로 디스크 I/O·네트워크(판정 구동 아닌 참고 신호)를 주황(#d97706),
  CPU·메모리·디스크 용량 판정 위반은 빨강(#dc2626). `_METRIC_ADVISORY_COLOR` 신설. 범례 "빨강=판정 기여 / 주황=참고
  신호". 사용자 질문(disk I/O red 인데 근본원인 없음)에서 파생 — io_bound 은 biased/표시만이라 판정 미구동.
- "창 대비 관측 부족" 신뢰도 노트: `HostAssessment.sample_sufficiency` 필드 신설 -> rollup_host 가 채움 ->
  build_host_confidence_notes 가 sample_sufficiency < RS_DOWNSIZE_MIN_SUFFICIENCY(0.7) 면 노트. 30h 절대바닥(표본
  부족)과 별개 축. 호출부 4곳(assessment·report·right-sizing·attention) 시그니처 변경 0 -> assessment API
  `data_quality.notes` + right-sizing API 에도 자동 반영(단일 소스, 계약 의도 "짧으면 하향"과 정합). 지금 데이터 44h
  라 전 서버 발화.
- 윈도우 선택기 15분~7일 제거 -> 14일/30일만(분류 14일 표준 이상만).
- Q3 감사 결론: 앵커/윈도우가 report_aggregate·environment_utilization·net_baseline 동일 적용(정합), runway 만
  full-history(F10 예외), 계산은 공용 report_aggregate 엔진 재사용(새 연산 없음).

### 스토리지 측정 원칙 (device_filters.py) — 1절.

---

## 5. Wrap-up 정합 항목 (커밋 시 — `docs/guides/wrap-up.md` #F9)

- 테스트 신규/갱신: build_environment_realtime(부하상위 6축·도넛6), fs.used_bytes·`_RATE_PER_DIM` collapse=True 합산
  정확성, cpu.psi/mem.psi env 노출, `_ALL_METRIC_TYPES` dispatch, sample_sufficiency 노트, advisory 색,
  HostAssessment.sample_sufficiency 필드, 네트워크 1칼럼 metric_labels.
- 문서: `docs/reference/web/services.md`(env 차트 신구성·자원평가 표 칼럼·advisory 색), EnvironmentMetricType 카탈로그,
  `contracts/assessment-api.md`(data_quality.notes "창 대비 관측 부족" 값 + 커버리지 하향), storage 원칙을 CLAUDE.md
  #C 또는 storage 레퍼런스에서 device_filters 참조 선언, `_thresholds_reference.html` virtio 서술은 의도적 유지.
- 코드 정리: `get_selection_attention` orphan(실시간 운영신호 제거로 호출처 0) 제거 검토.

## 6. 검증·운영 메모

- web 은 템플릿 자동 리로드 안 됨 -> `.html`/CSS/py 변경 후 `docker compose restart web` (약 4초 대기).
- 스크린샷: `node scripts/screenshot.mjs <outDir> --settle <ms> [--vw 1600] <url>`. playwright 는 프로젝트 node_modules.
- DB: `docker compose exec -T postgres psql -U assessment -d assessment`.
- ruff: `uv run ruff check <files>`. JS 타입: `./node_modules/.bin/tsc --noEmit`.
- 표준 서버 public_id 예: alma8bios=740c0631-c4fb-49d4-9323-c80285888022 (Linux plain), win2019 계열(Windows),
  centos8suefilvm/raidlvmluks(복잡 LVM/RAID/crypt).
- 데이터 span 현재 ~1일 20시간(약 44h) — 14일 창의 13%. "창 대비 관측 부족" 정상 발화 상태(데이터 쌓이면 해소).

## 7. 지속 제약 (사용자·프로젝트)

- 한국어 반말. 굵게(`**`) 금지. 키보드 직타 문자만(이모지·비ASCII 기호 금지). 커밋·PR 은 명시 요청 시만.
- AI 메타데이터(Co-Authored-By 등) 산출물에 금지. 메모리 기능 사용 안 함(git 추적 문서로만 컨텍스트 관리).
- 표시 계층 P1-P4: repo=raw / service=파생 단일 / 템플릿=순수 렌더 / 차트 JS=P3 예외.
- 단위 실무정석: 메모리·디스크 = binary(2^30) 값 + GB/TB 라벨. bytes_to_gb 도 binary.
