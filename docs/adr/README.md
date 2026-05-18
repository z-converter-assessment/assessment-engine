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
| 0008 | dev engine HTTPS endpoint (전체 통합) + SAN 동적화 | Superseded by 0009 | self-signed CA 분배 부담 + agent 측 HTTPS-only 정책 한계로 dev 운영 부담 누적, plain HTTP 복귀 |
| 0009 | dev plain HTTP 복귀 (0008 supersede) | Accepted | engine dev plain HTTP. ZConverter Install success 경로는 agent 측 호환성 작업(WORKER_ALLOW_HTTP 또는 nginx ingress) 후 활성화 |
| 0010 | 진단 규칙 기반 한정 | Accepted | "AI 진단" 명칭 제거 (scope에 따라 환경/서버 진단). LLM 분기 보류 (mock default·ollama 미구현 유지). ADR 0003·0004 정정 |
| 0011 | Prometheus metrics endpoint | Accepted | `/metrics` 노출 (web 한정) — prometheus-fastapi-instrumentator. 인프라가 자유롭게 Prometheus stack 결정 |
| 0012 | CI 산출물 = wheel + GitHub Release | Accepted | Python wheel 단일 artifact (migrations·alembic.ini 동봉) + tag(v*) → GitHub Release 자동. Dockerfile·docker-compose는 dev 한정 (`docker-compose.prod.yml` 제거) |
| 0013 | release-please 자동화 | Accepted | Conventional Commits 기반 자동 semver bump + Release PR + tag push. main 직접 commit·tag 수동 작성 없음 (PR 강제 + branch protection 정합) |
| 0014 | Diagnostic 발행 책임 분리 | Accepted | `DiagnosticSubmitter` (`diagnostic/submitter.py`) 신규 — scheduler 노드 `web.services` 의존 끊김. web service 는 호환 re-export + 조회/기록 유지 |
| 0015 | UI 임계값 단일 진실 (body data-attribute) | Accepted | `mappers._USAGE_*_PCT`/`_SWAP_DANGER_PCT` → `template_setup.env.globals["ui_thresholds"]` → `base.html` body data-attribute → JS `document.body.dataset`. P4 임계 분류 hardcoded drift 제거 |

트레이드오프 카탈로그(T1~T13)는 ADR 형식과 맞지 않아 `docs/tradeoffs.md`로 분리.

## 새 ADR 작성

파일명 `NNNN-짧은-제목.md` (4자리 zero-padded). 본문 권장 섹션: Status / Context / Decision / Consequences. 작성 후 본 인덱스 표에 한 줄 추가.