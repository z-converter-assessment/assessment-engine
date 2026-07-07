# ADR 0018: dev 한정 ZDM mock endpoint (web 컨테이너 재활용)

Status: Superseded by ADR 0045 (2026-06-26). 원: Accepted (2026-05-22)

Refines: ADR 0016 (self-host install bundle 제거 + ZDM 본체 패키지 직접 fetch)

## Context

ADR 0016 채택 후 install 흐름은 단일 단계 — agent worker 가 ZDM 본체 패키지를 직접 fetch + tar 추출 + install.sh exec. engine 은 wrapper/bundle 자체를 호스트하지 않고 task.install 메시지에 `download.url = http://{ZDM_IP}{ZDM_PACKAGE_PATH}` 만 박는다.

본 결정으로 prod 운영 책임 분기는 깔끔해졌으나 dev/Lima 환경에서 다음 빈 곳:

- 실제 ZDM 서버가 dev 환경에 없음 — `192.168.3.94` 같은 사내 ZDM 좌표는 Lima VM 의 agent worker 가 user-mode network 에서 도달 불가 (NAT 경계).
- `dev/agent.env` 의 `WORKER_DOWNLOAD_ALLOWED_HOSTS=host.lima.internal` — agent worker 는 host (Mac) 측 endpoint 만 download 허용.
- 위 두 제약으로 dev 에서 install task E2E (모달 발행 → publish → download → exec → task.result → consumer 6 컬럼 UPDATE → list UI badge 갱신) 실증 자체가 불가. HttpZdmPackageResolver HEAD 가 외부 ZDM 에 도달 못 해 publish 가 503 차단.
- ADR 0008 (engine HTTPS endpoint) / 0009 (dev plain HTTP 복귀) 는 install bundle endpoint 자체가 사라지면서 ADR 0016 으로 supersede 됐는데, dev mock 도 같이 사라진 셈.

가능 옵션:

- dev/docker-compose 에 별도 nginx 컨테이너 추가 — 컨테이너 1개 추가 (운영 부담) + 호스트 마운트 fixture 관리 필요. web 컨테이너 재활용 대비 부담 큼.
- agent 측 ZDM mock 모드 — agent 외부 repo + 코드 추가 + dev-only 분기 오염. agent contract 무변경 정합 깨짐.
- 본 repo 의 web 컨테이너에 dev-only mock router 추가 — startup 비용 미미, 별도 컨테이너 없음, prod 등록 안 됨으로 분리 보장.

## Decision

`APP_ENV=dev` 일 때 web 컨테이너에 ZDM 본체 패키지 mock router 등록. ADR 0016 의 단일 단계 흐름 사상 유지 — engine 이 wrapper 가 아니라 "ZDM 측 역할" 을 dev 한정으로 재현.

### 라우터

- 파일: `src/assessment_engine/web/routers/dev_zdm_mock.py`
- 라우트: `api_route({web_settings.zdm_package_path}, methods=["GET", "HEAD"])` (default path `/download/ZConverter_CloudSource_Setup_Linux.tar.gz`). FastAPI 가 GET → HEAD 자동 매핑 안 함 → 두 메서드 모두 받고 핸들러 안에서 `request.method == "HEAD"` 면 body 만 drop, 헤더 (Content-Length 명시 포함) 는 동일.
- 응답 헤더 (HEAD/GET 일관):
  - `Content-Length` — HttpZdmPackageResolver HEAD 의 size_bytes 추출
  - `ETag` — 더미 tarball 의 sha256 hex (불변, 결정론적 build 이라 process restart 후에도 동일)
  - `Last-Modified` — ETag fallback (resolver 는 ETag 우선이라 실질적으로 미참조)
