# ADR 0049 — 식별 단일 키를 agent_id 로 전환 (composite_id 감사용 강등)

상태: Accepted (2026-07-01)

ADR 0027 (composite_id 단일 식별) supersede. ADR 0044 (composite_id 재연결) supersede — 재연결 로직 자체가 불요해짐.

## 배경

ADR 0027 은 엔진 식별 단일 키를 `composite_id`(SHA-256(machine_id + 정렬·dedup MAC))로 잡았다. 그러나
composite_id 는 MAC 파생이라, 부팅마다 NIC MAC 이 재발급되는 환경(관측: OpenStack 위 Windows VM)에서 같은
VM 인데 값이 바뀌어 중복 `server_inventory` 행이 생겼다. ADR 0044 는 이를 machine_id+hostname 재연결
(`_relink_rebooted_host`)로 흡수했으나, 이는 근본 원인(가변 식별자)을 우회하는 fallback 이었다 — machine_id+hostname
을 둘 다 공유하는 clone 오병합 리스크가 잔존했다.

agent 가 첫 실행 시 UUID v4 를 1회 생성·영구저장하는 `agent_id` 를 발행하기 시작했다. agent_id 는 MAC/machine_id
재발급과 무관하게 불변이라 가변 식별자 문제의 근본 해소다. agent worker 도 이미 `agent.tasks.{agent_id}` 를 구독한다.

## 결정

- 엔진 식별 단일 키 = `agent_id`(UUID). `server_inventory.agent_id` UNIQUE, MQ 큐/라우팅
  `agent.tasks.{agent_id}` / `task.install.{agent_id}`. 수집 저장·server_id 조회·task 발행 모두 agent_id 기준.
- `composite_id` 는 감사·표시용으로 강등 (nullable, UNIQUE 해제). agent 가 계속 발행하니 저장하되 식별·라우팅
  미사용 — machine_id 와 함께 clone collision 진단 자료.
- `_relink_rebooted_host` 제거. agent_id 불변이라 재부팅해도 동일 agent_id 가 자연히 같은 행을 upsert — 재연결 불요.
- URL 식별자는 `public_id`(UUID) 유지 불변. 시계열 5 테이블 FK = `server_id bigint` 불변.

## 결과

- 부팅마다 composite_id 변동 문제 근본 해소 — 중복 행·재연결 휴리스틱·오병합 리스크 모두 제거.
- task.install 배달이 agent worker 구독 큐(`agent.tasks.{agent_id}`)와 일치 — 라우팅 정합 (0027/0044 하에서는
  엔진이 composite_id 로 큐를 declare 해 배달이 어긋났다).
- breaking cutover(전 에이전트 교체 + DB 초기화 전제)라 데이터 백필 없음. 마이그레이션 `e8b4d2f6a1c9`.

## 트레이드오프

- agent_id 는 agent 로컬에 영구저장되므로 그 파일이 유실되면(재설치·디스크 초기화) 새 agent_id 로 새 행이 된다
  (옛 행은 오프라인 잔류). composite_id 재연결이 흡수하던 "같은 VM 재등장" 일부 케이스를 놓칠 수 있으나, agent_id
  영구저장이 정상 동작하는 한 발생하지 않는다. 감사 컬럼(composite_id/machine_id)이 사후 진단 자료로 남는다.
