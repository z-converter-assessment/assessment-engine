# ADR 0016 — Self-host install bundle 제거 + ZDM 본체 패키지 직접 fetch (0008·0009 supersede)

상태: 채택 (2026-05-21) — Supersedes ADR 0008, 0009. Linux 만 지원 (Windows 별도 결정).

## Context

ADR 0007 채택 후 task.install 흐름은 다음 두 단계 다운로드였다.

1. agent worker 가 engine self-host endpoint(`/zconverter.tar.gz`) 에서 wrapper bundle fetch.
2. bundle 안 `install.sh` (wrapper) 가 `-s ZDM_IP` 인자로 받아 ZDM 서버에서 본체 패키지 fetch + 실행.

이 두 단계 다운로드는 다음 부담을 만든다:

- engine 이 wrapper bundle 의 sha256·size 단일 진실을 짊어짐 (`payloads.py` `_build_install_bundle` + `INSTALL_BUNDLE_SHA256` / `INSTALL_BUNDLE_SIZE` 상수).
- wrapper bundle 의 `install.sh` 가 IP 파싱·scheme strip·다시 ZDM 호출 등 비대칭 가공 누적 (Linux/Windows 동작 다름).
- dev 환경에서 agent worker HTTPS-only 정책과 plain HTTP endpoint 충돌 — ADR 0008/0009 가 그 해소를 위해 두 번 결정 반전, 결국 success 경로 미실증 상태로 ADR 0009 채택 (agent 측 호환성 작업까지 보류).
- wrapper 자체가 거의 thin 한데 (`curl` + `tar` + 한 줄 exec) engine 측 endpoint·테스트·문서·sha256 관리 책임이 모두 따라옴.

리더 측 사양 (2026-05-21 수신) 은 ZDM 서버가 `http://{ZDM_IP}/download/ZConverter_CloudSource_Setup_Linux.tar.gz` 경로로 본체 패키지를 호스트하고, agent 가 그 패키지를 받아 안의 `install.sh` 를 `-s ZDM_IP -u ZDM_USER` 로 실행하는 단일 흐름을 명시한다.

## Decision

engine self-host install bundle endpoint 와 wrapper 메커니즘을 모두 제거. agent 가 ZDM 본체 패키지를 직접 fetch 하는 단일 단계 흐름으로 단순화.

변경 사항:

- `web/routers/payloads.py` 파일 전체 삭제 — `/zconverter.tar.gz` + dev mock `/download/...tar.gz` endpoint 두 라우트 + wrapper 스크립트 본문 + bundle 빌드 함수·sha256·size 상수 전부.
- `task_service._publish_install` 의 `download.url` 을 운영자 입력 ZDM host 와 `ZDM_PACKAGE_PATH` env 로 조립 (`http://{ZDM_IP}{ZDM_PACKAGE_PATH}`).
- `download.sha256` / `download.size_bytes` 는 `ZDM_PACKAGE_SHA256` / `ZDM_PACKAGE_SIZE_BYTES` env 그대로. 둘 중 하나라도 미설정(빈/0) 이면 publish 차단 → 503 응답 (`TaskNotConfigured`).
- `install.script` 는 `ZDM_PACKAGE_SCRIPT` env (default `zconverter_install_source/install.sh` — ZDM 본체 tar layout).
- 운영자 입력 ZDM 좌표는 IP·hostname·URL 어느 형태든 받되 publish 시 `_extract_zdm_host` 가 scheme/path strip 해서 host 만 추출. agent host whitelist 검증과 정합.
- `_is_windows` / `_select_install_script` 헬퍼 제거 — Linux 단일.

대체된 ADR:

- 0008 (HTTPS endpoint 통합) — install bundle endpoint 자체가 사라져 무효.
- 0009 (dev plain HTTP 복귀) — 동일 사유.

## Amendment (2026-05-21)

초기 채택 직후 sha256·size 를 env 단일 진실로 박는 방식의 한계 인지: ZDM 패키지 버전 롤링 시 운영자가 4 위치 (config default + .env.example + env.md + 운영자 env file) 갱신 의무, 누락 시 agent 가 `sha256_mismatch` 로 reject.

수정: 엔진이 publish 직전 ZDM 에서 HEAD + (cache miss 시) GET full 로 메타 동적 산출 (`HttpZdmPackageResolver`).

- HEAD `Content-Length` → size_bytes
- HEAD `ETag` (또는 fallback `Last-Modified`) → Redis cache key
- cache miss 시 GET full + streaming sha256 계산 + cache set (TTL 6h, ETag 자체가 invalidation)
- HEAD Content-Length 와 GET 실측 byte count 일치 검증 (정합성)
- 메타 fetch 실패 시 publish 차단 → 503 (`TaskNotConfigured`)

ZDM 측이 Apache static serving 으로 자동 ETag 생성 (inode-size-mtime hex) — 패키지 파일 변경 시 무조건 ETag 바뀜 → cache 자동 invalidation. ZDM 측 매니페스트 endpoint 추가 contract 불필요.

config 변경:
- `zdm_package_sha256` / `zdm_package_size_bytes` env 필드 제거
- `zdm_meta_connect_timeout_sec` (default 5.0) / `zdm_meta_total_timeout_sec` (120.0) / `redis_ttl_zdm_package_sha256` (21600) 추가
- `redis_key_zdm_package_sha256` 키 prefix 추가

대안 (선택 안 함):
- ZDM 측 `.sha256` sidecar 추가 요청 — ZDM 운영자 협력 필요, 본 작업 내 결정 불가
- engine env-fixed 유지 — 패키지 버전 롤링 시 매번 env 동기 갱신 부담

## Consequences

긍정:

- engine 측 책임 단순화 — wrapper 스크립트 본문·sha256 빌드·테스트·dev mock endpoint 모두 사라짐.
- Linux/Windows 분기 가공 (`_is_windows`) 제거, Linux 한정 단일 흐름.
- agent contract (sha256·size·host whitelist) 그대로 따라 — agent 측 변경 0.
- dev/prod 동일 흐름 — ADR 0008/0009 충돌 사라짐. dev 도 ZDM 측 contract 가 갖춰지면 success 경로 동작.

부정:

- ZDM 측 contract 가 engine 운영 전제 — ZDM 호스트가 `ZDM_PACKAGE_PATH` 경로에 패키지를 안정적으로 호스트해야 함. ZDM 측 path/sha256/size 변경 시 engine env 동시 갱신 의무.
- 패키지 sha256·size 가 env 단일 — 패키지 버전 롤링 시 매번 env 갱신. 매니페스트 endpoint(ZDM 측 sha256 노출) 도입은 별도 결정.
- 운영자가 모달에 박는 ZDM host 는 agent 측 host whitelist 에 사전 등록되어야 함. 새 host 도입 시 agent 재배포.
- Windows 호스트 install 워크플로 미지원. 사양 보강 시 별도 ADR.

미정:

- ZDM 측 매니페스트 endpoint(`/download/...sha256` 같은 sidecar) 도입 여부. 도입 시 engine 이 매 publish 직전 매니페스트 fetch 해서 sha256·size 동적 박는 흐름으로 진화.
- agent 측 host whitelist 동적 갱신 메커니즘 (현재는 deploy 시점 고정).
