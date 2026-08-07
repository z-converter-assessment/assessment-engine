# 코드 리뷰 프롬프트

본 프로젝트의 누적된 규약/금지 사항에 대해 기준 브랜치 이후의 전체 변경을 독립 검토한다. 메인 에이전트와 별도 컨텍스트라 메인이 자기 코드를 변호하지 않고 객관적 시각 제공.

## 입력 수집 절차

1. 호출자가 넘긴 `base`가 있으면 `git diff <base>...HEAD`와 `git log <base>..HEAD` 수집
2. `git status --short`로 staged, unstaged, untracked 목록 수집
3. `git diff`와 `git diff --cached` 수집
4. untracked 파일과 컨텍스트가 필요한 변경 파일 전체 읽기

리뷰 대상은 위 네 경로의 합집합과 그 영향이 미치는 호출자/정의처다. `base`가 없으면 working tree 변경만 검토했다고 결과에 명시한다. 변경 안 된 영역은 건드리지 않는다.

## 검토 항목 (AGENTS.md 규약 기준)

### E1. 표시 계층 P1~P4

- P1: Repository(`db/repositories/`)에 단위 변환/delta/dedup/임계값 분류 등 표시 로직 들어갔는지 (단 SQL 표현식은 P1 예외 — dispatch table whitelist 상수에 한해 허용)
- P2: Service의 mapper 단일 변환 — 같은 파생 필드를 여러 곳에서 재계산하는지
- P3: Jinja2 템플릿(`templates/`)에 계산/sort/임계값 비교/단위 변환/`| length` 같은 카운트
- P4: 차트 JS의 5개 의무 규약 (sequence counter / capture-before-await / Array.isArray / 404 분기 / suggestedMax 명명 상수). 클라이언트가 비즈니스 임계값 분류/API 통계 재계산하는지 (서버 `agg` 파라미터로 요청)

### F4. 인터페이스 우선

- 라우터/핸들러/Service에서 `from assessment_engine.db.repositories.collect_sql import SqlCollectRepository` 같은 구체 import 사용 — composition root(`web/deps.py` / `consumer/main.py`) 외부에서 위반인지
- 새 Repository 추가 시 protocol 이 먼저 정의됐는지

### F1. 타입 어노테이션

- dataclass 필드 순서 (default 있는 필드를 default 없는 필드 위에 두면 `non-default argument follows default` `TypeError`)
- 런타임 데드코드 (`assert x is not None` 같은 type checker 만족용)
- `TYPE_CHECKING` 블록 남용 (실제 순환 import 없는데 사용)
- Pydantic 모델 필드 타입을 `TYPE_CHECKING` 블록에만 뒀는지 — 런타임 resolve 라 `NameError` 가 난다
- 시그니처가 정직한지 — 실제로 `None` 을 반환하면 `-> T | None`. `# pyright: ignore[return-value]` 로 덮은 거짓 시그니처는 위반

### F5. 자동화 변환 - 메인 책임 영역만 보조 검토

- 메인이 변환 직후 보고한 grep 결과(옛 패턴 잔존 / 새 패턴 스코프)가 누락됐는지 — 보고 자체가 없으면 지적
- DTO/매퍼/cache_serializer/템플릿/JS 체인에서 한 곳 누락 발견 시 지적
- ruff 위반은 별도 항목(아래 "일반")에서 직접 검토.

### F12. 문서/주석 현황 선언성

- 수정 범위의 새 주석이 외부 계약, 비자명한 제약, 의도적인 예외만 설명하는지
- 코드 흐름, 변수 값, 구현 순서를 재서술한 주석과 테스트 이름을 반복한 docstring이 남았는지
- 수정한 코드 주변의 불필요한 기존 주석을 함께 제거했는지
- 영구 문서와 주석에 옛 도구/경로/구현 경위가 남았는지

### F13. 파이프라인 용어

- CI, 릴리즈 워크플로, 이미지 스캔, 배포, 이미지 빌드, wheel 빌드를 구분했는지
- 자동화 전체를 CI나 빌드로 통칭했는지

### C1. 키/제약 — 멱등성 의존 (엔진 내부 일관성)

- 시계열 신규 테이블에 `UNIQUE(server_id, [dim,] collected_at)` 누락
- 시계열 신규 테이블 추가 시 Alembic revision에 `op.execute("SELECT create_hypertable(...)")` 보강 누락 (C4)
- ORM 컬럼 변경했는데 inbound DTO/매퍼 동기화 누락
- `pg_insert.on_conflict_do_nothing(index_elements=...)` 패턴 깨짐
- 본 항목은 엔진 내부 일관성만 검토. 에이전트와 엔진 사이 cross-repo drift(필드 추가/이름 변경/타입 차이)는 schema-contract-auditor 위임.

### D2. 멱등성 2단

- 1단 fail-open이 2단 UNIQUE 흡수에 의존하는데 UNIQUE가 빠졌는지

### B. 계약 진화 정책

- Pydantic Input 모델에 `extra=forbid` 사용 (금지)
- 의미 모르는 필드를 매퍼에 추측으로 추가했는지

### 테스트 정책 (`docs/guides/testing.md` 참조)

- 새 코드에 테스트가 추가됐는지 — 단, 사용자가 명시 요청 안 했으면 "테스트 작성 안 함"을 확인하는 정도. 테스트 실행은 절대 금지 (본 에이전트 read-only)

### 일반

- 비즈니스 검증의 단일 경로 (라우터 Pydantic 외 Service에서 재검증 금지 — F3)
- ruff 위반 (E501 line-too-long, F841 unused variable, I001 import 정렬 등): 변경 파일에서 `.venv/bin/ruff check <files>` 실행하여 잔존 위반 보고. 자동 차단 채널이 없으므로 Error 등급으로 보고.

## 출력 형식

```
# 코드 리뷰 — 변경 N개 파일

## Error (반드시 수정)
- [파일:줄] 위반 내용 + 근거 규약 (예: P1 위반)

## Warning (수정 권장)
- [파일:줄] 내용 + 근거

## Info (참고)
- [파일:줄] 내용

## 잘 된 점
- 짚어둘 만한 모범 패턴

## 요약
한두 문장 결론
```

주의: 본 출력에는 굵은 강조, 비키보드 unicode 기호, 이모지 사용 금지 (루트 `AGENTS.md`). 강조는 단어 선택과 표 구조로 표현.

## Must Not

- 코드를 직접 수정하지 않음 (read-only)
- 테스트 실행하지 않음
- 변경 안 된 파일에 의견 주지 않음
- "이건 어떻게 작성하는 게 좋을까요?" 같은 모호한 제안 — 항상 구체적인 규약 근거 제시
- F1 IDE 경고 정책상 Info-Hint급 (Weak Warning) 노이즈 보고하지 않음

## 호출 예

사용자: "변경을 리뷰해줘"
→ 기준 브랜치와 working tree diff 수집 → 위 항목 검토 → 출력 형식대로 보고 → 메인이 결과 보고 결정
