# wire v2 계약 — 락 회신 (에이전트 -> 엔진)

> 성격: 협의 회신. `v2-signoff-reply.md`(엔진 조건부 sign-off)의 락 조건 2건 확정 + 추가 개선 1건.
> 갱신 산출물: `wire.schema.v2.json`, `v2-example-messages.json`(6종 전부 스키마 검증 통과).

## 0. 결론 — 락 조건 2건 해소 + 네트워크 키 통일. 계약 확정 요청.

엔진 2절 2건 반영 완료, 추가로 device 키 일관성 개선(네트워크도 안정 id). 이대로 락 요청.

## 1. 락 조건 2.1 — task.result / error body (해소)

v2 스키마에 task.result/error 를 명시했다. "v2 envelope 아래 v1 body 그대로 + schema_version" 원칙, system.* 재설계 대상 아님을 스키마 $comment 에 박음.

- task.result: v1 필드 전부 유지 + `schema_version:"2.0"` + `task_policy(bool|null, exit_code 보다 우선)` 신규. `exit_code`/`signal_no`(int|null 상호배타, Windows signal_no 항상 null). `boot_time`/`agent_started_at` nullable(워커 컨텍스트, 항상 null 허용). task_id 매칭(composite_id 불요).
- error: v1 필드 유지 + `schema_version`. `failed_component`(자유 문자열, minLength 1) 포함. retry_count/first_failed_at/recovered_at 옵셔널.
- 예시 `task_result`/`error` 2종 추가, 스키마 검증 통과.

## 2. 락 조건 2.2 — device 자연키 (해소 + 네트워크 통일)

엔진 우려(metric device id non-null 보장 / 네트워크 name 안정성)를 실측 기반으로 해소.

디스크 (non-null 보장):
- 폴백 티어에 `by-path`(PCI) 추가: dm/uuid -> partuuid -> wwid -> serial -> by-id -> by-path -> name.
- 실측: serial 없는 virtio-blk(예 centos7 vda)도 `/dev/disk/by-path/pci-0000:00:05.0` 항상 존재 -> metric device id non-null 보장. name 은 최후 폴백일 뿐 metric-bearing 디스크엔 by-path 로 안정 id 확보.

네트워크 (name -> 안정 id 로 통일, 엔진 질문에 대한 역제안):
- 엔진 2.2 는 "네트워크 키 name 유지, Windows name 안정하냐" 물었으나, 실측상 Windows NIC name 은 불안정(`tape7c53f38-86` — friendly name 아님). 그래서 name 유지 대신 디스크와 동일 패턴으로 통일:
- 네트워크 device 키 = MAC(OpenStack Neutron 포트 MAC, 인스턴스 수명 안정, 양 OS 읽힘). id_type=mac. 폴백 Windows `ifguid` / Linux `by-path`. name 은 표시용(inventory `net_interfaces[].name`)으로 강등.
- 실측: Linux ens3 MAC=fa:16:3e:4c:3b:62, Windows GUID={C28F...}+MAC=FA:16:3E:FA:9B:A7.
- 결과: 전 device(디스크/네트워크)가 id+id_type+name 한 패턴 -> 시계열 자연키(server_id, device=id, collected_at) 전부 안정. Windows name 불안정 우려 원천 해소.
- inventory 에 `net_interfaces[]`(name, id=mac, id_type, speed_mbps) 추가. metric device attr = id(mac).

## 3. 엔진 3절 추가요청 (수용)

- 3.1 PSI 소스: `pressure.stall.time`(counter, 시간적분)을 14일 saturation canonical 로. `pressure.stall.ratio`(gauge)도 발행하니 계약 변경 없음, 소비 합의 수용.
- 3.2 per-cpu 스케일: cpu attr 로 전체 발행(hottest-core p95용). 현 VM 규모 상한/다운샘플 불요. 대형 VM 대비 상한 정책은 구현 시 옵션으로 검토.
- 3.3 memory 이용률 분모: `memory.usage` state=available 상시 발행 확정(1 - available/limit). available 미발행 커널(centos6 2.6.32 등)만 MemFree+Buffers+Cached 계산 폴백.

## 4. 확정 상태

- 6종 예시(linux/windows metrics·inventory + task.result + error) 전부 wire.schema.v2.json 검증 통과.
- 미결 없음. 이 회신으로 락 요청.

## 5. 락 이후 흐름 (합의대로)

- 에이전트: 양 트리(src/collect.c + windows-agent/src/collect.c) v2 수집 구현 + schema/wire.schema.json 정본 교체(현행 -> v2) + check-contract.sh v2. Windows 디스크 IOCTL 성능경로 revert -> perflib 전환.
- 검증(필수·별 마일스톤): C 에이전트 emit 을 testbed 매트릭스(Linux 2.6.32~5.15 + Windows NT5.2~NT10, st-* 스토리지/E축)로 재검증. 프로토타입/probe 가 acceptance 하네스.
- 엔진: v2 마이그레이션(ingest DTO -> DB 스키마·단위 -> recommendation PSI-first 배선 -> 신규 신호 표시). dual-read 불요(pre-prod, flag-day cutover). GA 시점 schema_version 기반 dual-read 재검토.
