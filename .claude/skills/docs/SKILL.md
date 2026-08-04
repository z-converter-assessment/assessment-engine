---
name: docs
description: TRIGGER when the user wants documentation brought in line with the code ("문서 정리", "문서 갱신", "/docs", "문서가 코드랑 맞나"). Compares what the code actually does against what the docs claim, rewrites the docs to the present state per the 4 principles, settles ADR records for any decision that changed, then verifies with doc-auditor. Read the code first — never rewrite from the diff alone. Does not commit or push.
---

# /docs — 코드 현황에 맞춰 문서 정리

문서를 현재 코드 상태로 맞춘다. 릴리즈 주기와 독립적으로 언제든 실행한다 — drift 는 기능 단위로 쌓이지 않고 시간이 지나며 쌓인다.

문서 규율 단일 진실 = `docs/README.md`(4원칙) + `docs/guides/pre-pr-checklist.md` Stage 4·5. 본 skill 은 절차만 가지고 체크리스트를 복제하지 않는다. develop PR 게이트가 본 skill 을 feature 영역으로 호출한다 (배치 근거는 `docs/guides/pre-pr-checklist.md` 0절).

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

### 4. ADR 을 정리한다 (건너뛰지 않는다)

문서 정리에는 ADR 정리가 반드시 따른다. 현황 문서를 고쳤다는 것은 무언가가 달라졌다는 뜻이고, 그중 결정이 달라진 것은 아카이브에 남아야 한다. 이 단계를 생략하면 "왜 바꿨나" 가 어디에도 안 남는다.

먼저 판정한다 — 이번 정리에서 고친 것이 구현 세부인가 결정인가. 결정이면 기존 ADR 을 뒤집었는지, 새 결정인지 가른다.

상태는 파일과 인덱스 두 곳에 있다. 한쪽만 고치면 조용히 어긋나므로 항상 같이 본다.

| 상황 | 처리 |
|------|------|
| 구현 세부만 바뀜 | ADR 불요. 라이브 문서만 |
| 기존 결정을 뒤집음 | 새 ADR 파일 + 인덱스 새 행. 이전 ADR 은 파일 `상태:` 줄과 인덱스 Status 열 둘 다 `Superseded by NNNN` |
| 기존 결정을 일부만 수정·확장 | 새 ADR 파일 + 인덱스 새 행. 이전 ADR 은 두 곳 다 `Refined by NNNN` (무엇이 남고 무엇이 바뀌는지 새 ADR 본문에) |
| 기존 결정을 폐기 (대체 없음) | 이전 ADR 파일·인덱스 둘 다 `Withdrawn` + 사유 1줄 |
| 새 결정 (선행 ADR 없음) | 새 ADR 파일 + 인덱스 새 행 |

번호는 마지막 + 1 단조 증가다. 재사용하지 않는다.

기존 ADR 은 덮어쓰지 않는다. 허용되는 변경은 셋뿐이다 — `상태:` 줄 갱신, 인덱스 행 갱신, 본문 끝에 `정정 (날짜)` 블록 덧붙이기. 결정 당시 서술을 사후에 고쳐 쓰는 것은 금지다.

파일명 규약과 본문 섹션 구조는 `docs/decisions/adr/README.md` 가 갖는다.

ADR 을 안 쓰기로 판단했으면 그 판단도 보고에 남긴다. 침묵으로 건너뛰지 않는다.

### 5. 검증

- doc-auditor 에이전트(`Agent(subagent_type='doc-auditor')`) — 라이브 문서 4원칙 + ADR 인덱스 정합 독립 검증. 지적 반영 후 재검.
- 라이브 docs 에 `ADR [0-9]{4}` · 옛 doc 경로 · bold · 비키보드 unicode grep 0.
- ADR 파일 번호 집합 == 인덱스 행 집합. 차집합이 나오면 4절로 돌아간다.

### 6. 보고

고친 문서와 각 drift 를 무엇으로 확인했는지(실행/읽기) 보고한다. ADR 은 신설·Superseded·불요 중 무엇으로 판정했는지 명시한다. 코드 쪽 결함을 발견했으면 문서를 코드에 맞추지 말고 보고한다 — 문서가 옳고 코드가 틀린 경우가 있다.

## 규율

- 코드를 먼저 읽는다. diff 만 보고 문서를 다시 쓰지 않는다 — diff 에 없는 drift 가 대부분이다.
- 사실 확인 없이 문서 서술을 근거로 삼지 않는다. 문서끼리 서로를 근거로 인용하면 틀린 서술이 굳는다.
- 코드를 고치지 않는다. 코드 결함은 보고만 하고 수정은 별도 작업으로 분리한다.
- commit·push·PR 안 한다 — `/commit`·`/pr` 별도 발동.
