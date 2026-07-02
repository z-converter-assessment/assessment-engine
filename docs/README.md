# 문서 인덱스

본 디렉토리는 프로젝트의 영구 문서. 단일 진실은 코드 + `.claude/CLAUDE.md` (원칙·금지 사항) + 본 디렉토리의 deep dive.

## 어떤 문서를 언제 보는가

| 상황 | 위치 |
|------|------|
| 시스템 한눈에 / 처음 진입 | 루트 `README.md` |
| 결정된 규약·금지 사항·계층 책임 | `.claude/CLAUDE.md` |
| 컴포넌트가 어떻게 동작하나 | `architecture/` |
| 본 repo dev 작업·코드 규약 (Docker·dependencies·testing·conventions·wrap-up·github-setup) | `development/` |
| 기능 개발 마무리 5단계 표준 워크플로 | `development/wrap-up.md` |
| 외부 인프라용 contract (deployment·env·alembic·observability·release) | `operations/` |
| 본 repo CI · release(tag push) · branch protection 활성 (GitHub UI) | `development/github-setup.md` |
| 운영 산출물별 의의·근거 (보고서·Install·Export 등) | `products/` |
| 왜 그렇게 결정했나 | `adr/` |
| 트레이드오프와 한계 (T1~T16) | `tradeoffs.md` |

## 디렉토리

```
docs/
├── README.md              ← 본 파일 (인덱스)
├── architecture/          컴포넌트별 deep dive (영구·갱신)
│   ├── agent.md           메시지 데이터 형식 (inventory / metrics / error / task.install / task.result)·포트 수집·디스크 필터링
│   ├── consumer.md        handler·main·멱등성·재시도·부가 시그널
│   ├── rabbitmq.md        vhost·권한 모델·토폴로지·dev·prod 분기
│   ├── redis.md           키 설계·TTL·PUB/SUB·캐시 무효화·mget
│   ├── right-sizing.md    right-sizing 분류 기준·임계 근거 (USE Method·AWS/Azure/GCP advisor)·OS 분기·한계
│   ├── db/                models / dtos / repositories / timescaledb (4분할)
│   └── web/               layering / routers / services / view-models / static-assets / export-schema (6분할 — JSON Export 응답 스키마 포함)
├── development/           본 repo 안 dev 작업·코드 규약 (영구·갱신)
│   ├── docker.md          Dockerfile·루트 docker-compose(prod base + dev override) 명세
│   ├── dependencies.md    pyproject.toml + uv.lock 관리·운영자 수동 bump·CI drift 검증
│   ├── testing.md         pytest 단위·통합 테스트
│   ├── conventions.md     본 repo 작업 규약 단일 — IDE·Hook(F1) + 자동화 변환 검증·누적 사고 패턴(F5)
│   ├── wrap-up.md         기능 개발 마무리 5단계 표준 워크플로 — 문서 정합·코드 리뷰·테스트·README·CLAUDE.md (skill: /wrap-up)
│   └── github-setup.md    GitHub UI 활성 의무 카탈로그 — CI·release(tag push)·배포(runner·Environment)·branch protection
├── operations/            배포·운영 contract (영구·갱신)
│   ├── release.md         release artifact(서명·SBOM·provenance OCI 이미지) 카탈로그·생성 trigger(tag push)·검증 (ADR 0048·0030)
│   ├── deployment.md      bootstrap + rollout(deploy.sh) 배포 가이드 (트러블슈팅 포함, ADR 0048)
│   ├── env.md             환경변수 관리 단일 진실 — 정책·매트릭스·secret 채널·전체 키 카탈로그·운영 체크리스트
│   ├── alembic.md         DB schema 마이그레이션 contract
│   └── observability.md   로그 레벨·외부 의존 실패 매트릭스·LOG_FORMAT toggle + 확장 트리거 (Request ID 분산 trace)
├── products/              운영 산출물 의의·근거 (영구·갱신)
│   ├── dashboard.md               대시보드 의의·근거 (다른 산출물 navigation hub)
│   ├── environment-report.md     환경 보고서 + 환경 진단 통합 (scope=environment) — view=customer/engineer 분기
│   ├── server-report.md          서버 보고서 + 서버 진단 통합 (scope=server) — view=customer/engineer 분기
│   ├── json-export.md             JSON Export 의의·근거 (자동화 도구 입력)
│   └── install-task.md            Install task 의의·근거 (원격 설치 워크플로)
├── adr/                   Architecture Decision Records (영구·불변, 0001~0039)
└── tradeoffs.md           의식적 설계 선택과 한계 (T1~T16) — 카탈로그
```

## 범위

본 repo는 엔진 애플리케이션 + docker compose 배포 + 엔진 rollout(`deploy.sh`, VM 에서 실행)까지 다룬다 (CLAUDE.md #A0, ADR 0048). VM provisioning(IaC — VM 생성·OS 설정)은 별도 준비 VM 전제 — docker·cosign·deploy.sh 설치는 1회성 `bootstrap.sh`.

## 라이프사이클 규약

- `architecture/`·`development/`·`operations/`·`products/` — 영구·갱신. 코드 변경 시 동시 업데이트(#F9 영향도 체크리스트).
- `adr/` — 영구·불변. 결정 변경 시 새 ADR 추가, 이전은 `Status: Superseded` 또는 `Withdrawn`.