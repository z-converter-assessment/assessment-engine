# 자동화 발화 지도

무엇이 언제 도는지 한 장. 각 항목이 무엇을 검증하는지는 루트 `README.md` "워크플로" 절, required status check 등록은 `docs/guides/ci-setup.md` 3.4, 저장소 설정 토글은 같은 문서 4.2·4.3 이 갖는다.

두 종류가 섞여 있다. 워크플로는 GitHub Actions 러너에서 돌고 `.github/workflows/` 의 파일이 발화 조건을 정한다. 플랫폼 기능은 GitHub 자기 인프라에서 돌고 발화 조건을 우리가 정하지 않는다 — Actions 사용량도 로그도 남지 않는다.

## 한눈에

| 무엇 | 종류 | 발화 시점 | 결과 |
|------|------|----------|------|
| `pr title + metadata` | 워크플로 | main·develop PR 의 열림·재열림·제목 수정·새 커밋 | PR check |
| `ruff + hadolint` · `pytest (unit)` · `frontend typecheck` | 워크플로 | main·develop PR 의 새 커밋마다 | PR check |
| `wheel build` · `pytest (integration)` | 워크플로 | main PR 만 (`base_ref == 'main'`) | PR check |
| `alembic-check` | 워크플로 | main·develop PR 의 새 커밋마다 | PR check |
| `codeql` | 워크플로 | main PR 만 | Security 탭 |
| `release` | 워크플로 | main push, 또는 수동 dispatch | GHCR 이미지 + `vX.Y.Z` tag |
| `image-scan` | 워크플로 | 매주 월 03:00 KST, 또는 수동 dispatch | Security 탭 |
| Dependabot alerts | 플랫폼 | 상시 — 새 advisory 가 lockfile 의 패키지와 맞는 순간 | Security 탭 |
| Secret scanning | 플랫폼 | 모든 브랜치 push 직후 + 활성화 시점 전체 이력 1회 | Security 탭 |
| Push protection | 플랫폼 | `git push` 순간 | push 거부 |
| Ruleset (main·develop·tag) | 플랫폼 | 머지·force push·삭제 시도 순간 | 차단 |

## 막는 것과 알리는 것

| 막는다 | 알린다 |
|--------|--------|
| required status check (ci-setup 3.4 목록) | `codeql` |
| Ruleset | `image-scan` |
| Push protection | Dependabot alerts |
| | Secret scanning |

알리는 쪽은 결과가 Security 탭 alert 으로만 남는다. 새 취약점 공개가 무관한 릴리즈를 막지 않게 한 것이고, 판단은 사람이 한다. 예외가 push protection 하나인데, 비밀은 유출을 되돌릴 수 없어 사후 경고의 가치가 낮아 막는 쪽에 뒀다.

## PR 에서 도는 것이 base 에 따라 갈린다

develop PR 은 5개, main PR 은 그 5개에 `wheel build`·`pytest (integration)` 이 붙고 `codeql` 이 따로 돈다. 무거운 것을 승격 직전에만 돌린다 — 통합 테스트는 testcontainers 로 DB·broker 를 띄우고, CodeQL 은 `security-extended` 쿼리라 오래 걸린다.

`paths` 조건은 어느 워크플로에도 없다. 이유는 `docs/guides/ci-setup.md` 3.4.

## main push 가 곧 릴리즈는 아니다

`release.yml` 은 main push 마다 돌지만 이미지를 항상 내지는 않는다. `resolve-version` job 이 `pyproject.toml` 의 version 을 읽어 `vX.Y.Z` tag 가 이미 있는지 보고, 있으면 `release=false` 로 나머지 job 을 건너뛴다.

즉 발행 조건은 "main 에 머지" 가 아니라 "main 에 머지 + version 이 아직 tag 없는 값" 이다. 문서 수정만 올린 승격은 워크플로가 돌고 조용히 끝난다.

`workflow_dispatch` 는 이 판정을 건너뛴다 — 이미지 유실·재서명처럼 같은 버전을 다시 발행할 때 쓴다.

## 시간으로 도는 것은 하나뿐

`image-scan` 만 schedule 이다. 이미지가 바뀌어서가 아니라 취약점 DB 가 자라서 생기는 신호라 시간에 맞췄다 — 근거는 `docs/guides/dependencies.md` 5절.

schedule 워크플로는 기본 브랜치에서만 발화한다. `main` 에 올라가기 전에는 develop 에 파일이 있어도 돌지 않고, `workflow_dispatch` 수동 실행도 같은 제약을 받아 미리 시험할 수 없다.

GitHub 은 public repo 에 60일간 활동이 없으면 schedule 워크플로를 자동 비활성화한다. 버전 고정 정책은 저장소가 조용한 상태를 전제하므로, 이 채널은 가장 필요한 국면에서 조용히 멈출 수 있다. 릴리즈 간격이 두 달을 넘기면 Actions 탭에서 마지막 `image-scan` 실행 시각을 확인한다.

실패해도 신호가 없다. trivy DB 를 못 받아 job 이 죽으면 SARIF 를 안 올리고 기존 alert 이 그대로 남는데, 화면상으로는 "새 취약점 없음" 과 구분되지 않는다. 위 실행 시각 확인이 이 경우도 함께 덮는다.

## 관련 문서

- 워크플로가 무엇을 검증하나: 루트 `README.md` "워크플로" 절
- required status check 등록 목록: `docs/guides/ci-setup.md` 3.4
- 플랫폼 기능 토글 값과 근거: `docs/guides/ci-setup.md` 4.2·4.3
- 취약점 신호 채널 분담: `docs/guides/dependencies.md` 5절
