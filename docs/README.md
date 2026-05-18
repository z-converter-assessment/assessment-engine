# 문서 인덱스

본 디렉토리는 프로젝트의 영구 문서. 단일 진실은 코드 + `.claude/CLAUDE.md` (원칙·금지 사항) + 본 디렉토리의 deep dive.

## 어떤 문서를 언제 보는가

| 상황 | 위치 |
|------|------|
| 시스템 한눈에 / 처음 진입 | 루트 `README.md` |
| 결정된 규약·금지 사항·계층 책임 | `.claude/CLAUDE.md` |
| 컴포넌트가 어떻게 동작하나 | `architecture/` |
| 본 repo dev 작업·코드 규약 (Docker·pipeline 검증·testing·conventions) | `development/` |
| 외부 인프라 첫 셋업 (Debian 12 multi-node 명령어 가이드) | `operations/quickstart.md` |
| 외부 인프라용 contract (deployment·env·prod·alembic·observability·release·github-setup) | `operations/` |
| 운영 산출물별 의의·근거 (보고서·진단·Install·Export 등) | `products/` |
| 왜 그렇게 결정했나 | `adr/` |
| 트레이드오프와 한계 (T1~T11) | `tradeoffs.md` |

## 디렉토리

```
docs/
├── README.md              ← 본 파일 (인덱스)
├── architecture/          컴포넌트별 deep dive (영구·갱신)
│   ├── agent.md           메시지 데이터 형식 (inventory / metrics / error / task.install / task.result)·포트 수집·디스크 필터링
│   ├── consumer.md        handler·main·멱등성·재시도·부가 시그널
│   ├── diagnostic.md      진단 워커·스케줄러·LLM 토글·diagnostic_jobs (ADR 0004 + 0010)
│   ├── rabbitmq.md        vhost·권한 모델·토폴로지·dev·prod 분기
│   ├── redis.md           키 설계·TTL·PUB/SUB·캐시 무효화·mget
│   ├── db/                models / dtos / repositories / timescaledb (4분할)
│   ├── web/               layering / routers / services / view-models / static-assets (5분할)
│   └── inventory-export.md  JSON Export 스키마·정제 원칙·자동화 도구 매핑
├── development/           본 repo 안 dev 작업·코드 규약 (영구·갱신)
│   ├── docker.md          Dockerfile·docker-compose dev 명세
│   ├── pipeline.md        E2E 파이프라인 검증 + Lima VM 매트릭스·합성 부하·provisioning·누적 사고 패턴 (macOS 한정)
│   ├── testing.md         pytest 단위·통합 테스트
│   └── conventions.md     본 repo 작업 규약 단일 — IDE·Hook(F1) + 자동화 변환 검증·누적 사고 패턴(F5)
├── operations/            외부 인프라가 활용할 contract (영구·갱신)
│   ├── quickstart.md      Debian 12 multi-node 첫 셋업 명령어 가이드 (외부 인프라 운영자 첫 진입점)
│   ├── release.md         release artifact 카탈로그·생성 trigger·무결성 검증·다운로드 (ADR 0012)
│   ├── deployment.md      외부 인프라가 release artifact 활용해 운영하는 단계별 가이드 (OS 독립)
│   ├── env.md             환경변수 키 카탈로그
│   ├── prod-contract.md   prod 환경변수·secret 채널·`_validate_prod_*`·운영 체크리스트
│   ├── alembic.md         DB schema 마이그레이션 contract
│   ├── observability.md   로그 레벨·외부 의존 실패 매트릭스·Prometheus `/metrics`·LOG_FORMAT toggle (F6·F7 부속)
│   └── github-setup.md    GitHub UI 활성 의무 카탈로그 (운영 시작 전 체크리스트)
├── products/              운영 산출물 ref (영구·갱신)
│   ├── dashboard.md               대시보드 의의·근거 (다른 산출물 navigation hub)
│   ├── customer-report.md         고객 보고서(양식 A) 의의·근거
│   ├── engineer-report.md         엔지니어 보고서(양식 B) 의의·근거
│   ├── environment-diagnostic.md  환경 진단(scope=environment) 의의·근거
│   ├── server-diagnostic.md       서버 진단(scope=server) 의의·근거
│   ├── json-export.md             JSON Export 의의·근거 (자동화 도구 입력)
│   └── install-task.md            Install task 의의·근거 (원격 설치 워크플로)
├── adr/                   Architecture Decision Records (영구·불변, 0001~0013)
└── tradeoffs.md           의식적 설계 선택과 한계 (T1~T11) — 카탈로그
```

## 범위

본 repo는 기능 개발에 필요한 환경 구성만 다룬다 (CLAUDE.md #A0). 배포 인프라(IaC — Terraform·Ansible 등)는 본 repo 범위 밖. 단 prod 배포 시 외부 인프라가 활용할 수 있는 정석 contract(환경변수·SecretStr 검증·prod 분기·release artifact·CI 자동화 등)는 본 repo에서 유지.

## 라이프사이클 규약

- `architecture/`·`development/`·`operations/`·`products/` — 영구·갱신. 코드 변경 시 동시 업데이트(#F9 영향도 체크리스트).
- `adr/` — 영구·불변. 결정 변경 시 새 ADR 추가, 이전은 `Status: Superseded` 또는 `Withdrawn`.