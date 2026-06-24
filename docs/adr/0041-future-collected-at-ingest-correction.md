# ADR 0041 — collected_at 수신 경계 보정 (시계오차)

상태: Accepted (2026-06-24)

## 정정 (2026-06-24): 양방향 확장

최초 future-only(미래만 보정, 과거는 backlog 정상 지연 보존)로 채택했으나, Windows 게스트 시계가 과거로도
틀어지는 사례가 관측돼 양방향으로 확장했다. 함수명 `_correct_future_collected_at` -> `_correct_skewed_collected_at`,
조건 `skew > threshold` -> `abs(skew) > threshold`, 설정 `clock_skew_future_threshold_sec` -> `clock_skew_threshold_sec`.
과거 방향 trade-off: consumer backlog/재처리로 늦게 온 정상 메트릭도 threshold 넘으면 보정될 수 있다(시계오차
흡수 위해 수용). 아래 본문은 최초 future-only 결정 기록 — "미래만" 서술은 본 정정으로 양방향이 현행.

## Context

일부 Windows 게스트(특히 OpenStack 위 Windows Server 2022)의 시스템 시계가 부팅마다 들쭉날쭉 틀어진다 — 어떤 부팅은 정상, 어떤 부팅은 수시간 미래로 점프(관측 사례 +5.5h). 에이전트는 게스트 시계를 충실히 읽어 보내므로 `task.result`/metrics 의 `boot_time`·`agent_started_at`·`collected_at` 세 시각이 같은 양만큼 함께 틀어진다. 즉 에이전트 tz 라벨링 버그가 아니라 게스트 실제 시계 문제다 (근본 해결은 게스트 NTP/`RealTimeIsUniversal`).

`collected_at` 이 미래로 찍히면 hypertable partition pruning·차트·right-sizing 윈도우가 그 호스트 데이터를 오배치/오집계해 데이터 품질이 깨진다. 엔진이 수신하는 순간이 진짜 UTC(server now)를 아는 유일한 신뢰 지점이고, 쿼리 시점엔 보정할 방법이 없다.

기존 `_log_time_invariants` 는 boot <= agent_started <= collected 내부 순서만 검사 — 셋이 같이 틀어지면 순서는 유지돼 통과한다. 서버 자신의 시계와 비교하지 않아 본 케이스를 못 잡는다.

## Options

### A. 게스트 시각 동기만 (엔진 무변경)
근본 해결이나 운영자 인프라 작업에 의존. 그 전까지 차트·윈도우 오염 무방비.

### B. 수신시각 고정 offset 보정
skew 가 부팅마다 가변(0 ~ +5.5h)이라 뺄 상수가 없다 — 불가.

### C. 수신 경계 future-only 보정 (채택)
`collected_at - received_at > threshold(5분)` 이면 `collected_at = received_at`. 미래는 "미래에서 수집" 이 물리적으로 불가능하니 오탐 0. 과거 방향은 보정하지 않는다 — consumer backlog 로 정상 지연 가능(복구 시 오래된 메시지)이라 당기면 실제 이력을 뭉갠다.

### D. 미래값 reject(DLQ) / 탐지·플래그만
reject 는 데이터 손실, flag-only 는 오염 잔존. 보정보다 약하다.

## Decision

옵션 C. consumer 수신 경계(`handlers/_common._correct_future_collected_at`, metrics 핸들러에서 멱등성 체크 직후)에서 future-only 보정.

- `collected_at` 만 보정한다. `boot_time`·`agent_started_at` 은 건드리지 않는다 — 수신시각 기반 평행이동은 per-message 전송지연 jitter 를 "부팅 내 불변값" 에 주입해 counter-reset(#B/#D)·재시작 추적의 안정 가정을 깬다. 잔여 미래값(agent_started_at > collected_at)은 `_log_time_invariants` 가 시계이상 신호로 노출.
- 보정 시 F7 쿨다운 WARNING 로그(스팸 방지). Redis 장애 fail-open.
- 임계는 `ConsumerSettings.clock_skew_future_threshold_sec`(기본 300).

## Consequences

포기한 것 / 한계
- `collected_at` 은 시계열 자연키(#C1 `UNIQUE(server_id, collected_at)`). 수신시각 기준 보정은 비결정적(재처리 시 now 상이)이라 D2 2단(DB UNIQUE)을 약화한다. 단 D2 1단(message_id SET NX, 24h)이 재전송을 DB 도달 전에 흡수하므로 실노출은 "Redis 장애 + 재전송 + 시계불량" 동시 발생의 좁은 창뿐. 상세·재검토 트리거는 `docs/tradeoffs.md` T17.
- 보정은 임시 방어선 — 근본은 게스트 시각 동기.

왜 받아들였나
- 미래 방향만이라 오탐 0(물리적으로 미래 수집 불가). 과거 미보정으로 backlog 정상 지연 데이터 보존.
- 멱등성 노출이 1단 dedup 으로 좁고, 한 호스트의 틀린 시계가 차트·윈도우 전역을 오염시키는 것을 수신 경계에서 차단.

언제 다시 봐야 하는가
- 게스트 시각 동기가 인프라에서 해결되면 본 보정은 no-op 으로 남아도 무해(방어선 유지).
- backlog 가 일상화돼 과거 방향 보정도 필요해지면 → backlog 로 설명 불가능한 큰 과거 skew(예: 24h 초과)만 별도 정책으로.
