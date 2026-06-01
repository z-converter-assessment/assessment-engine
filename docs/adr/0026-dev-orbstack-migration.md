# ADR 0026 — dev 가상화 스택 Lima -> OrbStack 전환

상태: Superseded (2026-05-31) — dev host 가 Linux x86_64 homeserver 로 이전되며 OrbStack(macOS 전용)에서 libvirt(KVM)로 재전환. 현행 dev 가상화 단일 진실은 `docs/development/pipeline.md`·`windows-vm.md` (libvirt). 본 ADR 은 OrbStack(macOS) 시기의 역사 기록으로 보존 (정정·덮어쓰기 금지 규약).

원래 상태: Accepted (2026-05-25)

## Context

dev 파이프라인 검증(E2E)은 macOS host 에서 Lima(Apple Virtualization Framework) VM 4대 매트릭스로 agent 를 실제 Linux 환경에서 구동해 왔다 (`docs/development/pipeline.md`). Docker 엔진은 Docker Desktop, agent VM 은 Lima 가 담당하는 2 도구 구성.

운영자가 dev host 환경을 OrbStack 단일 런타임으로 전환하고 Lima 를 삭제 — OrbStack 은 Docker 엔진과 Linux machines(VM)를 한 런타임에서 통합 네트워크로 묶는 macOS 가상화 도구. 본 repo 의 Lima 전제(yaml·limactl·user-mode 네트워킹)가 환경과 불일치.

본 결정은 dev 가상화 도구 교체이며 배포 인프라(IaC)와 무관 — #A0 dev 범위 안. OrbStack 은 로컬 dev 도구로 ADR 0006 의 OpenStack prod staging 과는 이름만 비슷할 뿐 별개다.

## Options

### A. Lima 유지

- 장점: 기존 yaml·스크립트·문서 무변경.
- 단점: 운영자가 이미 Lima 삭제 — 환경 불일치. Lima 재설치 강제.

### B. OrbStack 전환

- 장점: Docker 엔진·VM 통합 네트워크 (컨테이너·VM 양방향 직접 도달 — probe 포워딩 불필요), cloud-init 없이 `orb create` 즉시 ready (Lima boot stuck 우회 로직 폐기), 단일 런타임.
- 단점: pipeline 스크립트·문서 재작성. OrbStack 환경 전제 (운영자 1회 setup).

## Decision

옵션 B 채택.

근거:
1. 운영자 환경이 이미 OrbStack — 도구 일치가 정공.
2. 통합 네트워크로 dev 검증 단순화 — Lima user-mode 격리(`host.lima.internal` alias + SSH localPort 포워딩)가 사라지고, `host.docker.internal`(VM·컨테이너 -> host) + `<name>.orb.local`(probe -> agent VM 직접)로 통일.
3. cloud-init 부재로 `orb create` 동기 완료 = 즉시 ready — Lima 의 "boot scripts must finished" stuck 우회 PID kill 로직 폐기.
4. yaml provision(합성 부하·swap·restart-demo)을 pipeline-up.sh post-provision 함수로 흡수 — VM 정의 단일 진실이 dispatch 함수로 통일.

## Architecture

### 네트워크 좌표

| 용도 | Lima | OrbStack |
|------|------|----------|
| VM·컨테이너 -> host (RabbitMQ/ZDM mock) | `host.lima.internal` | `host.docker.internal` |
| probe (web 컨테이너 -> agent VM SSH) | `host.docker.internal:<localPort>` (포워딩) | `<name>.orb.local:22` (직접) |
| VM 생성·관리 | `limactl start <yaml>` | `orb create <distro> <name>` |
| VM 명령 실행 | `limactl shell <vm>` | `ssh <vm>@orb` |

### 코드·스크립트 변경

- `dev/lima/*.yaml` 4개 삭제 — distro 는 `vm_distro` dispatch (debian:12/13·rocky:9·alma:9), provision 은 post-provision 흡수.
- `dev/pipeline-up.sh` 재작성 — `orb create`/`orb start`, `ssh <vm>@orb` 후처리, 바이너리 ssh stdin 전송, 합성 부하·swap·restart-demo 함수(`install_synthetic_load`/`install_swap_trigger`/`install_agent_restart_demo`). `LIMA_VMS` -> `ORB_VMS`, `DISCOVERY_PROBE_SSH_LOCALPORT` 폐기.
- `dev/pipeline-down.sh` — `orb delete`.
- `dev/docker-compose.yml` — `extra_hosts: host.lima.internal:host-gateway` 제거 (OrbStack 기본 제공), `DISCOVERY_DEFAULT_TARGET` default = `db-server-01.orb.local`.
- `src/assessment_engine/config.py` — `zdm_default_ip` default `host.docker.internal:8000`, discovery probe 주석 정정. `device_filters.py`·`query/types.py` 의 `/mnt/lima-cidata` 필터 제거 (Lima cloud-init ISO 전용 — OrbStack 무관, iso9660 fstype 필터가 일반 ISO 계속 방어).

### 기존 ADR 좌표 대체

- ADR 0018 (dev ZDM mock): 결정(web 컨테이너 mock 재활용)은 유효. `ZDM_DEFAULT_IP` dev default 좌표만 `host.lima.internal:8000` -> `host.docker.internal:8000`. 0018 본문은 결정 시점(Lima) historical 유지.
- ADR 0008/0009 (Superseded by 0016): dev TLS/plain HTTP SAN·url 의 `host.lima.internal` 은 이미 무효 결정의 historical 좌표 — 무변경.
- ADR 0006 (OpenStack staging, Withdrawn): prod 인프라 staging — OrbStack(로컬 dev)과 무관.

## Consequences

### 긍정

- dev 검증 단순화 — probe 포워딩·boot stuck 우회 로직 폐기, VM yaml 4개 제거.
- 통합 네트워크 — 컨테이너·VM 양방향 직접 도달.
- VM 정의 단일 진실 (dispatch 함수) — yaml 분산 제거.

### 부정·한계

- OrbStack 환경 전제 — 운영자 1회 setup (OrbStack 설치). 다른 dev host(Lima·colima)는 본 pipeline 미지원.
- OrbStack CLI(`orb create`/`ssh <vm>@orb`)·distro 태그·통합 네트워크 동작은 OrbStack 버전 의존 — pipeline 스크립트는 실 OrbStack 환경 검증 시 조정 가능.
- Lima 도입 검증 round(15 사고 패턴 — cloud-init·virtiofs·mirror)는 historical artifact — git history 보존, pipeline.md 에서 제거.

## 관련 문서

- `docs/development/pipeline.md` — OrbStack 파이프라인 검증 단일 진실
- `dev/pipeline-up.sh` · `dev/pipeline-down.sh` — orb 기반 스크립트
- ADR 0018: dev ZDM mock (좌표 대체)
- ADR 0006: OpenStack staging (별개 — prod 인프라)
