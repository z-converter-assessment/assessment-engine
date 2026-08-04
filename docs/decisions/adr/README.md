# Architecture Decision Records

"왜 이렇게 바꿨나"의 이력 아카이브. 결정이 바뀌면 새 ADR 을 추가하고 이전 ADR 에 `Status: Superseded` 를 단다 — 지난 결정을 지우거나 덮어쓰지 않는다.

기록의 오류는 다르다. 사실과 다른 수치·이름·서술은 그 결정의 근거가 아니라 흠이므로 본문에서 바로 고친다. 정정 블록으로 "위 숫자가 틀렸다"를 덧붙이면 독자가 본문과 정정을 대조해야 하고, 그 대조를 요구하는 것이 오류를 남기는 것보다 나쁘다. 판단 기준은 하나다 — 그때의 결정이 달라지면 새 ADR, 그때의 기록이 틀렸으면 본문 수정.

라이브 문서는 여기 의존하지 않는다 — ADR 은 이력이지 현재 사실의 출처가 아니다. 현재 사실은 라이브 문서 인라인에 있어야 한다 (docs/README.md 4원칙).

## 인덱스

| 번호 | 제목 | Status | 요약 |
|------|------|--------|------|
| 0001 | Redis fail-open 전환 | Accepted | 멱등성·캐시·부수 작업의 Redis 의존을 fail-open — DB UNIQUE 2단이 정확성 보장 |
| 0002 | Task RPC piggyback vs polling | Superseded by 0007 | 운영자 작업 명령을 `server.metrics` reply 채널에 piggyback — 발행 측 별도 worker 진화로 폐기 |
| 0003 | AI/LLM 활용 로드맵 | Refined by 0010 | Phase 2~3 — USE Method 임계값·방법론·LLM 모델 선택. LLM narrative·리포트 생성은 0010으로 보류 |
| 0004 | 진단 워커 아키텍처 | Superseded (2026-06-14) | AI 진단(LLM narrative) 폐기 — 워커·LLM 인프라 제거. `diagnostic_jobs` 테이블은 보고서 발행 정적 스냅샷 용도로 존속 |
| 0005 | DB Schema 관리 표준화 | Accepted | Alembic 단일 진실, migrate init-container, `alembic check` CI |
| 0006 | OpenStack 분산 staging 배포 | Withdrawn | 본 repo 범위를 기능 개발 환경으로 한정 (2026-05-16) — IaC out-of-scope, `deploy/openstack/` 삭제 |
| 0007 | Task 별도 큐 모델 | Accepted | task.install / task.result 를 `assessment.tasks` exchange + 머신별 큐로. 0002 supersede |
| 0008 | dev engine HTTPS endpoint (전체 통합) + SAN 동적화 | Superseded by 0016 | self-host install bundle endpoint 자체가 사라져 결정 무효 |
| 0009 | dev plain HTTP 복귀 (0008 supersede) | Superseded by 0016 | self-host install bundle endpoint 자체가 사라져 결정 무효 |
| 0010 | 진단 규칙 기반 한정 | Accepted | 규칙 기반 right-sizing(USE Method, `recommendation.py`)을 web 인라인 계산으로 활용 — 본질 유효. LLM narrative 계층(0025)은 2026-06-14 폐기, 규칙 기반만 존속 |
| 0011 | Prometheus metrics endpoint | Withdrawn | `/metrics`·prometheus-fastapi-instrumentator 미적용으로 철회 (2026-06-08). 관측은 `LOG_FORMAT=json` 구조화 로그 단독 |
| 0012 | CI 산출물 = wheel + GitHub Release | Superseded by 0048 | Python wheel 단일 artifact (migrations·alembic.ini 동봉) + tag(v*) → GitHub Release 자동. Dockerfile·docker-compose는 dev 한정 (`docker-compose.prod.yml` 제거) |
| 0013 | release-please 자동화 | Superseded by 0028 | Conventional Commits 기반 자동 semver bump + Release PR + tag push. release-please(트렁크 전용)가 develop git-flow 와 구조 충돌 — 0028 Commitizen 으로 대체 |
| 0014 | Diagnostic 발행 책임 분리 | Superseded (2026-06-14) | AI 진단 폐기로 `DiagnosticSubmitter`(`diagnostic/submitter.py`) 제거. 보고서 발행은 `diagnostic_service.emit_report` 가 broker 미경유 DB enqueue 로 직접 수행 |
| 0015 | UI 임계값 단일 진실 (body data-attribute) | Accepted | `mappers._USAGE_*_PCT`/`_SWAP_DANGER_PCT` → `template_setup.env.globals["ui_thresholds"]` → `base.html` body data-attribute → JS `document.body.dataset`. P4 임계 분류 hardcoded drift 제거 |
| 0016 | self-host install bundle 제거 + ZDM 본체 패키지 직접 fetch (0008·0009 supersede) | Refined by 0019 | `web/routers/payloads.py` 삭제. task.install download.url 을 ZDM host + `ZDM_PACKAGE_PATH` 로 조립. sha256·size 는 env 단일 진실, 미설정 시 publish 차단(503). Linux 만 지원 |
| 0017 | Docker 이미지 CI 산출물 추가 (wheel 보조) | Superseded by 0048 | wheel + GHCR multi-arch image (`linux/amd64,arm64`) 양쪽 발행. 4 컴포넌트 단일 이미지 + ENTRYPOINT `python -m` + CMD override. cosign keyless + BuildKit SBOM. ADR 0012 refines |
| 0018 | dev 한정 ZDM mock endpoint (web 컨테이너 재활용) | Superseded by 0045 | `APP_ENV=dev` 시 web 컨테이너에 `GET {ZDM_PACKAGE_PATH}` mock router 등록 — in-memory 더미 tar.gz 반환 (install.sh = args echo + exit 0). install E2E 시연·자동화 검증. dev libvirt 파이프라인 제거(0045)로 mock 제거 |
| 0019 | task.install payload 에 install.type enum 도입 | Accepted | install.type = `shell` / `direct_exec` / `msi` enum 추가. Linux .tar.gz + bash 한정 가정을 enum 으로 확장. Windows install (.exe / .msi) 지원 준비. failure_reason 에 `unsupported_install_type` 추가. ADR 0020 의 os_family 기반 OS 별 dispatch. ADR 0016 refines |
| 0020 | inventory payload 에 os_family 필드 + server_inventory.os_family 컬럼 도입 | Accepted | OS family 식별 단일 진실. agent 가 자기 OS 명시 보고 (silent drift 위험 0). task.install dispatch (ADR 0019) 의 신호 출처. 호환 단계 (nullable + fallback "linux"). Linux agent minor bump 배포 완료 후 not-null tighten 별도 |
| 0021 | API URL prefix 단순화 (`/api/v1` → `/api`) | Accepted | URL versioning prefix 폐기. 모든 JSON API 는 `/api/...` 직접. B2B 내부 포털 + 외부 client 0 이라 versioning 가치 없음. routers.md 의 breaking change 절차 절 supersede |
| 0022 | 호스트 식별자 분리 (host_id 단일 식별자) | Superseded by 0027 | server_inventory 식별 3 분리 — id bigint PK (FK 대상) / host_id char(64) UNIQUE (agent 매칭, MAC+machine-id 합성 해시) / public_id UUID (URL 노출) / hostname display. MQ queue `agent.tasks.{host_id}`. ADR 0027 에서 host_id -> composite_id 단일 식별 + machine_id 표시 분리 (agent v4) 로 대체 |
| 0023 | diagnostic scheduler 폐기 (사용자 trigger 모델로 통합) | Superseded (2026-06-14) | cron 자동 발화 폐기 (0004 cron 부분 supersede). 이후 AI 진단(워커·LLM) 전면 폐기로 본 ADR 대상 자체 소멸 |
| 0024 | AI 진단 RAG 도입 (도메인 지식 phase) | Superseded by 0039 | pgvector + rag_documents + 도메인 지식 만 (본 phase). embedding = mxbai-embed-large-v1 (1024d) · 인덱스 HNSW · RAG_ENABLED False default · query 영어 통일 · ingest CLI. 운영 노트·peer = 보류. phase 1 infra 만 구축·미활성 끝에 0039 제거 |
| 0025 | LLM 단일 provider 통합 (ollama), mock 폐기 | Superseded (2026-06-14) | AI 진단(LLM narrative) 전면 폐기 — `OllamaLlmClient`·`OLLAMA_*` env·LLM 호출 계층 제거. 진단은 규칙 기반 right-sizing(0010)만 존속 |
| 0026 | dev 가상화 스택 Lima -> OrbStack 전환 | Superseded by 0037 | Lima -> OrbStack(macOS): `host.docker.internal`·`<name>.orb.local`·post-provision 흡수·lima yaml 삭제. homeserver Linux 이전으로 0037 libvirt 재전환에 supersede — OrbStack(macOS) 시기 역사 기록 |
| 0027 | composite_id 단일 식별 + machine_id 표시 분리 (agent v4) | Superseded by 0049 | agent v4 가 host_id -> machine_id(raw) + composite_id(SHA-256 hash) 분리. 엔진 식별 단일 키 = composite_id (ADR 0022 host_id 역할 전면 대체) — server_inventory UNIQUE·task 라우팅(`agent.tasks.{composite_id}`)·URL 매핑. machine_id 표시 전용(nullable). |
| 0028 | Commitizen 전환 (release-please 폐기) | Superseded by 0030 | Commitizen `cz bump` 이 버전을 repo 에 commit 하는 모델 — bump 커밋이 보호된 develop·main 직접 push 불가 + `bump:` 메시지 commit-msg hook 거부. ruleset+hook 과 구조 충돌. 0030 tag-derived 로 대체 |
| 0029 | OS-aware right-sizing 분류 (Windows swap 제외 + 부분 평가) | Superseded by 0052 | agent v4(0027) Windows 합류 후 OS-blind 분류 왜곡 정정. swap 은 Linux page-out 신호이나 Windows pagefile 은 baseline → `swap_saturation(os_family, swap_used)` helper 로 Windows swap 축 제외. saturation 축(load/iowait) OS 부재라 Windows 는 utilization 축만 분류 → `is_partial_evaluation`(부분 평가 마커). os_family None=Linux fallback 으로 회귀 0 |
| 0030 | tag-derived 버전 (hatch-vcs) | Superseded by 0057 | 버전을 repo 에 저장 안 함 — git tag(`v*`) 단일 진실, hatch-vcs 가 빌드 시 derive. bump 커밋 자체 제거로 보호 브랜치 push·commit-msg hook `bump:` 충돌 소멸. release = main 에 tag push. release notes = GitHub 자동. CHANGELOG 자동 갱신 중단. cz 폐기. 0028 supersede. |
| 0031 | OS EOL 운영신호: endoflife.date 스냅샷 카탈로그 | Refined by 0061 | OS 지원종료(EOL) 신호를 endoflife.date 스냅샷 카탈로그로 도입 — 보고서 운영신호(os_eol)·distro 필터 옵션 단일 진실 |
| 0032 | 서비스 분류: 단일 카탈로그 + 다중 신호 + 호스트 union | Accepted | 이름 substring 단일 신호 + 분산 카탈로그(5곳)를 `SERVICE_CATALOG` 단일 진실 + 다중 신호로 전환. per-unit 은 name->comm->port(귀속 포트에만). agent join key 부재(services 에 pid 없음) 제약상, 뱃지/role/환경분포는 `detect_listen_categories`(listen 소켓 직접 분류)와 이름 분류를 union — opaque Windows SCM 이름을 1433/`sqlservr` 로 구제. |
| 0033 | 루트 docker-compose 단일 파일 (dev + 퀵스타트) | Superseded by 0035 | `dev/docker-compose.yml` 제거, 루트 `docker-compose.yml` 하나가 dev 파이프라인·퀵스타트 겸용 (wheel 이미지·`.env` 단일). ADR 0012 5절 supersede. ADR 0035 base/override 분리 + 0036 퀵스타트 폐기에 supersede |
| 0034 | 환경 평균 활용률 capacity-weighted 전환 | Accepted | 서버 동등가중(1대=1표)을 자원 총량 가중(Σused/Σtotal)으로 전환. CPU `1-Σd_idle/Σd_total`·MEM/DISK `Σused/Σtotal`. `environment_utilization`(전체+selection server_ids 통일, end anchor)·`metric_trend`(추이 차트) 동일 산식. 빈 구간/서버별 기간 편차 분모에 자연 반영. 거대 VM 지배는 의도(자원 활용률 관점). 윈도우 14일 고정 유지(ADR 0003) |
| 0035 | compose base(prod) + override(dev) 분리 | Refined by 0046·0048·0059·0060 | 루트 `docker-compose.yml` 을 prod-safe base(build 키 없는 GHCR 이미지 pull·bind mount 없음·`PGDATA_HOST`/`MQ_DATA_HOST` 볼륨 바인딩·diagnostic-worker 포함)로 전환 = 빌드 없는 pull-and-run prod compose(릴리즈 첨부, infra B안). dev 편의는 `docker-compose.override.yml`(자동 머지)로 분리. Dockerfile 은 단일 유지(dev-prod parity). |
| 0036 | 퀵스타트 카테고리 폐기, dev/배포 2분류 | Refined by 0045·0046·0048 | 환경 모델을 dev/prod 2분류로 고정, "퀵스타트" 제3 카테고리 폐기. 루트 `.env.example` = 배포 템플릿(APP_ENV=prod·secret placeholder `changeme`로 fail-fast·배포 키 중심), dev 검증은 `dev/.env.example`. compose base/override 구조(0035) 존속, 소스 clone `docker compose up` 은 여전히 dev(기능 손실 0). ADR 0033·0035 퀵스타트 개념 정정 |
| 0037 | dev 가상화 OrbStack -> libvirt(KVM) 재전환 | Superseded by 0045 | dev host macOS -> Linux x86_64 homeserver 이전. OrbStack(macOS 전용) -> libvirt+qemu-kvm(virsh·virbr0 NAT). dev-up.sh libvirt 매트릭스(Linux 5+Windows 1). agent VM 을 OpenStack 공급으로 전환(0045)하며 dev libvirt 파이프라인 제거 |
| 0038 | release 에셋명 env.example (점 prefix 제거) | Superseded by 0058 | GitHub Release 가 점 prefix(`.env.example`)를 `default.env.example` 로 변환(download URL·asset name 모두) -> 에셋명·루트 배포 템플릿 파일명을 `env.example`(점 없이)로 rename. dev 전용 `dev/.env.example`·`agent.env.example`(release 미첨부)은 점 유지. ADR 0035 에셋명 점-prefix 부분 supersede |
| 0039 | RAG 제거 (0024 supersede) | Accepted | 미활성(`RAG_ENABLED=False`) RAG infra 전면 제거 — `rag/` 패키지·`rag_documents` 테이블·pgvector extension(drop revision `e2f4a6c8b0d3`)·embedding/retriever 추상·ingest CLI·`docs/rag-seed/`·config 7 필드. 진단 = 통계 집계 + 결정론 분류 + LLM narrative 단독. LLM(ADR 0025) 유지. 재도입은 새 ADR |
| 0040 | 비동기 보고서 발행 복원 (web job-claim 워커) | Amended by 0055 | 동기 즉시 succeeded 발행 -> 비동기. emit=`enqueue_report`(parent pending) 즉시 `?job=` 반환, web lifespan job-claim 워커(`FOR UPDATE SKIP LOCKED`)가 `build_report_result_for_job` 생성 -> succeeded/failed. GET pending/running 폴링(`report-poll.js`+`/status`). |