- body: in-memory build 더미 tar.gz — startup 1회 build + bytes 캐싱
- tar 내용: 디렉토리 entry `zconverter_install_source/` + 파일 entry `zconverter_install_source/install.sh` (운영 ZDM 본체 패키지 layout 과 동일 경로). 디렉토리 entry 명시 의무 — agent `extract.c` 가 libarchive 의 `ARCHIVE_EXTRACT_NO_AUTODIR` 플래그로 부모 디렉토리 자동 mkdir 비활성, dir entry 없으면 file write 시 ENOENT → `extract_failed`. install.sh 는 인자 `-s ZDM_IP -u ZDM_USER` 받아 stdout 에 echo + exit 0.

### 등록 분기

`web/main.py` 에서:

```python
if web_settings.app_env == "dev":
    from assessment_engine.web.routers.dev_zdm_mock import dev_zdm_mock_router
    app.include_router(dev_zdm_mock_router)
```

prod (`APP_ENV=prod`) 에서는 import 자체가 안 일어남. ADR 0016 사상 (engine 이 prod 에서 install 패키지 안 호스트) 정합.

### dev default 좌표 변경

- `config.py` 의 `_ZDM_DEV_DEFAULT_IP` 및 `zdm_default_ip` instance default 를 `192.168.3.94` 에서 `host.lima.internal:8000` 으로 변경.
- agent worker 의 `download_url_extract_host` (`download.c:71`) 가 `':'` 도 host 종료 문자로 처리 — port 제외하고 host 만 추출. `download_host_allowed` 는 단순 strcmp 매칭 — `host.lima.internal` whitelist 에 그대로 통과.
- `_validate_prod_web_secrets` 의 거부 메시지도 새 dev default 와 일관 (`host.lima.internal:8000`).
- `dev/agent.env` 의 `WORKER_DOWNLOAD_ALLOWED_HOSTS` 무변경. `dev/docker-compose.yml` ports 매핑 무변경 (`8000:8000` 그대로).

### `extra_hosts` self-loop alias (dev compose)

`HttpZdmPackageResolver` 가 publish 직전 ZDM 호스트에 HEAD/GET 으로 메타 산출 — dev 에서 ZDM 호스트가 web 컨테이너 자기 자신이라 self-loop 필요. `host.lima.internal` 은 Lima VM 의 user-mode network alias 일 뿐 web 컨테이너 안에서는 unknown host (DNS 실패) → `ZdmPackageMetaError` → publish 503 차단.

`dev/docker-compose.yml` web service 에 `extra_hosts: ["host.lima.internal:host-gateway"]` 추가 — Docker Engine 20.10+ / Compose v2 의 magic value 가 Mac host gateway IP 로 치환. 흐름:

- web 컨테이너 → `host.lima.internal:8000` → Mac host gateway → port 매핑 → web 자기 자신 reentrant
- Lima VM agent worker → `host.lima.internal:8000` → Mac host → 같은 web 컨테이너

양쪽이 같은 alias 로 같은 endpoint 도달. resolver 도 운영과 동일한 HTTP HEAD/GET 흐름 실행 — dev 분기 우회 없이 resolver 자체 회귀까지 잡힘.

### `_validate_zdm_ip` 매트릭스 (`web/routers/tasks.py`)

`InstallRequest.zdm_ip` 가 운영자 입력으로 받을 수 있는 형식 카탈로그:

| 형식 | 예 | 통과 |
|------|-----|------|
| IPv4 | `192.168.3.94` | OK |
| IPv4:port | `192.168.3.94:8080` | OK |
| hostname / FQDN (옵션 trailing dot) | `zdm.example.com` / `zdm.example.com.` | OK |
| hostname:port | `host.lima.internal:8000` | OK |
| http/https URL (대소문자 무관) | `http://zdm.example.com/` / `HTTPS://Zdm.Example.com:8443/path/x.tar.gz` | OK |
| IPv6 raw / bracket | `::1` / `[::1]` / `[::1]:8000` | NG — agent `download.c::download_url_extract_host` 가 IPv6 bracket 미처리 (한계, 별도 ADR 필요) |
| shell metachar | `host;rm -rf` | NG — `#F8` 차단 |

