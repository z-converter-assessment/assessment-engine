# ADR 0050 — collected_at 수신 경계 보정 제거 (에이전트 UTC 정상 전제)

상태: Accepted (2026-07-01) — ADR 0041 supersede

## Context

ADR 0041 은 일부 Windows 게스트(OpenStack 위 Windows Server 2022)의 시스템 시계가 부팅마다 수시간 틀어지는
사례를 흡수하려고 `_correct_skewed_collected_at`(metrics 핸들러, 멱등성 체크 직후)를 도입했다. `abs(collected_at
- received_at) > 5분` 이면 `collected_at` 을 수신시각(`received_at`)으로 덮어썼다.

이 보정은 `collected_at`(시계열 자연키 #C1 `UNIQUE(server_id, collected_at)`)을 수신시각 기준으로 재작성한다.
수신시각은 재처리 시점마다 달라 비결정적이라, D2 멱등성 2단(DB UNIQUE `on_conflict_do_nothing`)이 "동일 메시지
재전송"을 중복으로 못 잡을 수 있다(서로 다른 collected_at -> 두 행). ADR 0041·T17 이 이 약화를 의식적
트레이드오프로 수용했고, 재검토 트리거로 "게스트 시각 동기가 인프라에서 해결되면 보정은 무해한 no-op" 을 적어뒀다.

이제 전제가 바뀌었다: 에이전트를 설치하는 서버는 UTC 기준 정상 시각을 발행한다고 전제한다(게스트 NTP /
`RealTimeIsUniversal` 를 인프라 레벨에서 보장). 이 전제 하에서 수신 경계 보정은 불필요할 뿐 아니라, 자연키를
비결정적으로 재작성해 멱등성만 약화하는 순손해다.

## Decision

수신 경계 보정을 제거한다.

- `_correct_skewed_collected_at`(`consumer/handlers/_common.py`) 함수 + metrics 핸들러 호출 삭제.
- 관련 설정 삭제: `clock_skew_threshold_sec`, `redis_key_clock_corrected`, `redis_ttl_clock_corrected`.
- `collected_at` 은 에이전트 발행값을 그대로 신뢰(권위 소스) — 재작성 없음.

`_log_time_invariants` 는 유지한다. 이는 보정(데이터 변형)이 아니라 순수 이상 신호 로깅이다 — `boot_time <=
agent_started_at <= collected_at` 내부 순서 위반을 warning 으로만 노출(쿨다운 스로틀, DLQ 미전송, 데이터
불변). UTC 정상 전제가 깨진 서버를 사후 인지하는 관측 방어선으로 남긴다.

## Consequences

- 얻은 것: D2 멱등성 2단(DB UNIQUE)의 결정성 회복 — `collected_at` 자연키가 더 이상 수신시각으로
  비결정적 재작성되지 않아 "Redis 장애 + 재전송 + 시계불량" 동시 창의 중복 행 위험 소멸. 보정 코드·설정·쿨다운
  Redis 키·전용 테스트 제거로 consumer hot path 단순화. T17 트레이드오프 자체 소멸(tradeoffs.md 에서 제거).
- 포기한 것: 시계 불량 서버가 UTC 아닌 `collected_at` 을 보내면 hypertable partition pruning·차트·right-sizing
  윈도우가 그 호스트 데이터를 오배치할 수 있다 — 이 책임은 인프라(게스트 시각 동기)로 이관. `_log_time_invariants`
  가 내부 순서 위반은 신호로 노출하나, 셋이 같은 양만큼 함께 틀어진 경우(순서 유지)는 못 잡는다(ADR 0041 Context
  동일 한계) — 전제 위반 시 데이터 품질은 인프라 보장에 의존.
- 재검토 트리거: UTC 정상 전제가 실제로 깨진 서버가 관측되면(미래 collected_at 오염 재발) → 게스트 NTP 강제를
  인프라 레벨에서 재점검하거나, 보정 대신 미래값 reject(DLQ) 로 방향 전환(재작성 없는 결정적 처리).
