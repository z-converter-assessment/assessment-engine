# 세션 핸드오프 (2026-05-29)

> 임시 문서 (docs/temp). 컨텍스트 clear 후 새 세션이 읽는 진입점. "현재 상태"의 단일 진실은 각 영역의
> 영구 docs (아래 포인터). 본 문서는 이번 세션이 무엇을 바꿨고·무엇이 미완이며·다음에 뭘 할지의 요약.
> 브랜치: chore/general-edits. commit 여부 미정 (working tree).

---

## 출발점

운영 컨텍스트 전제 — 고객사 내부망 500 VM, 사측이 망 안에 engine 서버 구성, 각 VM 에 agent, 2주~1달
수집 후 평가·보고서. Windows agent 페이로드와 Linux 가 달라 UI/보고서 부정합 → agent.md 계약 정합부터
시작해 dev 파이프라인에 Windows 환경 추가, 그 후 환경요약 UI 개선까지 이어진 세션.

---

## 1. agent 계약 정합 (Windows/Linux wire drift)

- 변경: `docs/architecture/agent.md` (drift 전면 반영), `consumer/schemas.py` (`MessageBase` 에 os_family
  정식 등록, `ErrorInput.error_message` min_length 제거), `consumer/handlers/error.py` (빈 문자열 `(empty)` fallback)
- 단일 진실: `docs/architecture/agent.md`
- 핵심: Windows nullable 필드(load_avg/buffers/cached NULL, pagefile swap 의미), services.sub OS 차이,
  disks major Windows=0, listen_ports pid 의미, error_message/failed_component reject 위험
- 미완 (agent repo 측, engine 무관): Windows `failed_component` NULL default `"agent"` (Literal reject),
  `agent_started_at` 빈 문자열 발행 가능 → agent repo PR 항목 (운영 평시 미트리거)

## 2. dev 파이프라인 — Windows 하이브리드 (OrbStack + UTM)

4 VM 매트릭스 (1 VM = 2 서비스, service_classifier 6 카테고리 커버):
| VM | 가상화 | 서비스 | attention |
|----|--------|--------|-----------|
| app-server-01 | OrbStack debian:12 | nginx + rabbitmq | agent_unstable |
| data-server-01 | OrbStack rocky:9 | postgresql + zabbix-agent | capacity_warnings (swap) |
| edge-server-01 | OrbStack debian:12 | docker + memcached | gap_warnings (3회 발행 후 poweroff) |
| win-server-01 | UTM Win11 ARM | IIS + redis(tporadowski) | (windows-vm.md) |

- 변경: `dev/docker-compose.yml` (ollama 제거 — AI 진단 발행 시 LLM 호출 실패 의도 재현, 진단 워커
  로직 무수정), `dev/pipeline-up.sh` (다중 서비스 dispatch, offline-demo, build APP_VERSION 주입),
  `dev/win-pipeline.sh` (신규 — Windows 부분 자동화), `dev/Dockerfile` (#48 회귀 수정: hatch-vcs
  버전을 `SETUPTOOLS_SCM_PRETEND_VERSION` build arg 로 주입)
- 신규 문서: `docs/development/windows-vm.md` (UTM Win11 ARM 전체 절차)
- 단일 진실: `docs/development/pipeline.md` (Linux), `docs/development/windows-vm.md` (Windows)
- 검증됨: `./dev/pipeline-up.sh` 실동작 → 4 VM 전부 DB 등록 (Windows os_family=windows 확인).
  Windows agent 는 macOS cross-compile (mingw-w64 + cmake `CMAKE_SYSTEM_NAME=Windows` wrapper) 로 빌드
  — ARM Windows MSYS2 installer 불가(알려진 이슈) 우회. win VM 시간 16h 어긋남 → w32time 동기화.
- 미검증/주의: Windows agent hostname override 무시 (실 머신명 발행 — agent 측), win VM NTP 서버
  도달 실패(RTC 보정은 됨, 재부팅 시 재drift 가능), win-pipeline 재실행 멱등성

## 3. 환경 요약 (대시보드 /servers/) 5개 개선

- OS별 서버 수 (os_distribution), 총 메모리/디스크 TB 자동 스케일 (disksize 필터), 역할=전체 서비스
  카테고리 (대표 1개 infer_role → classify 전체), 평균 활용률 SQL 엄밀화 (CPU/메모리 flat 시점평균 →
  서버별 평균 후 서버간 평균, 디스크와 동일 서버 1대=1표), 색 검정 통일 + 소제목 h3 위계
