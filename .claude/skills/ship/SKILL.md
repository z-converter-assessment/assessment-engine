---
name: ship
description: TRIGGER when the user is ready to land a completed feature ("커밋", "/ship", "마무리", "출하"). Documents the working diff declaratively (present-state, dedup, remove past remnants) per docs/README.md 4원칙 + Diátaxis, then commits. Never pushes or opens a PR unless explicitly asked.
---

# /ship — 기능 -> 선언적 문서화 -> 커밋

작업이 완성된 시점의 working diff 를 현재 상태로 문서화한 뒤 커밋한다. 하나의 워크플로: diff 파악 -> 문서 선언화(중복 제거·과거 잔재 삭제) -> 기계 게이트 -> 커밋.

문서 규율 단일 진실 = `docs/README.md`(4원칙) + `docs/guides/wrap-up.md`(단계별 체크리스트). 본 skill 은 절차만 담고 체크리스트 본문을 복제하지 않는다.

## 진입 조건

- feature branch 위 (`main`/`master` 직접 커밋 X — 위반 시 abort + 보고).
- 기능 동작 완성 — 사용자 또는 `/run`·`/verify` 로 확인됨. 이 검증이 리팩토링 회귀 안전망.
- 진입 시 `git branch --show-current` · `git status --short` · `git diff --stat` 확인 -> 변경 범위 1줄 요약 + 진행 OK.

## 단계

### 1. 코드 정합
- code-reviewer 에이전트 1회 (`Agent(subagent_type='code-reviewer')`) — 정석 idiom + 명문 규약(P1-P4·F1-F11·#B·#C5). Error 즉시 수정 / Warning 위임 / Info 보고.
- 코드 수정 시 2단계로 이어감.

### 2. 문서 선언화 (핵심)
diff 가 만진 영역을 Diátaxis 목적별로 갱신한다. "무엇이 바뀌었나 -> 어느 목적 문서인가":
- 동작(subsystem) 바뀜 -> `docs/reference/`
- 계약(agent 데이터·env) 바뀜 -> `docs/reference/contracts/`
- 작업 절차 바뀜 -> `docs/guides/`
- 설계·한계 바뀜 -> `docs/explanation/`

갱신 규율 (docs/README.md 4원칙):
1. 현재 상태만 선언 — "B다". 이력 서사("옛 X 폐기"·"~에서 전환"·"정정(날짜)") 0.
2. 사실 1곳 — 중복이면 원본만 두고 pointer. 같은 사실 두 문서에 안 씀.
3. 문서 하나 = 목적 하나 — reference 에 절차 안 섞고 explanation 에 how-to 안 넣음.
4. ADR 번호·옛 doc 경로(architecture/operations/...) 참조 0 — 사실은 인라인, 이력은 `decisions/`.

`#F9` 영향도 체인 적용 — 코드 변경 유형별 동시 갱신 위치를 CLAUDE.md #F9 표에서 확인해 누락 0.

### 3. 기계 게이트
- 훅 규칙 자가 확인: 라이브 docs 에 `ADR [0-9]{4}` · 옛 doc 경로 · bold · 비키보드 unicode · 날짜 정정 grep 0.
- doc-auditor 에이전트(`Agent(subagent_type='doc-auditor')`) 로 중복·목적 혼선·이력 서사 독립 검증. 지적사항 반영 후 재검.

### 4. 커밋
- 현황 선언형 메시지: 무엇을 왜 바꿨나 (feature 요지 + 문서 정합). AI 메타데이터(Co-Authored-By·Generated-with) 절대 없음.
- feature branch 에 커밋. push·PR 은 안 한다.

## 규율

- 기능 개발 중엔 코드만 — 문서·테스트 선제 작성 X. 정합은 /ship 에서 일괄 (wrap-up 원칙).
- pytest 자동 실행 X — 사용자 명시 시만 (단계 종료 시 옵션 1회).
- push·PR 자동 X — 사용자 명시 시.
- 단계 경계에서만 보고 — 스코프 확장·알려진 갭만 컨펌, 그 외 정석 정정은 즉시 적용.
- 체크리스트 본문 복제 X — `docs/guides/wrap-up.md` 단일 진실.