| 0041 | collected_at 수신 경계 보정 (시계오차, 양방향) | Superseded by 0050 | Windows 게스트 시계가 틀어진 메시지의 `collected_at` 을 수신 경계(`_correct_skewed_collected_at`, 멱등성 체크 직후)에서 `received_at` 으로 보정 — `abs(collected_at - now) > 5분`(미래·과거 양방향, 최초 future-only -> 정정 확장). `collected_at` 만 보정(boot_time/agent_started_at 미보정). D2 2단 약화는 1단 message_id dedup 으로 흡수(T17). 근본은 게스트 시각 동기 |

| 0042 | 서비스 카테고리 ingest 사전계산 + service_classifier 도메인 이전 | Accepted | 카테고리(web/db/cache/mq/container/monitor)를 inventory upsert 시 `compute_service_categories`(이름 분류 ∪ listen 소켓 분류)로 1회 산출해 `server_inventory.service_categories text[]`(마이그레이션 `a7c3e5f1b9d4` + GIN)에 저장. 목록·상세·리포트·필터가 저장값 소비 -> 화면 간 카테고리 집합 비대칭 0(T15 목록-상세 해소), 목록은 services JSONB·행별 classify 제거(경량). |

| 0043 | 보고서 메트릭 집계 continuous aggregate + counter_agg reset 통일 | Accepted | 카운터(CPU jiffies·disk/net bytes) 7일 집계를 매 요청 LAG 스캔에서 cagg 4개(`server_metrics_5m`·`server_disk_io_5m`·`server_net_io_5m`·`server_mount_usage_5m`, 5분 버킷, real-time agg)로 사전집계. |

