# ADR 0019: task.install payload 에 install.type enum 도입 (Windows install 지원 준비)

Status: Accepted (2026-05-23)

Refines: ADR 0016 (self-host install bundle 제거 + ZDM 본체 패키지 직접 fetch — Linux 한정)

## Context

ADR 0016 채택 후 task.install 흐름은 agent worker 가 ZDM 본체 패키지를 download → libarchive 로 tar.gz extract → install.sh exec 의 단일 단계. payload schema 의 `install.script` 가 "extract 된 dir 기준 상대 path" 의미로 hardcoded — Linux .tar.gz + bash 한정 가정.

Windows install 분석 결과 흐름 본질 다름:

- download = 단일 `.exe` (self-extracting installer) 또는 `.msi` (Microsoft Installer)
- extract 단계 없음 — exe 가 자기-extracting, msi 는 msiexec 자체 해제
- 실행 대상 = download 된 파일 자체 (extract dir 안 path 아님)
- 인자 형태는 동일 — `-s ZDM_IP -u ZDM_USER` (Linux install.sh / Windows .exe 양쪽 같은 convention)

agent worker.c 의 흐름이 download → extract → exec hardcoded 라 Windows 단일 binary 흐름은 extract 단계에서 실패. 분기 의무.

분기 신호는 두 가지 옵션:

- 확장자 magic: agent 가 download URL 파일명 보고 자체 dispatch. implicit, silent drift 위험, 운영 가시성 약함, 확장자 spoofing 보안 약점.
- 명시 enum: payload 에 dispatch 신호 박음. explicit, 새 format 추가 시 양쪽 동시 결정 강제, 자기 OS 아닌 type reject 명확.

업계 정공 (HTTP Content-Type / systemd Type= / K8s apiVersion+kind / AWS Lambda Runtime 등) 모두 명시 metadata. magic 추론 아님.

## Decision

`task.install` payload 의 `install` object 에 `type` enum 필드 추가:

```
install.type: "shell" | "direct_exec" | "msi"
```

enum 카탈로그:

| install.type | 동작 | install.script | 적용 OS |
|--------------|------|----------------|---------|
| `shell` | archive (tar.gz) extract 후 script 실행 | archive 안 path | Linux |
| `direct_exec` | extract 없음, 다운로드 파일 직접 실행 | null | Windows .exe |
| `msi` | extract 없음, `msiexec /i {path} /quiet` | null | Windows .msi |

`install.args` 는 OS 무관 동일 (`["-s", zdm_host, "-u", zdm_user]`).

`task.result` payload 의 `failure_reason` enum 카탈로그에 `unsupported_install_type` 추가 — agent 가 자기 OS 에서 처리 못 하는 `install.type` 수신 시 reject. DLQ 회피, 즉시 result 발행.

### 현재 적용 범위

- engine `_publish_install` 가 `server_inventory.os_family` (ADR 0020 도입) 조회 후 OS 별 dispatch — Linux = `shell` / Windows = `direct_exec`. batch 안 OS 섞임 자동 처리.
- ZDM 패키지 path 카탈로그: `ZDM_PACKAGE_PATH` (Linux .tar.gz) + `ZDM_PACKAGE_PATH_WINDOWS` (Windows .exe) env 분리. `HttpZdmPackageResolver.resolve(zdm_host, package_path)` 가 OS 별 path 별도 fetch (cache key 도 ETag 기반 자동 분리).
- `server_inventory.os_family` 가 None (Linux agent minor bump 전) 이면 engine 측 fallback `"linux"` — 호환 단계.
- Linux agent 측 worker.c 의 `install.type` dispatch + reject 정책은 agent repo 별도 작업.
- Windows agent 신규 구현 + Windows native inventory/metrics payload 형식 결정은 agent repo 별도 작업.

## Consequences

긍정:

- task.install schema 확장 1 회로 Linux/Windows install 흐름 양쪽 표현 가능. 새 format (`.AppImage` / `pkg` 등) 추가 시 enum +1.
- agent 측 reject 정공 — 자기 OS 아닌 type 수신 시 `unsupported_install_type` 명확. OS mismatch 가 type 차원에서 자동 격리.
- 운영자 가시성 — task.install payload 만 봐서 처리 방식 즉시 파악.
- ADR 0016 의 단일 단계 흐름 사상 유지 — engine 은 ZDM 본체 패키지 좌표 + 처리 방식만 박음, agent 가 dispatch.

부정:

- payload schema 확장 — engine + agent 양쪽 minor bump 의무. 외부 repo (agent) 변경 협의 필요.
- 현재 시점에는 `shell` 만 활성 — `direct_exec` / `msi` enum 은 정의됐으나 발행 측 미사용. Windows 도입 전까지 dead code 느낌.
- `install.script` 의 의미가 type 별 다름 (`shell` 일 때만 의미) — 약한 multi-purpose 필드. 운영자가 schema 만 보고 헷갈릴 가능성.

미정:

- Windows install 발행 trigger UI — 운영자가 모달에서 OS 선택 vs install.type 직접 선택 vs 자동 dispatch (server_inventory.os_family 도입 후).
- agent reject 메시지의 운영자 노출 — `unsupported_install_type` 한글 라벨은 `web/services/mappers/task.py::_FAILURE_REASON_LABEL` 카탈로그 (11 enum) 에 추가됨.
