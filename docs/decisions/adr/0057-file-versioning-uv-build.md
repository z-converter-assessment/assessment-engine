# ADR 0057 — 파일 버전 + uv_build 백엔드 (tag derive 폐기, tag 는 릴리즈 산물)

상태: Accepted (2026-07-31) — ADR 0030(tag-derived 버전, hatch-vcs)을 supersede. ADR 0012(wheel CI artifact)의 wheel 검증은 존속.

## Context

ADR 0030 은 버전을 repo 에 저장하지 않고 git tag 에서 derive 하기로 했다. 채택 근거는 bump 커밋이 보호 브랜치와 충돌한다는 것이었다 — develop·main 직접 push 차단, main 의 PR source 는 develop 만, 그리고 당시 commit-msg hook 의 type 검사가 `bump:` 를 거부.

그 결정이 남긴 구조적 비용이 셋이다.

빌드가 저장소 이력에 의존한다. hatch-vcs 는 `git describe` 로 버전을 만들므로 빌드 자리에 `.git` 이 있어야 한다. 컨테이너 빌드 컨텍스트는 `.git` 을 제외하므로 릴리즈 워크플로가 태그에서 값을 뽑아 `--build-arg` 로 넣고, Dockerfile 이 그것을 `SETUPTOOLS_SCM_PRETEND_VERSION` 으로 옮기고, hatch-vcs 가 그 환경변수를 읽는 4단 경로가 생겼다.

빌드 백엔드가 플러그인에 묶인다. 버전 derive 를 위해 hatchling + hatch-vcs 조합이 필요했고, 패키지 밖 파일(`migrations/`, `alembic.ini`)을 넣기 위해 `force-include` 설정이 따라붙었다.

버전 산출 지점이 갈라진다. 워크플로가 태그를 파싱한 값과 hatch-vcs 실측값이 같은지 assert 하는 등가성 검증 단계가 필요했다 — 두 경로가 독립적으로 같은 값을 만들어야 했기 때문이다.

한편 ADR 0030 이 들었던 마찰 셋은 현재 규약에서 해소된다. bump 커밋을 브랜치에서 만들어 PR 로 develop 에 넣으면 직접 push 차단과 무관하고, 그 커밋이 develop 에 있으므로 main 의 PR source 원칙도 지켜진다. commit-msg hook 은 type 검사를 하지 않으며 Conventional Commits 강제 지점은 PR title 검사뿐이라 커밋 메시지 제약도 없다.

## Decision

버전 입력 지점을 `pyproject.toml` 의 `version` 하나로 두고, git tag 는 릴리즈가 성공한 뒤 워크플로가 그 값에서 파생 생성한다.

- `pyproject.toml`: `dynamic = ["version"]` 을 static `version` 으로 되돌린다. bump 는 `uv version --bump <part>` 가 수행한다.
- 빌드 백엔드를 `uv_build` 로 교체한다. 버전 플러그인이 불필요해지고, `migrations/` 와 `_alembic.ini` 를 패키지 디렉토리 안으로 옮겨 `force-include` 설정도 없앤다. 패키지 안 파일은 확장자와 무관하게 포장된다.
- `release.yml` 트리거를 tag push 에서 main push 로 바꾼다. 워크플로가 `uv version --short` 로 버전을 읽고, 해당 tag 가 이미 있으면 버전을 올리지 않은 일반 머지이므로 종료한다. 이미지 발행과 서명이 끝난 뒤 마지막에 `v<version>` tag 를 push 한다.
- Dockerfile 에서 `ARG APP_VERSION` 과 `SETUPTOOLS_SCM_PRETEND_VERSION` 을 제거한다. 빌드가 저장소 이력을 보지 않는다.

릴리즈 절차는 브랜치에서 `uv version --bump` -> PR -> develop -> develop→main PR 이다. 상세는 릴리즈 가이드가 단일 진실이다.

## Consequences

버전 불일치가 구조적으로 불가능하다. 사람이 tag 를 입력하지 않고 파일 값에서 파생되므로, 두 값을 대조하는 등가성 검증 단계 자체가 사라진다.

빌드가 자족적이다. `.git` 없이도 정확한 버전이 나오므로 컨테이너 빌드에 값 주입 경로가 필요 없고, 빌드 백엔드가 플러그인 없이 동작한다.

릴리즈마다 커밋과 PR 이 하나 늘어난다. tag 하나 push 로 끝나던 것이 bump 커밋 PR 을 거치게 된다. 이것이 ADR 0030 이 피하려던 비용이며, 위 이득과 교환한 것이다.

저장소 Actions 설정에 write 권한이 필요하다. 워크플로가 tag 를 push 하므로 전역 workflow permissions 가 read-only 면 실패한다. 전역 설정이 각 워크플로 `permissions:` 블록의 상한이기 때문이다.

main 에 머지가 들어올 때마다 워크플로가 발사된다. 버전이 그대로면 첫 job 에서 종료하므로 비용은 작지만, tag push 만 트리거하던 때보다 실행 횟수는 늘어난다.
