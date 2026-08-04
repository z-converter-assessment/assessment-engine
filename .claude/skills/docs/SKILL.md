---
name: docs
description: TRIGGER when the user wants documentation brought in line with the code ("문서 정리", "문서 갱신", "/docs", "문서가 코드랑 맞나"). Compares what the code actually does against what the docs claim, rewrites the docs to the present state per the 4 principles, then verifies with doc-auditor. Read the code first — never rewrite from the diff alone. Does not commit or push.
---

# /docs — 코드 현황에 맞춰 문서 정리

문서를 현재 코드 상태로 맞춘다. 릴리즈 주기와 독립적으로 언제든 실행한다 — drift 는 기능 단위로 쌓이지 않고 시간이 지나며 쌓인다.

문서 규율 단일 진실 = `docs/README.md`(4원칙) + `docs/guides/wrap-up.md` Stage 4·5. 본 skill 은 절차만 가지고 체크리스트를 복제하지 않는다.

## 범위

인자로 영역을 받는다 (`/docs 배포`·`/docs consumer`·`/docs docs/guides/migrate.md`). 미지정이면 사용자에게 대상을 묻는다 — 저장소 전체 감사는 한 번에 끝나지 않는다.

## 절차

### 1. 코드에서 사실을 뽑는다

문서를 먼저 읽지 않는다. 코드·설정·워크플로를 실행하거나 읽어서 현재 동작을 확정한다. 문서를 먼저 읽으면 그 서술에 끌려가 같은 오류를 재생산한다.

확인 수단은 실행이 우선이다 — 명령을 돌려보고, 파일 존재를 확인하고, 설정값을 출력한다. 읽기만으로 판단한 것은 그렇게 표시한다.

### 2. 문서 서술과 대조한다

대상 영역을 다루는 문서를 찾아 각 서술이 현재 사실과 맞는지 본다. 자주 나오는 drift 유형:

- 폐기된 도구·경로·명령이 남아 있다 (전환 시 문서 미갱신).
- 개수·목록이 실제와 다르다 (컴포넌트·서비스·필드가 늘거나 줄었는데 표가 그대로).
- 절차가 실행되지 않는다 (경로 변경·옵션 변경으로 문서대로 하면 실패).
- 규약이 강제되지 않는다 (문서는 강제라 쓰는데 실제 게이트가 없다).

### 3. 현재 상태로 다시 쓴다

목적별로 갱신한다 — 동작은 `docs/reference/`, 계약은 `docs/reference/contracts/`, 절차는 `docs/guides/`, 설계·한계는 `docs/explanation/`.

4원칙 적용: 현재 상태만 선언(이력 서사 0) · 사실 1곳(중복이면 pointer) · 문서 하나 = 목적 하나 · ADR 번호·옛 경로 참조 0.

결정 자체가 바뀐 것을 발견하면 문서만 고치지 않는다 — 새 ADR + 이전 ADR `Superseded by` + 인덱스 행이 함께 필요하다.

### 4. 검증

- doc-auditor 에이전트(`Agent(subagent_type='doc-auditor')`) — 중복·목적 혼선·이력 서사·죽은 포인터 독립 검증. 지적 반영 후 재검.
- 라이브 docs 에 `ADR [0-9]{4}` · 옛 doc 경로 · bold · 비키보드 unicode grep 0.

### 5. 보고

고친 문서와 각 drift 를 무엇으로 확인했는지(실행/읽기) 보고한다. 코드 쪽 결함을 발견했으면 문서를 코드에 맞추지 말고 보고한다 — 문서가 옳고 코드가 틀린 경우가 있다.

## 규율

- 코드를 먼저 읽는다. diff 만 보고 문서를 다시 쓰지 않는다 — diff 에 없는 drift 가 대부분이다.
- 사실 확인 없이 문서 서술을 근거로 삼지 않는다. 문서끼리 서로를 근거로 인용하면 틀린 서술이 굳는다.
- 코드를 고치지 않는다. 코드 결함은 보고만 하고 수정은 별도 작업으로 분리한다.
- commit·push·PR 안 한다 — `/commit`·`/pr` 별도 발동.
