# ADR 0020: inventory payload 에 os_family 필드 + server_inventory.os_family 컬럼 도입

Status: Accepted (2026-05-23)

Related: ADR 0019 (task.install install.type enum)

## Context

ADR 0019 채택으로 task.install payload 에 install.type enum (shell / direct_exec / msi) 추가됨. engine `_publish_install` 가 dispatch 결정 — install.type / download.url / install.script 가 host OS 에 따라 다름.

dispatch 신호 출처 결정 필요:

- os_id (Linux distro ID — `/etc/os-release`) 기반 카탈로그 매핑 추론 — implicit, 새 distro 추가 시 매핑 갱신 silent drift 위험. Windows agent 측 os_id 형식 별도 합의.
- 명시 enum 필드 (`os_family`) — agent 가 자기 OS family 자체 보고. explicit, 새 distro/OS 추가 시 family 매핑 무관 (distro 만 다양화).

업계 사례 — Ansible facts (`ansible_os_family`), Puppet facts (`osfamily`) 모두 family / distro 별도 필드. host intrinsic 속성이라 명시 신호가 정공.

## Decision

inventory 메시지 payload 에 `os_family` 필드 추가:

```
os_family: Literal["linux", "windows"] | None
```

`server_inventory.os_family` VARCHAR(16) DB 컬럼 추가 — inventory 수신 시 mapper 가 payload 값을 INSERT. host 메타 단일 진실.

`server_inventory_history.os_family` 컬럼도 동일 — inventory 변경 감지 시 history INSERT 분기에서 같이 기록.

engine `task_service._publish_install` 가 `server_inventory.os_family` 조회 → OS 별 dispatch (ADR 0019 install.type / download.url / install.script).

### 호환 단계 처리

agent minor bump (Linux agent 가 `os_family` payload 박기 시작) 시점 호환 의무:

- Pydantic InventoryInput: `os_family: Literal["linux", "windows"] | None = None` (nullable).
- DB 컬럼: nullable + 기존 row backfill `'linux'` (본 시점 Linux 호스트만 등록).
- engine `task_service` 가 `detail.os_family or "linux"` fallback — None 받으면 Linux 가정.

agent 측 배포 완료 후 별도 revision 에서:

- Pydantic schema not-null tighten.
- DB 컬럼 NOT NULL 강제.
- engine fallback 제거.

### 보류 (Windows agent 측 데이터 형식 확정 후)

Windows agent 가 보낼 inventory / metrics native payload 본문 (WMI 출력 구조 등) 미정. engine mapper 의 Windows 분기 (정제 로직) 비워둠. 형식 확정 후 mapper 활성.

다만 `os_family="windows"` payload 신호 자체는 본 ADR 로 수신 준비 완료. Windows install 발행 시 dispatch 가능.

## Consequences

긍정:

- OS family 식별 단일 진실 (server_inventory.os_family) — task.install 발행 / mapper / 보고서 / UI 모든 분기 위치 일관.
- agent measurement 와 일관 — agent 가 자기 OS 자체 보고가 가장 정확. silent drift 위험 0.
- 새 distro 추가 시 family 매핑 갱신 불필요 (distro 만 다양화, family 는 그대로).
- enum 확장 — 향후 `bsd` / `darwin` 추가 시 Literal +1, 양쪽 동시 결정.

부정:

- agent 측 payload schema 변경 의무 — Linux agent minor bump (외부 repo + 사용 중 binary). 호환 단계 (nullable + fallback) 로 운영 영향 최소화.
- 호환 단계의 fallback (`detail.os_family or "linux"`) 가 silent drift 위험 — Windows 도입 후 fallback 제거 의무. 본 단계 한정.
- DB 컬럼 +1 (server_inventory + server_inventory_history) — schema migration 2 revision (c2a4e6f8b0d1 + c3a5e7f9b1d2).

미정:

- Linux agent 측 `os_family` payload 박기 minor bump — agent repo 별도 작업.
- not-null tighten 시점 — Linux agent 배포 완료 + 모든 등록 호스트 row 가 NOT NULL 가짐 시점에 별도 revision.
- Windows agent 측 inventory / metrics native payload 형식 — engine mapper Windows 분기 활성 시점 결정.
