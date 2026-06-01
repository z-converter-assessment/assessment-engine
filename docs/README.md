# 문서 인덱스

본 디렉토리는 프로젝트의 영구 문서. 단일 진실은 코드 + `.claude/CLAUDE.md` (원칙·금지 사항) + 본 디렉토리의 deep dive.

## 어떤 문서를 언제 보는가

| 상황 | 위치 |
|------|------|
| 시스템 한눈에 / 처음 진입 | 루트 `README.md` |
| 결정된 규약·금지 사항·계층 책임 | `.claude/CLAUDE.md` |
| 컴포넌트가 어떻게 동작하나 | `architecture/` |
| 본 repo dev 작업·코드 규약 (Docker·dependencies·pipeline 검증·testing·conventions·wrap-up) | `development/` |
| 기능 개발 마무리 5단계 표준 워크플로 | `development/wrap-up.md` |
| 외부 인프라용 contract (deployment·env·alembic·observability·release) | `operations/` |
| 본 repo CI · release(Commitizen) · branch protection 활성 (GitHub UI) | `development/github-setup.md` |
| 운영 산출물별 의의·근거 (보고서·진단·Install·Export 등) | `products/` |
| RAG 도메인 지식 sample (ADR 0024 ingest 본질 자료) | `rag-seed/` |
| 왜 그렇게 결정했나 | `adr/` |
| 트레이드오프와 한계 (T1~T14) | `tradeoffs.md` |

## 디렉토리

```
docs/
├── README.md              ← 본 파일 (인덱스)
├── architecture/          컴포넌트별 deep dive (영구·갱신)
│   ├── agent.md           메시지 데이터 형식 (inventory / metrics / error / task.install / task.result)·포트 수집·디스크 필터링
│   ├── consumer.md        handler·main·멱등성·재시도·부가 시그널
│   ├── diagnostic.md      진단 워커·LLM (ollama 단일, ADR 0025)·RAG infra (ADR 0024)·diagnostic_jobs·rag_documents
│   ├── rabbitmq.md        vhost·권한 모델·토폴로지·dev·prod 분기
│   ├── redis.md           키 설계·TTL·PUB/SUB·캐시 무효화·mget
│   ├── right-sizing.md    right-sizing 분류 기준·임계 근거 (USE Method·AWS/Azure/GCP advisor)·OS 분기·한계
│   ├── db/                models / dtos / repositories / timescaledb (4분할)
│   └── web/               layering / routers / services / view-models / static-assets / export-schema (6분할 — JSON Export 응답 스키마 포함)
├── development/           본 repo 안 dev 작업·코드 규약 (영구·갱신)
│   ├── docker.md          Dockerfile·루트 docker-compose(dev+퀵스타트 단일) 명세
│   ├── dependencies.md    pyproject.toml + uv.lock 관리·운영자 수동 bump·CI drift 검증
│   ├── pipeline.md        E2E 파이프라인 검증 + libvirt Linux 5 VM 매트릭스·합성 부하·provisioning (Linux x86_64)
│   ├── windows-vm.md      Windows agent 검증 — libvirt Win Server 2022 autounattend VM (win-server-01, opt-in)
│   ├── testing.md         pytest 단위·통합 테스트
│   ├── conventions.md     본 repo 작업 규약 단일 — IDE·Hook(F1) + 자동화 변환 검증·누적 사고 패턴(F5)
│   ├── wrap-up.md         기능 개발 마무리 5단계 표준 워크플로 — 문서 정합·코드 리뷰·테스트·README·CLAUDE.md (skill: /wrap-up)
│   └── github-setup.md    GitHub UI 활성 의무 카탈로그 — CI·release(Commitizen)·branch protection (본 repo CI 책임자 자료)
├── operations/            외부 인프라가 활용할 contract (영구·갱신)
│   ├── release.md         release artifact 카탈로그·생성 trigger(Commitizen)·무결성 검증·다운로드 (ADR 0012·0028)
│   ├── deployment.md      외부 인프라가 release artifact 활용해 운영하는 단계별 가이드 (OS·도구 독립, multi-node 분리·트러블슈팅·인프라 레포 자동화 포함)
│   ├── env.md             환경변수 관리 단일 진실 — 정책·매트릭스·secret 채널·전체 키 카탈로그·운영 체크리스트
│   ├── alembic.md         DB schema 마이그레이션 contract
│   └── observability.md   로그 레벨·외부 의존 실패 매트릭스·Prometheus `/metrics`·LOG_FORMAT toggle + 확장 트리거 (Request ID 분산 trace)
├── products/              운영 산출물 의의·근거 (영구·갱신)
│   ├── dashboard.md               대시보드 의의·근거 (다른 산출물 navigation hub)
│   ├── environment-report.md     환경 보고서 + 환경 진단 통합 (scope=environment) — view=customer/engineer 분기
│   ├── server-report.md          서버 보고서 + 서버 진단 통합 (scope=server) — view=customer/engineer 분기
│   ├── json-export.md             JSON Export 의의·근거 (자동화 도구 입력)
│   └── install-task.md            Install task 의의·근거 (원격 설치 워크플로)
├── rag-seed/              RAG 도메인 지식 sample (자체 작성, license 의무 0, ADR 0024)
│   ├── README.md                  ingest 가이드 + source_type 카탈로그
│   ├── use-method.md              USE Method 본질 요약 (Utilization · Saturation · Errors)
│   ├── right-sizing-thresholds.md AWS Compute Optimizer + Azure Advisor 임계 catalog
│   └── classification-rules.md    본 엔진 7 category 분류 규칙
├── adr/                   Architecture Decision Records (영구·불변, 0001~0031)
└── tradeoffs.md           의식적 설계 선택과 한계 (T1~T14) — 카탈로그
```

## 범위

본 repo는 기능 개발에 필요한 환경 구성만 다룬다 (CLAUDE.md #A0). 배포 인프라(IaC — Terraform·Ansible 등)는 본 repo 범위 밖. 단 prod 배포 시 외부 인프라가 활용할 수 있는 정석 contract(환경변수·SecretStr 검증·prod 분기·release artifact·CI 자동화 등)는 본 repo에서 유지.

## 라이프사이클 규약

- `architecture/`·`development/`·`operations/`·`products/` — 영구·갱신. 코드 변경 시 동시 업데이트(#F9 영향도 체크리스트).
- `adr/` — 영구·불변. 결정 변경 시 새 ADR 추가, 이전은 `Status: Superseded` 또는 `Withdrawn`.