검증 helper 는 모듈 상단 `_is_valid_host_or_host_port` (IPv4 / hostname / optional port) + URL form 별도 분기로 단일 진실.

## Consequences

긍정:

- dev install E2E 실증 가능 — `pipeline-up.sh` 로 Lima 4 VM + engine 기동 후 list 모달 default 그대로 "발행" 만으로 publish → agent worker download → install.sh exec → task.result → consumer UPDATE → list UI badge 흐름 1 cycle 검증.
- ADR 0016 단일 단계 흐름 사상 유지 — engine 은 wrapper 가 아니라 ZDM 역할을 dev 한정 재현. prod 운영에서는 외부 인프라가 자체 ZDM 호스트.
- prod 영향 0 — 라우터 자체가 안 붙음 (`if app_env == "dev"` 분기). prod 부팅 시 `_validate_prod_web_secrets` 가 `zdm_default_ip == "host.lima.internal:8000"` 거부 — 운영자가 명시 좌표 박지 않으면 부팅 자체가 실패 (정공).
- 외부 nginx 컨테이너 없음 — dev/docker-compose 변경 0. 컨테이너 1개 추가 시 idle 메모리·image pull·port 매핑 운영 부담 발생, 본 안은 web 컨테이너 안 router 1개라 부담 미미.

부정:

- dev mock 의 install.sh 는 echo + exit 0 — 실제 ZConverter 설치 동작 검증 못 함. 평가 범위는 메시지 흐름 + DB UPDATE + UI badge 갱신. 실제 install 검증은 staging/prod 의 실 ZDM 좌표 박은 환경에서.
- 멀티 worker uvicorn 시 worker 마다 startup tarball build — mtime=0 결정론적 build 라 동일 bytes/sha256 보장이지만 process 별 메모리 점유 (수십 KB 수준이라 무시 가능).
- dev mock tarball 사이즈가 운영 ZDM 본체 패키지(44MB 가정) 와 다름 — Resolver 의 size mismatch 가드(`HEAD Content-Length == GET 실측`) 는 mock 안에서 자체 정합 (Content-Length 도 mock 이 박음). 운영 환경 size 와 다르다는 점만 별도 인지.

미정:

- 실제 ZConverter 본체 패키지 fixture 를 dev mock 에 박을지 — 사이즈/라이센스/repo bloat 트레이드오프. 현재는 더미 install.sh + exit 0 으로 메시지 흐름만 검증.
- Windows 호스트 install 워크플로 미지원 (ADR 0016 결정 그대로). 사양 보강 시 별도 ADR.

## Update (2026-05-24)

"Consequences > 긍정" 의 "prod 부팅 시 `_validate_prod_web_secrets` 가 `zdm_default_ip` dev default 를 거부 — 부팅 실패" 서술은 이후 철회됨. startup 거부 로직(`_validate_prod_web_secrets` 의 ZDM 검사 + `_ZDM_DEV_DEFAULT_IP`/`_ZDM_DEV_DEFAULT_USER` 상수)을 제거하고, 잘못된 ZDM 발행 방어를 런타임(`HttpZdmPackageResolver` 메타 도달 실패 시 503 차단) + agent host whitelist(`WORKER_DOWNLOAD_ALLOWED_HOSTS`, `url_not_allowed` reject)에 일임.

사유: ZDM 좌표는 secret 이 아니고(노출 무해), startup 검사가 dev default 정확 일치만 잡아 오타·잘못된 커스텀 값은 통과하므로 실효가 제한적이며, 런타임·agent 2차 방어가 실제 발행을 차단한다. discovery probe 기본값(`DISCOVERY_DEFAULT_TARGET`)과 동일하게 startup 거부 없는 정책으로 통일. 본 ADR 의 dev mock endpoint 결정 자체는 유효.