| 0044 | composite_id 재연결 (재부팅 composite_id 변동 흡수) | Superseded by 0049 | OpenStack Windows VM 등 부팅마다 NIC MAC 재발급 -> composite_id(=sha256(machine_id+MAC)) 가 같은 VM 인데 달라져 중복 행. inventory upsert 시 composite_id 미등록이면 `_relink_rebooted_host`(machine_id+hostname, 후보 정확히 1개) 기존 행 composite_id 를 re-point — server_id·시계열·history 보존, 중복 0. MAC 은 부팅마다 바뀌어 매칭 미사용. |

| 0045 | dev 런타임 경계 확정: libvirt VM 파이프라인·ZDM mock 제거 | Refined by 0047 | 본 repo 는 엔진 런타임만 담당 — agent VM 은 OpenStack 공급(범위 밖). `dev/` libvirt 시연 파이프라인(dev-up/down·agent 크로스빌드·Windows autounattend)·dev ZDM mock(`dev_zdm_mock.py`·`zdm_resolver_host_override`) 제거. dev = `docker-compose.override.yml` 핫리로드만. local-ci.sh -> `scripts/`. dev env 카탈로그 = 루트 `env.dev.example`. |

| 0046 | prod 비밀번호 file-secret 채널 (compose overlay, 단일) | Refined by 0047·0059 | 단일 호스트 non-swarm prod 비번을 file-secret 채널 단일로. prod 전용 overlay `docker-compose.secrets.yml` — `./secrets/*`(600) -> `/run/secrets/*`. app=secrets_dir(코드 변경 0)·postgres/pgadmin=`*_FILE`·rabbitmq=entrypoint wrapper(3.13 _FILE 제거). password env null 중화. |

