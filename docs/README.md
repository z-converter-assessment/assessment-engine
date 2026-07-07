# 문서 관리 계약

본 파일은 이 저장소 문서를 어떻게 쓰고 관리하는지의 단일 진실. 문서를 추가·수정하기 전에 아래 4원칙과 지도를 따른다.

## 4원칙

1. 사실 하나 = 문서 하나. 같은 사실을 두 곳에 쓰지 않는다. 다른 문서에 이미 있으면 그 문서를 가리키고(pointer) 재서술하지 않는다.
2. 현재 상태만 선언. "B다"라고 쓴다. "A였다가 B가 됐다"·"옛 X 폐기"·"~에서 전환" 같은 이력 서사는 쓰지 않는다 (`adr/`만 예외).
3. 근거는 인라인 한 줄. "X = Y (출처)" 형식으로 결정 옆에 붙인다. 깊은 "왜"가 필요하면 `adr/` 아카이브에 두고, 라이브 문서는 그것 없이 자족한다.
4. `adr/`는 이력 아카이브지 의존 대상이 아니다. 라이브 문서(architecture·operations·products·development·CLAUDE.md)는 ADR 번호를 전제로 참조하지 않는다 — 필요한 사실은 라이브 문서 안에 인라인으로 있어야 한다.

## 계층 역할

| 계층 | 역할 | 성격 |
|------|------|------|
| `.claude/CLAUDE.md` | 규칙·금지·원칙 + 문서 지도. 에이전트가 안 깨려면 알아야 할 것만. 구현 상세 없음 | 얇게·매 세션 로드 |
| `docs/` (본 디렉토리) | 시스템이 지금 어떻게 도는가 + 계약. on-demand 레퍼런스 | 현재 상태 선언 |
| `docs/adr/` | "왜 이렇게 바꿨나"의 append-only 이력 | 불변 아카이브 |

CLAUDE.md는 결정·금지를, docs는 구현 방식을 담는다 — 둘이 같은 것을 두 번 쓰지 않는다.

## 지도

계약 (얼어붙은 외부 인터페이스 — 1급 시민):
- `architecture/agent.md` — 에이전트 메시지 데이터 계약 (필드 카탈로그·값 의미론·OS별 차이).
- `operations/env.md` — 환경변수 계약 (키 카탈로그·secret 채널·prod 검증).
- `operations/deployment.md` + `operations/release.md` — 배포·릴리즈 계약 (rollout·이미지·서명).

architecture (subsystem이 지금 어떻게 도는가 + 각자 "한계" 절):
- `consumer.md` — handler 사이클·auto-register·재시도·부가 시그널·DLQ.
- `rabbitmq.md` — vhost·권한·토폴로지·큐 정책.
- `redis.md` — 키 설계·TTL·무효화·fail-open 매트릭스.
- `right-sizing.md` — 자원 적정성 분류 명세 (분류·판정 순서·임계·출처·OS 분기·한계) 단일 진실.
- `db/` — models / dtos / repositories / timescaledb.
- `web/` — layering / routers / services / view-models / static-assets / export-schema.

operations (배포·운영):
- `alembic.md` — 스키마 마이그레이션 절차.
- `observability.md` — 로그 레벨·외부 의존 실패 매트릭스 단일 진실.

products (산출물 존재 의의):
- `dashboard.md` · `environment-report.md` · `server-report.md` · `json-export.md` · `install-task.md`.

development (본 repo dev 규약):
- `docker.md` · `dependencies.md` · `testing.md` · `conventions.md` · `wrap-up.md` · `github-setup.md`.

`adr/` — 결정 이력 아카이브 (0001-0052). 라이브 문서는 여기 의존 안 함. 결정 변경 시 새 ADR 추가 + 이전은 `Status: Superseded`/`Withdrawn`.

## 변경 규칙

- 코드 변경 시 그 사실을 담은 문서 하나를 동시 갱신 (#F9 영향도 체크리스트). 여러 곳에 흩지 않는다.
- 사실을 옮길 땐 원본에서 지우고 새 위치에 pointer만 남긴다 (중복 0).
- 도구·구조가 바뀌면 옛 이름·경위를 라이브 문서에서 제거하고 현황으로 덮는다 (#F12 선언성). 이력은 ADR에만.

## 범위

엔진 애플리케이션 + docker compose 배포 + 엔진 rollout(`deploy.sh`, VM 에서 실행)까지 (CLAUDE.md #A0). VM provisioning(IaC)은 별도 준비 VM 전제 — docker·cosign·deploy.sh 설치는 1회성 `bootstrap.sh`.
