# ADR 0044 — composite_id 재연결 (재부팅으로 composite_id 변동 시 동일 호스트 흡수)

상태: Superseded by 0049 (2026-07-01) — 이전 Accepted (2026-06-24)

## Context

엔진은 `composite_id`(= sha256(machine_id + 정렬·dedup NIC MAC 들), ADR 0027)를 식별 단일 키로 쓴다. agent 가
부팅마다 composite_id 를 1회 계산하는데, 일부 환경(관측: OpenStack 위 Windows VM)은 부팅마다 NIC 포트가
재생성돼 MAC 이 통째로 바뀐다. machine_id(MachineGuid)·hostname 은 고정인데 MAC 만 바뀌어 composite_id 가
같은 VM 인데 달라진다. 컨슈머는 미등록 composite_id 를 새 서버로 auto-register -> 같은 VM 이 재부팅마다
중복 server_inventory 행으로 쪼개진다(실측: 수십 대 중복).

machine_id 는 저장만 하고 식별·라우팅에 미사용이라(ADR 0027), composite_id 미스를 되살릴 fallback 이 없었다.

## Decision

inventory upsert 진입 시 composite_id 가 미등록이면 `_relink_rebooted_host` 로 동일 호스트를 찾아 재연결.

- 매칭: machine_id(not null) + hostname 일치, 후보가 정확히 1개일 때만. 기존 행의 composite_id 를 새 값으로
  UPDATE(re-point) — server_id(식별·시계열 FK·history) 보존, 중복 행 0. `find_server_id(new) is None` 확인 후라
  composite_id UNIQUE 충돌 불가, 1 메시지 1 트랜잭션 안.
- MAC 은 매칭에 쓰지 않는다 — 부팅마다 통째로 바뀌는 환경이 있어 무용. 안정 식별자는 machine_id + hostname.
- 후보 0개(신규 호스트)·2+ (machine_id+hostname 동일한 모호한 clone)면 미연결 -> 새 행(clone 오병합 방지).
- inventory 경로만 — agent 가 startup 시 inventory 를 metrics 보다 먼저 발행(main.c)해 재부팅을 먼저 잡는다.
  metrics 는 mac/충분 신호 부족이라 미적용.

## Options

### A. agent 수정 (Windows UP-only NIC 필터 등)
근본은 agent 의 MAC 불안정이나, 컴파일된 바이너리 재배포 의존 + 이미 배포된 호스트 미적용.

### B. 엔진 fallback 재연결 (채택)
재배포 없이 흡수. machine_id+hostname 으로 안정 식별. 단일 후보 가드로 clone 오병합 방지.

### MAC 교집합 안 쓰는 이유
초안은 MAC 교집합(물리 NIC 보존 가정)을 엄밀 가드로 썼으나, 실측상 부팅마다 MAC 이 전부 바뀌어(교집합 0)
재연결이 무력했다. machine_id+hostname 단일후보로 완화 — 이 fleet 의 clone 은 hostname 또는 machine_id 가
달라(sysprep/rename) 오병합 위험 없음. trade-off: machine_id+hostname 을 둘 다 공유하는 별개 clone 은
오병합될 수 있다(둘 다 같으면 사실상 식별 불가라 수용).

## Consequences

- 재부팅으로 composite_id 가 바뀌어도 중복 행 0 — server_id·시계열·history 연속. task 라우팅도 최신 composite_id.
- ADR 0027 의 "machine_id 식별 미사용"을 fallback 한정 완화 — machine_id 는 평시 식별엔 여전히 미사용,
  composite_id 미스 시에만 hostname 과 함께 재연결 키.
- 기존 중복 행은 일회성 정리 필요(machine_id+hostname 중복 그룹에서 최신 메트릭 행만 유지).
- 한계: metrics-first race(드묾)·MAC 전체 변동 환경에서 machine_id+hostname 모호(2+ 후보) 시 미연결.