| 0047 | pgAdmin 제거 (repo 에서 완전 삭제) | Accepted | 운영 편의 DB GUI(pgAdmin4 wrapper)를 코드·설정·문서 잔재 0 으로 제거 — `docker/pgadmin/`·compose 3종 pgadmin 서비스·`release-pgadmin-image` job(GHCR `assessment-pgadmin` 발행 중단)·`PGADMIN_*` env·`pgadmin_password` secret. 서비스 6개(postgres·rabbitmq·redis·migrate·web·consumer)로 축소, 릴리즈 이미지 `assessment-engine` 단일 수렴. |

| 0048 | 엔진 배포(rollout)를 본 repo 로 통합 (compose 매체 + VM `deploy.sh`) | Accepted | ADR 0012·0017 supersede. artifact 게시에 그치던 배포를 rollout 까지 본 repo 소유로 재정의 — 별도 인프라 repo 없이 내부망 VM 단일 prod 에 배포. 배포 대상 VM 에서 `sudo deploy.sh vX.Y.Z` 실행(사람 실행=게이트): cosign verify -> 태그 compose raw fetch -> pull -> migration(init-container)+up -> `/health` gate -> 실패 시 `.last-good` rollback. |
| 0049 | 식별 단일 키를 agent_id 로 전환 (composite_id 감사용 강등) | Accepted | agent 가 첫 실행 시 생성·영구저장하는 불변 UUID `agent_id` 를 식별 단일 키로 승격. `server_inventory.agent_id` UNIQUE·MQ 큐/라우팅 `agent.tasks.{agent_id}`·수집 저장·server_id 조회·task 발행 모두 agent_id 기준. composite_id(SHA-256 machine_id+MAC)는 부팅마다 변동(OpenStack Windows VM NIC 재발급)이라 감사·표시용 nullable 강등(UNIQUE 해제). |
| 0050 | collected_at 수신 경계 보정 제거 (에이전트 UTC 정상 전제) | Accepted | ADR 0041 이 Windows 게스트 시계 불량을 흡수하려 도입한 `_correct_skewed_collected_at`(collected_at 을 received_at 으로 재작성)을 제거. collected_at 은 시계열 자연키라 수신시각 재작성이 D2 멱등성 2단(DB UNIQUE)을 비결정적으로 약화(T17). 에이전트 설치 서버가 UTC 정상 시각 발행을 전제하면 보정은 불필요·순손해라 제거 — collected_at 을 권위 소스로 신뢰. |

