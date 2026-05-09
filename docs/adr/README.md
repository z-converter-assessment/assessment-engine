# Architecture Decision Records

영구·불변 의사결정 기록. 결정이 바뀌면 새 ADR 추가 + 이전 ADR `Status: Superseded`로 표기. 덮어쓰기 금지.

## 인덱스

| 번호 | 제목 | Status | 요약 |
|------|------|--------|------|
| [0001](0001-redis-decoupling.md) | Redis fail-open 전환 | Accepted | 멱등성·캐시·부수 작업의 Redis 의존을 fail-open으로 분리 — DB UNIQUE 2단이 정확성 보장 |

## 보조 문서

| 파일 | 내용 |
|------|------|
| [tradeoffs.md](tradeoffs.md) | 의식적 설계 선택과 한계 (T1~T11). 단일 결정 단위가 아니라 누적 트레이드오프 카탈로그라 별도 파일 |

## 새 ADR 작성 형식

파일명: `NNNN-짧은-제목.md` (4자리 zero-padded 번호)

본문 권장 섹션:
- Status — Proposed / Accepted / Superseded by NNNN
- Context — 왜 결정이 필요했나
- Decision — 무엇을 결정했나
- Consequences — 결과·트레이드오프

작성 후 본 인덱스 표에 한 줄 추가.