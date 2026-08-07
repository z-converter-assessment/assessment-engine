---
name: pr
description: Pull Request(PR)를 만들 때 사용한다. 기준 브랜치 이후의 전체 커밋을 검토하고 프로젝트 템플릿으로 PR을 생성한다. 기본 기준 브랜치는 develop이다.
---

# PR 생성

브랜치 전략: main(배포 — 직접 push 금지) / develop(통합 — PR 머지) / feature/fix/chore(작업). 보호 브랜치 직접 push 는 GitHub ruleset 이 차단.

## Base 브랜치 정책

- Default base = `develop`. feature -> develop 통합이 표준.
- `--base main` 은 사용자가 명시적으로 "main에 PR" 요청한 경우만 (release 승격 또는 hotfix).
- 사용자 요청에서 base 를 확정하고 이후 fetch, diff, 감사, PR 생성에 같은 값을 사용한다.
- 원격 base 부재 시 임의 생성 X — `git ls-remote --heads origin <base>` 확인 후 사용자에게 보고.

### base = `develop` 게이트 (통합 branch)

코드와 문서를 feature 단위로 함께 맞추는 지점. 문서/ADR 은 결정한 직후에만 근거가 정확하므로 여기서 쓴다 (배치 근거는 `docs/guides/pre-pr-checklist.md` 0절).

1. `.agents/reviewers/code-reviewer.md` 감사 프롬프트에 `base=origin/develop` 을 넘겨 1회 실행 — 정석 idiom + 명문 규약(P1-P4/F1-F13/#B/#C5). Error 즉시 수정 / Warning 위임 / Info 보고.
2. 변경 유형이 결합 목록에 걸리면 `change-impact` skill 로 동시 갱신 위치 확인.
3. `docs` skill 을 본 feature 가 건드린 영역으로 실행 — 코드 현황 대조/문서 갱신/ADR 정리/doc-auditor 검증까지 그 skill 이 담당한다.
4. ADR 정합 확인 (차단 게이트). 검사 항목과 명령은 `.agents/reviewers/doc-auditor.md` 축 B 가 갖는다. 결정이 바뀐 건이 있는데 ADR 이 없지는 않은지도 함께 본다. 어긋나면 3 으로 돌려보내고 PR 을 열지 않는다 — 강제 채널이 워크플로에도 훅에도 없어 여기가 유일한 그물이다.
5. 이 PR 이 승격 직전이면(머지 후 바로 `develop -> main` 을 열 계획이면) `.agents/reviewers/doc-auditor.md` 감사 프롬프트를 `origin/main..HEAD` 범위로 1회 실행 — 릴리즈 단위 중복/목적 혼선/ADR 인덱스 정합. 개별로는 맞는 서술이 여러 feature 가 모이면 어긋나고 그건 묶어 봐야 보인다. 지적은 이 PR 안에서 고친다.

lint/단위 테스트/타입 계약/alembic drift 는 PR 발행 후 CI 가 돌린다 — 로컬 재현 없이 CI 결과를 확인한다.

### `--base main` 추가 강화 (배포 branch)

릴리즈 단위가 확정되는 지점. 문서는 여기서 쓰지도 검증하지도 않는다 — 릴리즈 단위 문서 검증은 승격 직전 develop PR 이 이미 했다(develop 게이트 5항).

1. 정상 경로 = `develop -> main` 승격 또는 `hotfix/*`. 다른 prefix 직접 main PR 이면 의도 재확인.
2. `uv run pytest tests/integration -q` 의무 (사용자 confirm 없이 — main 안전 우선).
3. `git log origin/main..HEAD --oneline` 전 커밋이 Conventional Commits 형식 의무.
4. PR body 에 "main PR 사유"(release/hotfix) 절 강제.

## Pre-check (발행 직전 의무 — CI 실패/알림 noise 회피)

### A. PR title 형식
`.github/workflows/pr-title-check.yml`(semantic PR)이 검증. `cat` 으로 현재 패턴 추출 후 정합:
- `<type>: <subject>` — type in feat/fix/docs/chore/refactor/perf/test/build/ci/style/revert.
- subject 첫 글자 대문자 금지 (`^[^A-Z].+$`) — 약어(ZDM/SQL) 첫 글자면 fail. 한글 시작 또는 소문자.
- breaking 이면 `feat!:`/`fix!:` 또는 body `BREAKING CHANGE:`.
- OK: `feat: 운영자 자율 선택` / `fix: handle null hostname`. NG: `feat: ZDM 직접 fetch`(대문자).

### B. 코드 검증
발행 후 CI 결과를 확인한다. 실패하면 수정 + 새 commit 을 올려 재검한다.

## PR template 우선 (의무)

`--body` 로 본문을 명시하면 `.github/PULL_REQUEST_TEMPLATE.md` 가 자동 적용 안 되니 호출자가 그 양식을 채운다:
1. `cat .github/PULL_REQUEST_TEMPLATE.md` 로 헤더/섹션/체크박스 구조 읽기.
2. 그 구조 그대로 유지 — placeholder 를 본 PR 내용으로 채움. template 의 이모지/기호는 사용자 의도 양식이라 보존 (전역 이모지 금지의 template 예외).
3. 체크박스: PR 유형은 type prefix 와 정합 1개만 `[x]`. 체크리스트는 실제 수행한 것만 `[x]` (강제 체크 금지). 빈 항목(관련 이슈/스크린샷)은 그대로.
4. template 없으면 fallback `## Summary` + `## Test plan` (한글).

## 분석

1. `git status --porcelain` — 출력이 있으면 PR 절차를 중단한다. 커밋 절차를 완료하고 작업 트리가 clean 인 상태에서만 재개한다.
2. `git fetch origin <base>` + `git log origin/<base>..HEAD --oneline` + `git diff origin/<base>...HEAD --stat` — 브랜치의 전체 커밋 분석 (최신 커밋만 보면 누락).
3. 감사 프롬프트는 `base=origin/<base>` 와 함께 실행해 커밋된 diff, staged, unstaged, untracked 를 모두 검토.

## 생성

- `git status --porcelain` 출력이 없는지 다시 확인한다.
- push 안 됐으면 `git push -u origin <branch>` 먼저.
- `gh pr create --base <base> --title "<type>: 한 줄 요약" --body "$(cat <<'EOF' ... EOF)"` — HEREDOC body.
- 제목 70자 미만. body 는 template 구조 채움 (현재 상태 선언, 브랜치 전체 요약).
- 작성 주체 메타데이터 footer(Generated with/Co-Authored-By) 절대 금지 — `pr-title-check.yml` 이 강제한다.
- 반환 PR URL 사용자에게 노출.

## 금지

- gh interactive flag(`-i`).
- `--base main` 자동 — 사용자 명시 시만.
- develop 부재 시 임의 생성.
- template 무시하고 임의 양식.
- 작성 주체 메타데이터.