| 0051 | install task lifecycle (오프라인 advisory·deadline<->큐TTL 정합·reaper) | Amended by 0055 | task.install 발행-저장-회신 3 결정: (1) 오프라인은 발행 게이트가 아닌 비차단 advisory — store-and-forward 유지, `TaskCreated.target_online` 응답 + UI warn 토스트(informed consent) (2) `install_task_deadline_sec`(3600) 하나로 engine `tasks.deadline_at` == broker 큐 `x-message-ttl` 단일 창 — 엔진 timeout == 미배달 만료(zombie 지연 실행 0), agent 실행 예산 `install_timeout_sec`(payload) 는 별개 (3) web lifespan `lifespan_task_reaper` 가 `expire_all_overdue_tasks` 로 deadline 경과 pending 을 emit 무관 failure(timeout) 전역 전이(유령 pending 0). |
| 0052 | 자원 적정성 분류 원칙 재설계 (전제 기반 유도 + USE 5자원 + tier 근거) | Amended by 0056 | ADR 0029 supersede. 분류를 임계 나열이 아니라 전제에서 유도한다 — USE Method 5자원(cpu·memory·disk capacity·disk io·network) x 3축(utilization·saturation·errors) 격자에 tier 별 근거를 붙이고, 미측정은 미측정으로 남긴다. 실제 신호 배선은 0053 이 잇는다. |
| 0053 | v2 진단모델 Gate0 — recommendation.py v2 신호 확정 (USE 5자원 x 3축) | Accepted | ADR 0052 원칙을 v2 스키마 실제 신호에 배선. 판정은 근거 있는 고전 신호로만(run queue·paging_major·await·filesystem used/inode·drops/conntrack) — 전부 vendor/convention 임계. PSI 는 collect-now-classify-later(저장만, 판정 Deferred — 벤더 임계 부재 + 운영 데이터 필요 + 레거시/Windows 커버리지 낮음). |
| 0054 | 프로비저닝 어세스먼트 API 사이징 모델 (near-peak 메모리·per-mount 디스크·물리 집계) | Accepted | Gate0 위에서 `/api/assessment` 사이징 산출 확정 — Gate0 가 목표%만 정하고 남긴 "어떤 통계로 역산하나"를 채움. (1) 메모리 사이징 통계 = near-peak(5분 버킷 max p99.9), 목표 80% — 비탄력·OOM 위험이라 평균 p95 아닌 피크로 역산. CPU 는 p95·70% 유지 -> CPU(p95)/메모리(near-peak) 용도적합 비대칭. 분류(eval)는 양쪽 p95 유지(통계 분리). |

