# Architecture Decision Records

영구·불변 의사결정 기록. 결정 변경 시 새 ADR 추가 + 이전 ADR `Status: Superseded`. 덮어쓰기 금지.

## 인덱스

| 번호 | 제목 | Status | 요약 |
|------|------|--------|------|
| 0001 | Redis fail-open 전환 | Accepted | 멱등성·캐시·부수 작업의 Redis 의존을 fail-open — DB UNIQUE 2단이 정확성 보장 |
| 0002 | Task RPC piggyback vs polling | Superseded by 0007 | 운영자 작업 명령을 `server.metrics` reply 채널에 piggyback — 발행 측 별도 worker 진화로 폐기 |
| 0003 | AI/LLM 활용 로드맵 | Refined by 0010 | Phase 2~3 — USE Method 임계값·방법론·LLM 모델 선택. LLM narrative·리포트 생성은 0010으로 보류 |
| 0004 | 진단 워커 아키텍처 | Refined by 0010 | 워커·스케줄러·diagnostic_jobs·LLM 토글 인프라. "AI 진단" 명칭은 0010으로 "진단"(환경/서버 scope)으로 정정 |
| 0005 | DB Schema 관리 표준화 | Accepted | Alembic 단일 진실, migrate init-container, `alembic check` CI |
| 0006 | OpenStack 분산 staging 배포 | Withdrawn | 본 repo 범위를 기능 개발 환경으로 한정 (2026-05-16) — IaC out-of-scope, `deploy/openstack/` 삭제 |
| 0007 | Task 별도 큐 모델 | Accepted | task.install / task.result 를 `assessment.tasks` exchange + 머신별 큐로. 0002 supersede |
| 0008 | dev engine HTTPS endpoint (전체 통합) + SAN 동적화 | Superseded by 0016 | self-host install bundle endpoint 자체가 사라져 결정 무효 |
| 0009 | dev plain HTTP 복귀 (0008 supersede) | Superseded by 0016 | self-host install bundle endpoint 자체가 사라져 결정 무효 |
| 0010 | 진단 규칙 기반 한정 | Accepted | "AI 진단" 명칭 제거 (scope에 따라 환경/서버 진단). LLM 분기 보류 (mock default·ollama 미구현 유지). ADR 0003·0004 정정 |
| 0011 | Prometheus metrics endpoint | Withdrawn | `/metrics`·prometheus-fastapi-instrumentator 미적용으로 철회 (2026-06-08). 관측은 `LOG_FORMAT=json` 구조화 로그 단독 |
| 0012 | CI 산출물 = wheel + GitHub Release | Accepted | Python wheel 단일 artifact (migrations·alembic.ini 동봉) + tag(v*) → GitHub Release 자동. Dockerfile·docker-compose는 dev 한정 (`docker-compose.prod.yml` 제거) |
| 0013 | release-please 자동화 | Superseded by 0028 | Conventional Commits 기반 자동 semver bump + Release PR + tag push. release-please(트렁크 전용)가 develop git-flow 와 구조 충돌 — 0028 Commitizen 으로 대체 |
| 0014 | Diagnostic 발행 책임 분리 | Accepted | `DiagnosticSubmitter` (`diagnostic/submitter.py`) 신규 — scheduler 노드 `web.services` 의존 끊김. web service 는 호환 re-export + 조회/기록 유지 |
| 0015 | UI 임계값 단일 진실 (body data-attribute) | Accepted | `mappers._USAGE_*_PCT`/`_SWAP_DANGER_PCT` → `template_setup.env.globals["ui_thresholds"]` → `base.html` body data-attribute → JS `document.body.dataset`. P4 임계 분류 hardcoded drift 제거 |
| 0016 | self-host install bundle 제거 + ZDM 본체 패키지 직접 fetch (0008·0009 supersede) | Accepted | `web/routers/payloads.py` 삭제. task.install download.url 을 ZDM host + `ZDM_PACKAGE_PATH` 로 조립. sha256·size 는 env 단일 진실, 미설정 시 publish 차단(503). Linux 만 지원 |
| 0017 | Docker 이미지 CI 산출물 추가 (wheel 보조) | Accepted | wheel + GHCR multi-arch image (`linux/amd64,arm64`) 양쪽 발행. 4 컴포넌트 단일 이미지 + ENTRYPOINT `python -m` + CMD override. cosign keyless + BuildKit SBOM. ADR 0012 refines |
| 0018 | dev 한정 ZDM mock endpoint (web 컨테이너 재활용) | Accepted | `APP_ENV=dev` 시 web 컨테이너에 `GET {ZDM_PACKAGE_PATH}` mock router 등록 — in-memory 더미 tar.gz 반환 (install.sh = args echo + exit 0). ZDM_DEFAULT_IP dev default 를 `host.lima.internal:8000` 로 변경. install E2E 시연·자동화 검증. ADR 0016 refines |
| 0019 | task.install payload 에 install.type enum 도입 | Accepted | install.type = `shell` / `direct_exec` / `msi` enum 추가. Linux .tar.gz + bash 한정 가정을 enum 으로 확장. Windows install (.exe / .msi) 지원 준비. failure_reason 에 `unsupported_install_type` 추가. ADR 0020 의 os_family 기반 OS 별 dispatch. ADR 0016 refines |
| 0020 | inventory payload 에 os_family 필드 + server_inventory.os_family 컬럼 도입 | Accepted | OS family 식별 단일 진실. agent 가 자기 OS 명시 보고 (silent drift 위험 0). task.install dispatch (ADR 0019) 의 신호 출처. 호환 단계 (nullable + fallback "linux"). Linux agent minor bump 배포 완료 후 not-null tighten 별도 |
| 0021 | API URL prefix 단순화 (`/api/v1` → `/api`) | Accepted | URL versioning prefix 폐기. 모든 JSON API 는 `/api/...` 직접. B2B 내부 포털 + 외부 client 0 이라 versioning 가치 없음. routers.md 의 breaking change 절차 절 supersede |
| 0022 | 호스트 식별자 분리 (host_id 단일 식별자) | Superseded by 0027 | server_inventory 식별 3 분리 — id bigint PK (FK 대상) / host_id char(64) UNIQUE (agent 매칭, MAC+machine-id 합성 해시) / public_id UUID (URL 노출) / hostname display. MQ queue `agent.tasks.{host_id}`. ADR 0027 에서 host_id -> composite_id 단일 식별 + machine_id 표시 분리 (agent v4) 로 대체 |
| 0023 | diagnostic scheduler 폐기 (사용자 trigger 모델로 통합) | Proposed | cron 자동 발화 폐기. 사용자 trigger 만 — 14일 윈도우 변화 빈도 낮음 + RAG (0024) 도입 시도 cron 누적 정당화 약. 워커 + LLM 토글 (0004) 유지. 0004 cron 부분 supersede |
| 0024 | AI 진단 RAG 도입 (도메인 지식 phase) | Proposed | pgvector + rag_documents + 도메인 지식 만 (본 phase). embedding = mxbai-embed-large-v1 (1024d) · 인덱스 HNSW · RAG_ENABLED False default · query 영어 통일 · ingest CLI. 운영 노트·peer = 보류 |
| 0025 | LLM 단일 provider 통합 (ollama), mock 폐기 | Proposed | mock vs ollama 분기 제거 → 단일 `OllamaLlmClient` 통합. `LLM_PROVIDER`·`LLM_MOCK_LATENCY_SECONDS` env 제거. dev/prod 일관 LLM 호출. ADR 0004 LLM 토글 + 0010 LLM 분기 보류 supersede. 외부 유료 API 도입은 별도 ADR 의무 |
| 0026 | dev 가상화 스택 Lima -> OrbStack 전환 | Superseded by 0037 | Lima -> OrbStack(macOS): `host.docker.internal`·`<name>.orb.local`·post-provision 흡수·lima yaml 삭제. homeserver Linux 이전으로 0037 libvirt 재전환에 supersede — OrbStack(macOS) 시기 역사 기록 |
| 0027 | composite_id 단일 식별 + machine_id 표시 분리 (agent v4) | Accepted | agent v4 가 host_id -> machine_id(raw) + composite_id(SHA-256 hash) 분리. 엔진 식별 단일 키 = composite_id (ADR 0022 host_id 역할 전면 대체) — server_inventory UNIQUE·task 라우팅(`agent.tasks.{composite_id}`)·URL 매핑. machine_id 표시 전용(nullable). Windows agent 합류(os_family·수치 정규화·플랫폼 부재 필드 null/0, listen_ports.uid nullable). revision b3e1d7f9a2c4 |
| 0028 | Commitizen 전환 (release-please 폐기) | Superseded by 0030 | Commitizen `cz bump` 이 버전을 repo 에 commit 하는 모델 — bump 커밋이 보호된 develop·main 직접 push 불가 + `bump:` 메시지 commit-msg hook 거부. ruleset+hook 과 구조 충돌. 0030 tag-derived 로 대체 |
| 0029 | OS-aware right-sizing 분류 (Windows swap 제외 + 부분 평가) | Accepted | agent v4(0027) Windows 합류 후 OS-blind 분류 왜곡 정정. swap 은 Linux page-out 신호이나 Windows pagefile 은 baseline → `swap_saturation(os_family, swap_used)` helper 로 Windows swap 축 제외. saturation 축(load/iowait) OS 부재라 Windows 는 utilization 축만 분류 → `is_partial_evaluation`(부분 평가 마커). os_family None=Linux fallback 으로 회귀 0 |
| 0030 | tag-derived 버전 (hatch-vcs) | Accepted | 버전을 repo 에 저장 안 함 — git tag(`v*`) 단일 진실, hatch-vcs 가 빌드 시 derive. bump 커밋 자체 제거로 보호 브랜치 push·commit-msg hook `bump:` 충돌 소멸. release = main 에 tag push. release notes = GitHub 자동. CHANGELOG 자동 갱신 중단. cz 폐기. 0028 supersede. 정정(2026-06-08): tag derive 4경로 분산을 `resolve-version` job single-source 로 수렴 + stable semver 가드(prerelease 미지원) + wheel·이미지 등가성 검증 |
| 0031 | OS EOL 운영신호: endoflife.date 스냅샷 카탈로그 | Accepted | OS 지원종료(EOL) 신호를 endoflife.date 스냅샷 카탈로그로 도입 — 보고서 운영신호(os_eol)·distro 필터 옵션 단일 진실 |
| 0032 | 서비스 분류: 단일 카탈로그 + 다중 신호 + 호스트 union | Accepted | 이름 substring 단일 신호 + 분산 카탈로그(5곳)를 `SERVICE_CATALOG` 단일 진실 + 다중 신호로 전환. per-unit 은 name->comm->port(귀속 포트에만). agent join key 부재(services 에 pid 없음) 제약상, 뱃지/role/환경분포는 `detect_listen_categories`(listen 소켓 직접 분류)와 이름 분류를 union — opaque Windows SCM 이름을 1433/`sqlservr` 로 구제. 목록 행은 partial SELECT 라 name 만(T15). 외부 API 미채택(IANA 포트는 큐레이션 반영) |
| 0033 | 루트 docker-compose 단일 파일 (dev + 퀵스타트) | Superseded by 0035 | `dev/docker-compose.yml` 제거, 루트 `docker-compose.yml` 하나가 dev 파이프라인·퀵스타트 겸용 (wheel 이미지·`.env` 단일). ADR 0012 5절 supersede. ADR 0035 base/override 분리 + 0036 퀵스타트 폐기에 supersede |
| 0034 | 환경 평균 활용률 capacity-weighted 전환 | Accepted | 서버 동등가중(1대=1표)을 자원 총량 가중(Σused/Σtotal)으로 전환. CPU `1-Σd_idle/Σd_total`·MEM/DISK `Σused/Σtotal`. `environment_utilization`(전체+selection server_ids 통일, end anchor)·`metric_trend`(추이 차트) 동일 산식. 빈 구간/서버별 기간 편차 분모에 자연 반영. 거대 VM 지배는 의도(자원 활용률 관점). 윈도우 14일 고정 유지(ADR 0003) |
| 0035 | compose base(prod) + override(dev) 분리 | Accepted | 루트 `docker-compose.yml` 을 prod-safe base(build 키 없는 GHCR 이미지 pull·bind mount 없음·`PGDATA_HOST`/`MQ_DATA_HOST` 볼륨 바인딩·diagnostic-worker 포함)로 전환 = 빌드 없는 pull-and-run prod compose(릴리즈 첨부, infra B안). dev 편의는 `docker-compose.override.yml`(자동 머지)로 분리. Dockerfile 은 단일 유지(dev-prod parity). GHCR public(3절 정정 — private+PAT 철회, 토큰 없이 pull). env 정규 키 `RABBITMQ_*`. ADR 0033 "override 0·정의 1곳" supersede |
| 0036 | 퀵스타트 카테고리 폐기, dev/배포 2분류 | Accepted | 환경 모델을 dev/prod 2분류로 고정, "퀵스타트" 제3 카테고리 폐기. 루트 `.env.example` = 배포 템플릿(APP_ENV=prod·secret placeholder `changeme`로 fail-fast·배포 키 중심), dev 검증은 `dev/.env.example`. compose base/override 구조(0035) 존속, 소스 clone `docker compose up` 은 여전히 dev(기능 손실 0). ADR 0033·0035 퀵스타트 개념 정정 |
| 0037 | dev 가상화 OrbStack -> libvirt(KVM) 재전환 | Accepted | dev host macOS -> Linux x86_64 homeserver 이전. OrbStack(macOS 전용) -> libvirt+qemu-kvm(virsh·virbr0 NAT). `host.docker.internal`·`orb.local` -> libvirt 게이트웨이 IP·VM DHCP lease IP. dev-up.sh libvirt 매트릭스(Linux 5+Windows 1). `zdm_default_ip` default 빈값. ADR 0026 supersede |

트레이드오프 카탈로그(T1~T15)는 ADR 형식과 맞지 않아 `docs/tradeoffs.md`로 분리.

## 새 ADR 작성

파일명 `NNNN-짧은-제목.md` (4자리 zero-padded). 본문 권장 섹션: Status / Context / Decision / Consequences. 작성 후 본 인덱스 표에 한 줄 추가.