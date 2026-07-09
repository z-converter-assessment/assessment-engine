# wire v2 계약 인수인계 (에이전트 -> 엔진, sign-off 요청)

> 성격: 협의 산출물. 엔진 피드백 7절 요청(canonical 예시 메시지 + wire.schema.json v2 초안) 회신.
> 이 문서는 커버. 실제 산출물은 같은 폴더의 두 파일이다:
> - `wire.schema.v2.json` — 정본 스키마(draft 2020-12). 계약.
> - `v2-example-messages.json` — canonical 예시 4종(linux/windows × metrics/inventory). 값은 testbed 실측 기반.
> 검증: 4종 예시 전부 이 스키마로 통과(jsonschema Draft202012).

## 1. 인코딩 (한눈에)

- 구조: envelope(불변, 현행 계약 유지) + `schema_version:"2.0"` + `system.*` 네임스페이스 payload.
- metric: `{type:counter|gauge, unit, points:[{attr, value}]}`. counter=monotonic 누적(엔진 델타), gauge=순간.
- attr: device(안정 id)/direction/state/source/resource/scope/window/kind/class. value: number|null(null=측정불가).
- base 단위: s / By / bit/s / 1(ratio) / operations|packets|errors|segments|connections|events|tasks|inodes.
- pressure 도 metric 통일: `pressure.stall.ratio|time` + attr resource/scope/window.

## 2. 이 계약이 확정한 것 (검증 근거)

- USE U/S: 4자원 정석 소스. Linux 2.6.32~5.15 + Windows NT5.2~NT10 실측/실배포 검증(엔진 DB win2003/2008 포함).
- USE E: 완전체 canonical(mdraid/btrfs/ext4/mpath/ioerr/net errors·discards/tcp retrans/conntrack/HardwareCorrupted/MCE). 5 testbed 검증. 완전 null 은 EDAC ce/ue(VM)뿐, HardwareCorrupted 로 대체 커버.
- device 안정키: 계층 폴백 100% 커버(Linux dm/uuid->partuuid->serial->fsuuid, Windows gptid->mbrsig->serial->volguid). id+id_type. parent 는 부모 id 로 링크(불안정 name/dm-N 정규화).
- 시계열 정합성: counter=delta->파생(util/await/throughput) 불변식 45/45 PASS, Windows perflib raw≈cooked.
- 엔진 MUST-FIX 6신호 반영: procs_blocked(cpu.blocked)·conntrack·tcp retrans·inodes·oom_kill·per-cpu(cpu attr).
- envelope 보존(agent_id/message_id/composite_id/machine_id/collected_at/boot_time/agent_started_at) + schema_version 신규.
- 마이그레이션: pre-production 확정 -> coordinated cutover(flag-day 허용, dual-read 불요). schema_version 은 GA 대비 심어둠.

## 3. 엔진 sign-off 요청 항목

계약 내용은 결정·검증 완료. 아래 형식/배선만 확인해주면 락:

1. datapoint-array 인코딩 shape(`{type,unit,points:[{attr,value}]}`)로 엔진 inbound DTO/mapper 확정 가능한가.
2. 미결 3건 결정:
   - loadavg: cpu.run_queue(procs_running 순간)로 대체 vs loadavg 병행(엔진이 현재 15m 사용).
   - swap: block_devices type=swap/pagefile 노드로 노출(용량은 스토리지, page-out 은 paging.operations).
   - device id 를 시계열 자연키로 쓸 때 (server_id, device=id, collected_at) 컬럼 매핑.
3. E축 소비 배선: retrans->net_retrans_pct(기존), disk fault(mdraid/btrfs/ext4)->confidence 오염 게이트(steal 패턴) + attention. 이 배선 열지.

## 4. sign-off 이후

- 에이전트: 양 트리(src/collect.c, windows-agent/src/collect.c)에 v2 수집 구현 + schema/wire.schema.json 정본 교체 + check-contract.sh v2.
- 검증(필수·별 마일스톤): C 에이전트 emit 을 이 testbed 매트릭스로 재검증(emit vs 네이티브, raw vs cooked, device 안정성, 시계열 불변식). 프로토타입/probe 가 acceptance 하네스.
- 엔진: dual-read 불요(pre-prod), v2 소비 코드 + 시계열 컬럼/counter_agg 단위 대응.
