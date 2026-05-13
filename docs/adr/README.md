# Architecture Decision Records

영구·불변 의사결정 기록. 결정 변경 시 새 ADR 추가 + 이전 ADR `Status: Superseded`. 덮어쓰기 금지.

## 인덱스

| 번호 | 제목 | Status | 요약 |
|------|------|--------|------|
| 0001 | Redis fail-open 전환 | Accepted | 멱등성·캐시·부수 작업의 Redis 의존을 fail-open — DB UNIQUE 2단이 정확성 보장 |
| 0002 | Task RPC piggyback vs polling | Accepted | 운영자 작업 명령을 `server.metrics` reply 채널에 piggyback — polling endpoint·큐 신설 0 |
| 0003 | AI/LLM 활용 로드맵 | Accepted | Phase 2~3 — USE Method 임계값·방법론·LLM 모델 선택 |
| 0004 | AI 진단 워커 아키텍처 | Accepted | 워커·스케줄러·diagnostic_jobs·LLM 토글 (Phase 2 실행 인프라) |
| 0005 | DB Schema 관리 표준화 | Accepted | Alembic 단일 진실, migrate init-container, `alembic check` CI |
| 0006 | OpenStack 분산 staging 배포 | Proposed | 4 VM 토폴로지(bastion + DB + MW + 앱) — 예상 시나리오, 실 도입 시 정정 |

트레이드오프 카탈로그(T1~T11)는 ADR 형식과 맞지 않아 `docs/tradeoffs.md`로 분리.

## 새 ADR 작성

파일명 `NNNN-짧은-제목.md` (4자리 zero-padded). 본문 권장 섹션: Status / Context / Decision / Consequences. 작성 후 본 인덱스 표에 한 줄 추가.