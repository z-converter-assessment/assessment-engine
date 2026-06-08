# ADR 0037 — dev 가상화 OrbStack -> libvirt(KVM) 재전환

상태: Accepted (2026-06-08, 전환 2026-05-31)

## Context

dev 파이프라인 E2E 검증은 OrbStack(macOS 가상화 — Docker 엔진 + Linux machines 통합 런타임, ADR 0026)으로 agent VM 매트릭스를 구동해 왔다. dev host 가 macOS 에서 Linux x86_64 homeserver 로 이전되며 OrbStack(macOS 전용)이 환경과 불일치 — Linux 네이티브 가상화로 재전환이 필요해졌다.

## Decision

dev 가상화를 libvirt(KVM)로 재전환한다.

- 가상화 런타임 — libvirt + qemu-kvm. `virsh`(VM 정의·제어) + virbr0 NAT(192.168.122.0/24). 연결 URI `qemu:///system`.
- VM 매트릭스 — `dev/dev-up.sh` 가 Linux 5대(app/data/edge/offline-01/02) + Windows 1대(win-server-01)를 base cloud image vol-clone + cloud-init seed + autounattend 로 생성·provision.
- 네트워크 모델 — OrbStack `host.docker.internal`(컨테이너/VM -> host) + `<name>.orb.local`(probe -> agent VM) 을 libvirt NAT 게이트웨이 IP(LIBVIRT_GW 192.168.122.1, agent RABBITMQ_HOST) + VM DHCP lease IP(운영자 모달 직접 입력)로 대체.
- ZDM mock(ADR 0018) 좌표 — `host.docker.internal:8000` -> dev/.env 가 libvirt host IP:8000 주입. `zdm_default_ip` default 는 빈값(운영자/dev .env 주입, discovery_default_target 과 동일 정책).

## Consequences

- dev 가상화 단일 진실 = `docs/development/pipeline.md`(Linux 매트릭스) · `docs/development/windows-vm.md`(Windows). Linux x86_64 host 전제.
- OrbStack/Lima 전제(`orb.local`·`host.docker.internal`·`orb create`·`limactl`·`pipeline-up.sh`) 잔재를 코드·문서에서 제거.
- macOS dev 미지원 — Linux x86_64 homeserver 단일 환경.

## 관계

- ADR 0026(Lima -> OrbStack) supersede. ADR 0026 은 OrbStack(macOS) 시기 역사 기록으로 보존(불변 규약).
- ADR 0018 ZDM mock 좌표를 libvirt 게이트웨이/host IP 로 갱신.
- #F9 동시 갱신: `dev/dev-up.sh` · `dev/.env.example` · `config.py`(discovery·zdm 주석/default) · `dev_zdm_mock.py` · `list.js` · `docs/operations/env.md` · `docs/development/pipeline.md` · `docs/development/windows-vm.md`.
