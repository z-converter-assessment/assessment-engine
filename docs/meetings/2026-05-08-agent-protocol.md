# Agent ↔ Engine 프로토콜 협의 안건

> **회의 일자**: 2026-05-08
> 
> **현재 계약 버전**: agent v1.0.0 / payload-schema v3 (2026-05-06)
> 
> **참석 대상**: 에이전트(C 바이너리) 측 / 엔진(assessment-engine) 측
> 
> **목적**: 운영 중 누적된 한계와 정확성 이슈를 다음 agent_version 사이클에서 어떻게 해결할지 합의

본 안건은 "있으면 좋은 것"이 아니라 **운영 중 실제로 발생한 비용 / 누적된 부정확성**에 기반한다.

---

## 1. 의제 A — Inventory 주기적 재발행 (정보 갱신 / drift 방지)

### 현재 상황
- inventory는 에이전트 기동 시 1회만 발행 (`assessment-agent/src/main.c` `publish_with_retry`). 운영 중 정적 정보 변경 감지·주기 발행 로직 없음.

### 동기 키워드

**(가) 정보 갱신 (drift 방지)**
여기서 drift는 **실제 인프라 상태와 DB에 기록된 inventory 사이의 누적 차이**. 정적 인프라가 운영 중 천천히 변할 수 있다 — 디스크 hotplug, 마운트 추가, 패키지 설치로 인한 새 systemd service / listen port, OS 패치로 kernel_version 변경, swap 재구성, cpu_cores hotplug 등. 현재 에이전트는 기동 시 1회만 발행하므로 **모든 운영 중 변경이 다음 에이전트 재시작까지 DB에 반영 안 됨**.

**(나) Placeholder → 풀 정보 전환 시간 상한 보장**
엔진의 auto-register로 생성된 placeholder는 정적 정보가 None. 다음 진짜 inventory 도착까지 web UI에서 "OS / CPU / Memory: -" 표시. 주기 재발행이면 **최대 1시간 안에** 풀 정보로 자동 전환 보장.

**(다) agent_version drift 추적**
여기서 drift는 **운영 서버 간 에이전트 버전이 통일되지 않은 상태**. 에이전트 업데이트 후 새 버전 정보가 inventory에 담김. 주기 재발행이면 운영팀이 deployment 진척을 web UI에서 즉시 확인 — 별도 도구 없이 version drift 모니터링.

### 협의 옵션
- 에이전트가 **30분 또는 1시간 주기로 inventory 자동 재발행**
- 엔진은 이미 `machine_id` UPSERT — 중복 무해

### 트레이드오프
- 비용: inventory ≈ 2KB × 1시간 주기 = 서버당 시간당 1건. 무시 가능
- 이득: 위 (가)~(다) 3가지 가치 동시 확보

---

## 2. 의제 B — `agent_started_at` 메타데이터

### 현재 상황

용어: **카운터** = `/proc/diskstats`·`/proc/net/dev`·`/proc/stat` 등 단조 증가하는 raw 누적값 (디스크 sectors_read, NIC rx_bytes, CPU jiffies 등). 재부팅·에이전트 재시작 시 0으로 리셋. 엔진이 두 시점 차로 delta·throughput을 계산.

- reset 시점 식별 가능한 메타는 `boot_time`(시스템 부팅 시각)뿐. 에이전트만 `systemctl restart`된 경우 `boot_time` 변화 없어 식별 불가.
- 엔진은 `delta < 0`만으로 reset 검출 — 카운터가 우연히 비슷한 값에서 재시작하면 false negative.

### 협의
- 메시지 공통 메타데이터에 `agent_started_at` 추가 (ISO 8601 UTC, 에이전트 프로세스 기동 시각)
- 엔진은 두 metrics readings의 `agent_started_at`이 다르면 delta 계산 skip

---

## 3. 의제 C — metrics 메시지에 `boot_time` 포함

### 현재 상황
- `boot_time`은 inventory에만 포함. metrics 두 시점 간 reset 검출하려면 inventory를 따로 조회해야 하나 엔진 미구현.
- 에이전트 측 약속("boot_time change marks a series cut") vs 엔진 미구현 → 본 의제 채택 시 metrics 자체에서 비교 가능해 자연 해소.

### 협의
- `server.metrics` 공통 메타데이터(또는 메시지 본문)에 `boot_time` 추가
- 부담: 8 bytes 추가 (ISO 8601 문자열 ≈ 20 bytes)
- 엔진의 `metrics_calculator.compute_*` 함수에서 두 readings의 `boot_time` 비교 → 다르면 delta skip → 잘못된 spike 제거