- 변경: `mappers/attention.py` (build_environment_overview), `view_models/attention.py`
  (os_distribution 필드), `templates/servers/list.html`, `repositories/query/report.py`
  (environment_utilization SQL), `mappers/shared.py`
- 단일 진실: `docs/architecture/web/services.md`, `view-models.md`
- 사용자 피드백: 폰트 위계·온라인/오프라인 검정 통일 만족 (환경요약 시범, 전역 확산은 차차)

## 4. 활용률 도넛 색 + 운영신호 검증

- 도넛: HSL 그라데이션(초록→빨강) 제거 → 푸른 단색 `_UTIL_COLOR_GAUGE="#3b82f6"`. 활용률은 게이지
  길이로, 색은 값 무관 (위험도 색은 Right-sizing 도넛이 별도)
- 운영신호 검증 결과: agent 재시작 빈번 = 정확 (최근 1h 슬라이딩, 임계 3회), 통신 끊김 = 정확
  (5분 끊김 + 24h 윈도우)

## 5. OS EOL 재설계 (endoflife.date 스냅샷) — ADR 0031

- 수동 EOL dict → endoflife.date 스냅샷 정적 카탈로그. 런타임 외부 의존 0 (폐쇄망), Linux 11 distro +
  Windows Server build 전 버전 커버
- 변경: `scripts/snapshot_os_eol.py` (신규 스냅샷 도구 — 빌드/릴리스 maintenance, dev 아님), `os_eol_catalog.json` (신규 카탈로그,
  git commit, wheel 자동 포함), `mappers/shared.py` (resolve_os_eol 카탈로그 기반 재작성),
  `mappers/attention.py`·`report.py` (resolve_os_eol 공용), `docs/adr/0031-os-eol-endoflife-snapshot.md`
- 핵심: Windows = kernel build ↔ windows-server latest build 매칭 (운영=Server 가정). Linux = os_id→
  product slug, os_version→cycle. EOL 경과 한정 발화 (미래 EOL 미발화). 미등록 OS 침묵 (의식적 한계)
- 갱신: `python3 scripts/snapshot_os_eol.py <카탈로그경로>` 재실행 + commit (분기 권장, 인터넷 환경)

---

## 미완 / 다음 작업 후보

- 라이트사이징 분류 Windows 대응 — 보류 명시 (윈도우 추가로 swap/saturation 축 재검토 필요, 별도 작업)
- `service_classifier` #E7 — Windows native 서비스 분류 (IIS `w3svc`/MSSQL `mssqlserver`) 카탈로그 확장.
  현재 IIS 는 분류 안 됨 (redis 는 서비스명 redis 라 cache 분류됨)
- `build_report_summary_bullets` 의 `today` 가 caller(query_service) 미주입 → default `now()`. 정석은
  주입(테스트 결정성). 코드 위생 (기능 무관)
- 미등록 OS EOL "확인 불가" 명시 (침묵 → false negative 인지) — 선택
- 환경요약 폰트 위계·색 통일 전역 확산 (현재 환경요약 섹션만 시범)

## 환경 상태 (clear 시점)

- engine docker 기동 중 (web localhost:8000, RabbitMQ 15672, 4 VM 등록). ollama 제거 상태 (AI 진단
  발행 시 LLM 실패가 의도된 동작)
- OrbStack VM: app/data 실행, edge 는 offline-demo 로 stopped (재기동 `orb start edge-server-01`)
- UTM win-server-01: 시간 동기화됨, agent 서비스 등록·발행

## 변경 파일 (working tree, commit 미정)

수정 21: CLAUDE.md / dev(.env.example·Dockerfile·README·docker-compose·pipeline-up·pipeline-down) /
docs(README·agent.md·web/services·web/view-models·development/pipeline) / src(consumer/handlers/error·
consumer/schemas·db/.../report·mappers/attention·mappers/report·mappers/shared·query_service·
templates/servers/list.html·view_models/attention)
신규 5: scripts/snapshot_os_eol.py / dev/win-pipeline.sh / docs/adr/0031 / docs/development/windows-vm.md /
mappers/os_eol_catalog.json
