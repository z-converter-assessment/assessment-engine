# 문서 인덱스

본 디렉토리는 프로젝트의 영구 문서. 단일 진실은 코드 + `.claude/CLAUDE.md` (원칙·금지 사항) + 본 디렉토리의 deep dive.

## 어떤 문서를 언제 보는가

| 상황 | 위치 |
|------|------|
| 시스템 한눈에 / 처음 진입 | 루트 `README.md` |
| 결정된 규약·금지 사항·계층 책임 | `.claude/CLAUDE.md` |
| 컴포넌트가 어떻게 동작하나 | `architecture/` |
| 어떻게 띄우고·검증하나 (Docker·Lima·env·testing) | `operations/` |
| 왜 그렇게 결정했나 | `adr/` |
| 트레이드오프와 한계 (T1~T11) | `tradeoffs.md` |
| 스코프 초과 예상 시나리오 (OpenStack 등) | `operations/scenarios/` |

## 디렉토리

```
docs/
├── README.md              ← 본 파일 (인덱스)
├── architecture/          컴포넌트별 deep dive (영구·갱신)
│   ├── agent.md           메시지 데이터 형식 (inventory / metrics / error / task.install / task.result)·포트 수집·디스크 필터링
│   ├── consumer.md        handler·main·멱등성·재시도·부가 시그널
│   ├── diagnostic.md      AI 진단 워커·스케줄러·LLM 토글·diagnostic_jobs (ADR 0004)
│   ├── rabbitmq.md        vhost·권한 모델·토폴로지·dev·prod 분기
│   ├── redis.md           키 설계·TTL·PUB/SUB·캐시 무효화·mget
│   ├── db/                models / dtos / repositories / timescaledb (4분할)
│   ├── web/               layering / routers / services / view-models / static-assets (5분할)
│   ├── deliverables.md    산출물 4종 워크플로 (서버 발견·Install·JSON Export·보고서)
│   └── inventory-export.md  JSON Export v3 스키마·정제 원칙·자동화 도구 매핑
├── operations/            운영·환경·검증 (영구·갱신, dev 집중 범위)
│   ├── alembic.md         DB schema 마이그레이션 절차
│   ├── automation-conventions.md  자동화 변환 책임 분담 매뉴얼 + 누적 사고 패턴
│   ├── conventions.md     IDE 경고 분류 + Hook 강제 채널 카탈로그 (F1 부속)
│   ├── dev-prod.md        환경변수·인프라 정책·secret 채널·dev/prod 분리
│   ├── docker.md          Dockerfile·docker-compose 단일 진실
│   ├── env.md             환경변수 키 카탈로그
│   ├── lima.md            dev 시연·파이프라인 검증 7 VM 단일 진실
│   ├── observability.md   Request/Correlation ID 분산 trace (F7 부속, 현재 미적용)
│   ├── pipeline.md        E2E 파이프라인 검증 운영자 절차
│   ├── testing.md         pytest 단위·통합 테스트
│   └── scenarios/         스코프 초과 예상 시나리오 (OpenStack 등)
├── adr/                   Architecture Decision Records (영구·불변)
└── tradeoffs.md           의식적 설계 선택과 한계 (T1~T11) — 카탈로그
```

## 라이프사이클 규약

- `architecture/`·`operations/` — 영구·갱신. 코드 변경 시 동시 업데이트(#F9 영향도 체크리스트).
- `adr/` — 영구·불변. 결정 변경 시 새 ADR 추가, 이전은 `Status: Superseded`.
- `operations/scenarios/` — 예상 시나리오. 실 도입 시점에 ADR 정정 의무.