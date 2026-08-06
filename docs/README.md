# 문서 관리 계약

본 파일은 이 저장소 문서를 어떻게 쓰고 관리하는지의 단일 진실. 문서를 추가·수정하기 전에 아래 4원칙과 지도를 따른다.

문서는 목적 넷으로 가른다 — 사람이 문서를 찾는 이유가 넷이라서: 지금 어떻게 도나(reference) / 어떻게 하나(guides) / 왜 이렇게 설계했나(explanation) / 왜 바꿨나(decisions).

명령은 문서가 아니라 `Makefile` 이 갖는다 (`make help`). 문서에는 왜 그 명령을 쓰는지와 옵션·변형·맥락만 둔다. 저장소에서 작업하는 순서는 루트 `CONTRIBUTING.md` 가 진입점이다.

## 4원칙

1. 사실 하나 = 문서 하나. 같은 사실을 두 곳에 쓰지 않는다. 다른 문서에 이미 있으면 그 문서를 가리키고(pointer) 재서술하지 않는다.
2. 현재 상태만 선언. "B다"라고 쓴다. "A였다가 B가 됐다"·"옛 X 폐기"·"~에서 전환" 같은 이력 서사는 쓰지 않는다 (`decisions/`만 예외).
3. 문서 하나 = 목적 하나. reference에 절차를 섞지 않고, explanation에 how-to를 넣지 않는다.
4. `decisions/`(adr·rfc)는 이력 아카이브지 의존 대상이 아니다. 라이브 문서(reference·guides·explanation·CLAUDE.md)는 결정 번호를 전제로 참조하지 않는다 — 필요한 사실은 라이브 문서 안에 인라인으로 있어야 한다.

## 계층 역할

| 계층 | 역할 | 성격 |
|------|------|------|
| `Makefile` | 개발 명령 카탈로그. 이름과 실행만, 설명은 한 줄 | 명령 단일 진실 |
| `CONTRIBUTING.md` | 작업 순서와 각 단계의 문서 위치. 내용은 갖지 않고 가리킨다 | 개발 진입점 |
| `.claude/CLAUDE.md` | AI 에이전트 운영 규칙 — 불변식·금지·지도. 안 깨려면 알아야 할 것만 | 얇게·매 세션 로드 |
| `docs/reference/` | 지금 어떻게 도나 — subsystem 동작 + `contracts/` 얼어붙은 계약 | 현재 상태 선언 |
| `docs/guides/` | 어떻게 하나 — 작업 절차 | 현재 상태 선언 |
| `docs/explanation/` | 왜 이렇게 설계했나 — 설계 한계·산출물 존재 의의 | 현재 상태 선언 |
| `docs/decisions/` | 왜 바꿨나 — `adr/`(결정)·`rfc/`(제안) append-only 이력 | 불변 아카이브 |

CLAUDE.md는 결정·금지를, reference는 구현 방식을 담는다 — 둘이 같은 것을 두 번 쓰지 않는다.

## 지도

reference/ (지금 어떻게 도나):
- `contracts/agent-data.md` — 에이전트 메시지 wire 계약의 사람용 카탈로그 (envelope + system.* datapoint-array + inventory, 필드 카탈로그·값 의미론·OS별 차이). 기계검증 정본 JSON Schema 는 같은 디렉토리. 얼어붙은 외부 인터페이스.
- `contracts/env.md` — 환경변수 계약 (키 카탈로그·secret 채널·기동 검증).
- `contracts/assessment-api.md` — 프로비저닝 어세스먼트 API 계약 (engine -> 재해복구/마이그레이션 소비자). GET /api/assessment + POST /api/exports/inventory 응답 구조·필드 의미·단위·불변식·버전 규약 + 복잡 스토리지 VM 워크드 예시(9절). 얼어붙은 외부 인터페이스.
- `docker.md` — 이미지·compose 구성 사양 (단일 이미지 command 분기·빌드 캐시·3파일 배치·base 서비스/포트/볼륨/기동 순서·dev override).
- `automation.md` — 워크플로·플랫폼 기능이 각각 언제 발화하나 (트리거 한눈에 + 막는 것/알리는 것 구분). 무엇을 검증하나는 루트 `README.md`, required 등록은 `guides/ci-setup.md` 3.4.
- `consumer.md` · `rabbitmq.md` · `redis.md` · `right-sizing.md` · `observability.md` — subsystem 동작 + 각자 "한계" 절.
- `right-sizing-thresholds.md` — 자원 적정성 임계치·근거 인간가독 정본 (5자원 x USE 3축, 신호·임계·basis·robustness). `right-sizing.md` 는 판정 명세, 본 문서는 임계 수치·근거.
- `db/` — models · dtos · repositories · timescaledb.
- `web/` — layering · routers · services · view-models · static-assets · type-contract.

guides/ (어떻게 하나):
- `deploy.md` · `migrate.md` · `release.md` — 배포·스키마 마이그레이션·릴리즈 절차.
- `local-dev.md` · `testing.md` · `ci-setup.md` — 로컬 개발·테스트·CI 활성.
- `pre-pr-checklist.md` · `conventions.md` · `dependencies.md` — PR 발행 전 체크리스트·코드 규약·의존성 관리.

explanation/ (왜):
- `tradeoffs.md` — 의식적 설계 한계.
- `products/` — 산출물 존재 의의 (dashboard · environment-report · server-report · json-export · install-task).

decisions/ (왜 바꿨나 — 라이브 문서 무의존):
- `adr/` — 결정 기록 (append-only). 쓰는 기준과 Status 어휘는 `adr/README.md` 가 갖는다.
- `rfc/` — 제안·탐색 문서 (결정 전).

`temp/` — 임시 자료. 외부 공유 자료(영구 문서·코드와 양방향 의존 0)와 학습 자료 초안(격상 대상)이 들어온다. 영구 문서·코드에서 인용 금지.

`learning/` — 학습 자료. 도구·플랫폼 원리 + 이 저장소를 예제로 든 대조. 시점 스냅샷이라 갱신 의무 없고 영구 문서·코드에서 인용 금지. 관리 계약은 `learning/README.md`.

`superpowers/` — 내부 방법론 작업 자료 (감사 리포트·설계 spec). 일회성 기록이라 영구 문서·코드에서 인용 금지.

## 변경 규칙

- 코드 변경 시 그 사실을 담은 문서 하나를 동시 갱신 (#F9). 여러 곳에 흩지 않는다.
- 사실을 옮길 땐 원본에서 지우고 새 위치에 pointer만 남긴다 (중복 0).
- 도구·구조가 바뀌면 옛 이름·경위를 라이브 문서에서 제거하고 현황으로 덮는다 (#F12). 이력은 decisions/에만.

## 범위

본 저장소 범위는 `.claude/CLAUDE.md` #A0 단일 진실.