| 0055 | 전용 백그라운드 워커 컨테이너 분리 (web = HTTP 전담) | Accepted | ADR 0040(보고서 job-claim 워커)·0051(install reaper)의 프로세스 배치 개정 — 결정(DB 상태머신·비동기·reaper)은 유지, 실행 위치만 web lifespan -> 전용 프로세스 `assessment_engine.worker`. `worker/main.py` composition root 가 두 루프를 공유 stop_event 로 병행 구동(consumer 와 동일 asyncio-native SIGTERM graceful). |

| 0056 | 자원 부족 처방 인과 억제 폐기 (자원별 독립 처방으로 3개 소비처 통일) | Accepted | ADR 0052 "root 에만 처방, 하류 억제"가 보고서(`under_prescription`)·`/api/right-sizing`(`prescribed_under_kinds` 공유)엔 적용됐지만 `/api/assessment`(ADR 0054 4항, "1회성 산출이라 재평가 루프 없음, 억제는 과소로 흐름")엔 처음부터 미적용 — 같은 호스트가 소비처마다 다른 조치를 안내하는 상태였다. `prescribed_under_kinds`를 `_under_kinds`와 동치화(관측된 under 자원 전부, 인과 무관)해 3곳 통일. |
| 0057 | 파일 버전 + uv_build 백엔드 | Accepted | 버전 입력 지점을 `pyproject.toml` `version` 하나로 두고 git tag 는 릴리즈 성공 후 워크플로가 파생 생성 — 사람이 tag 를 입력하지 않아 파일·tag 불일치가 구조적으로 불가능하고 등가성 검증 단계가 사라진다. 빌드 백엔드를 `uv_build` 로 교체(버전 플러그인 불요), `migrations/`·`_alembic.ini` 를 패키지 안으로 이동해 `force-include` 제거. |
| 0058 | env 템플릿 파일명 점 prefix 복원 | Accepted | ADR 0038 이 점을 뺀 근거는 GitHub Release 가 leading-dot 에셋을 `default.` 접두로 변환하는 것이었다. ADR 0048 이 compose·env 첨부를 폐기해 그 경로가 사라졌고 `release.yml` 에 첨부 단계가 없다. 제약이 소멸한 뒤에도 이름만 남아 생태계 관례(`.env.example`)에서 벗어나 있어 되돌린다. |
| 0059 | compose 3파일 표준 배치 정렬 | Accepted | ADR 0035 가 base 를 prod-safe 로 둔 근거(릴리즈가 base 를 에셋으로 첨부)는 ADR 0048 의 첨부 폐기로 사라졌고 `deploy.sh` 는 base 와 overlay 를 함께 fetch 한다. base 를 공통 정의로 낮추고(환경 색·비밀번호 설정 제거) dev override 가 env 채널을, `docker-compose.prod.yml`(구 secrets.yml)이 file-secret 채널을 각자 채우게 한다. |
| 0060 | 컨테이너 실행 명령을 compose 단일 소스로 | Accepted | ADR 0035 가 세운 `ENTRYPOINT ["python","-m"]` + `__ENGINE_VERSION__` 치환 전제가 둘 다 소멸 — 치환 스텝은 ADR 0048 의 에셋 폐기와 함께 사라졌고 placeholder 는 실재하지 않는 태그를 가리키는 기본값으로 남았다. 이미지에서 ENTRYPOINT 를 걷고 `CMD []` 로 기본 실행을 차단, compose `command` 가 완결 명령(`python -m <module>`)을 넘긴다. |
| 0061 | OS 지원 단계를 경계 3개 기준 4상태로 판정 | Accepted | ADR 0031 refine. 경계를 벤더 용어가 아니라 무엇이 끊기는지로 정의 — support(기능 업데이트)·eol(무상 보안 패치)·extendedSupport(유상 보안 패치). 상태 4개(full·security_only·paid_only·ended)를 표시보다 잘게 유지해 화면이 각자 접어 쓴다(목록 필터 3분기·환경 KPI 4분기) — 표시 변경이 판정을 건드리지 않게. 카탈로그가 경계 셋을 다 싣고, 없는 경계는 그 구간 부재로 읽는다. |
| 0062 | 타입 검사 강도를 규칙 단위 래칫으로 올린다 | Accepted | 최종 상태 = `typeCheckingMode = "strict"`, include 는 src·scripts·tests, 명시 선언은 두 묶음(채택 안 하는 4개 none / strict 가 끄지만 위반 0 인 4개 error). 한 번에 켜지 않은 이유는 시작점이 4846건이라 소진 전까지 게이트를 꺼야 했고, 그러면 이미 확보한 지점도 함께 되돌아가기 때문. 위반 0 인 규칙만 error 로 못박는 래칫으로 경로별(scripts -> src -> tests) 소진했고 끝난 뒤 경로별 블록을 제거. 뿌리는 값이 아니라 선언이었다 |

트레이드오프 카탈로그는 ADR 형식과 맞지 않아 `docs/explanation/tradeoffs.md`로 분리.

## 새 ADR 작성

파일명 `NNNN-짧은-제목.md` (4자리 zero-padded). 본문 권장 섹션: Status / Context / Decision / Consequences. 작성 후 본 인덱스 표에 한 줄 추가.