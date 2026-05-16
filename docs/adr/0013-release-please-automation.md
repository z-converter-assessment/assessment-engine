# ADR 0013 — release-please 자동화 (semver tag·CHANGELOG·Release PR)

## Status
Accepted

## Context

ADR 0012가 wheel + GitHub Release artifact contract 채택. release 발사대 = main의 semver tag `v*` push.

기존 ceremony 수동:
- 운영자가 main에 PR merge 후 git checkout main → git tag -a v1.2.3 → git push origin v1.2.3 직접 실행
- semver bump 결정 (major/minor/patch)도 사람이 매번 판단
- CHANGELOG 수동 작성 또는 release notes를 release.yml의 `generate_release_notes: true`에 의존 (PR title 기반, 누적 정리 안 됨)

문제:
- semver 결정 사람 판단 — 누락·잘못 bump 위험
- CHANGELOG 단일 진실 없음 — release.yml의 generate_release_notes는 마지막 release 이후 PR list만, 누적 history 보존 안 함
- release 빈도 낮을 때 ceremony 사람이 잊거나 미루는 부담
- main 직접 push 가능한 위험 (tag 생성 시점에 사람이 의도치 않게 commit push할 수도)

## Decision

Conventional Commits 강제 + Google `release-please` 자동화 채택.

흐름:
1. PR title은 Conventional Commits 형식 (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:` 등) — `pr-title-check.yml`이 PR 시점 강제
2. PR squash merge 시 PR title이 main commit message가 됨
3. main push → `release-please.yml` 발사
4. release-please가 main commit history 분석:
   - 마지막 release tag 이후 commit 중 feat/fix/BREAKING 감지
   - 의미 있는 변경 있으면 "Release PR" 자동 생성·갱신:
     - `pyproject.toml` version bump (semver 정책 자동 결정)
     - `CHANGELOG.md` 갱신 (type별 분류 누적)
     - PR body에 변경 요약
   - 의미 있는 변경 없으면 (docs·chore·test만) Release PR 생성 안 함
5. 운영자가 Release PR 검토·승인·merge
6. merge 시점에 release-please가 tag(`v*`) 자동 생성·push
7. tag push → 기존 `release.yml` 발사 → wheel + sdist + SHA256SUMS GitHub Release 자동 첨부

semver 정책 (Conventional Commits → release-please 매핑):
- `feat:` → MINOR bump
- `fix:` / `perf:` → PATCH bump
- `BREAKING CHANGE:` (body) 또는 `feat!:` / `fix!:` (title `!`) → MAJOR bump
- `docs:` / `chore:` / `refactor:` / `test:` / `build:` / `ci:` / `style:` / `revert:` → version bump 안 함 (CHANGELOG에만 카탈로그 누적)

`bump-minor-pre-major: true` + `bump-patch-for-minor-pre-major: true`:
- 0.x 버전 동안 feat → MINOR, fix → PATCH (0.x.y → 0.x.y+1 또는 0.y+1.0)
- 1.0.0 이전엔 BREAKING이 MINOR로 다운 — 초기 개발 자유도 보존
- 1.0.0 도달 시점에 manifest 수정

## Options Considered

1. release-please (Google, GitHub Action) — 채택
   - 장점: Release PR 단계로 운영자 review 강제. pyproject.toml + CHANGELOG 자동 갱신. Python 정식 지원. GitHub Action 단독 (외부 의존성 0)
   - 단점: Conventional Commits 강제 (PR title check 추가 필요)

2. python-semantic-release (pip 도구)
   - 장점: Python 생태계 정합. pip install
   - 단점: main push 즉시 tag·release (PR 단계 없음) — control 약함. Release PR 같은 review 단계 없어 운영자 mistake 시 즉시 release 발사

3. semantic-release (Node, npm)
   - 장점: 산업 표준. 가장 보편
   - 단점: Python repo에 Node 도구 도입 부담. npm 의존성

4. 수동 ceremony 유지 (이전 상태)
   - 장점: 단순
   - 단점: 누락·잘못 bump·CHANGELOG 단일 진실 부재

옵션 1 채택 — Release PR review 단계 + Python 정식 지원 + GitHub Action 단독이 본 repo 정합.

## Consequences

장점
- semver bump 자동 + 일관 (사람 판단 매번 안 함)
- CHANGELOG.md가 단일 진실로 누적 — 외부 인프라가 release history 추적 가능
- Release PR 단계가 운영자 review 보장 — 자동 push 즉시 release 아님
- main 직접 push 막혀도 release-please bot이 자동 tag — branch protection 정합

단점·한계
- Conventional Commits convention 학습 부담 (1인 운영이지만 commit 시마다 type prefix 의무)
- PR title check workflow가 위반 시 PR merge 차단 — 운영자가 PR title 신경 써야
- 0.x → 1.0.0 전환 시점 manifest 정책 수동 변경 (`bump-minor-pre-major` 비활성)
- Release PR이 누적되는 동안 main의 unreleased commit은 prod에 없음 — 의도적 시점 release 정합

## Migration (본 ADR 채택 시점)

| 작업 | 위치 | 변경 |
|------|------|------|
| `release-please-config.json` | 신규 | python release-type + bump-minor-pre-major |
| `.release-please-manifest.json` | 신규 | 초기 `.` = `0.0.0` |
| `.github/workflows/release-please.yml` | 신규 | main push trigger + release-please-action v4 |
| `.github/workflows/pr-title-check.yml` | 신규 | Conventional Commits PR title 강제 |
| `pyproject.toml` | version 라인에 `# x-release-please-version` marker | release-please가 인식해서 자동 bump |
| `CHANGELOG.md` | 신규 | 초기 헤더만 — release-please가 자동 갱신 |
| README | CI 표 갱신 | release-please.yml·pr-title-check.yml 행 추가 |
| `docs/operations/release.md` | 갱신 | release ceremony를 release-please 흐름으로 |

GitHub 측 설정 (본 repo 코드 영역 밖):
- main branch protection rule — PR 강제, admin 우회 차단
- tag protection rule (`v*`) — release-please bot 또는 owner만 허용

## 관련 문서·코드

- `.github/workflows/release-please.yml` — main push → Release PR 생성·갱신·tag publish
- `.github/workflows/pr-title-check.yml` — PR title Conventional Commits 강제
- `.github/workflows/release.yml` — tag `v*` push → wheel + sdist + SHA256SUMS GitHub Release
- `release-please-config.json` + `.release-please-manifest.json` — release-please 설정
- `CHANGELOG.md` — release-please 자동 갱신 단일 진실
- `docs/operations/release.md` — release artifact + ceremony 단일 진실
- ADR 0012 — wheel + GitHub Release artifact contract (본 ADR이 그 발사 ceremony 자동화)
