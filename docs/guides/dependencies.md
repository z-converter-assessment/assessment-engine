# 의존성 관리 (`pyproject.toml` + `uv.lock`)

본 문서는 본 repo 의 Python 의존성 관리 단일 진실. 의존성 추가·갱신·lockfile 동기화·Python 버전 변경·운영자 수동 bump 정책 모두 포함.

도구: [uv](https://docs.astral.sh/uv/) 단일 — `pip` / `poetry` / `pipenv` 미사용. 쓸 수 있는 계열은 `pyproject.toml` `[build-system]` 의 `uv_build` 제약과 `Dockerfile` 의 이미지 핀이 정한다.

## 1. 두 파일의 책임

| 파일 | 역할 | git |
|------|------|-----|
| `pyproject.toml` | 프로젝트 메타·dependency specifier·도구 설정 (PEP 621 + PEP 735) | 커밋 |
| `uv.lock` | resolved transitive dependency 트리 — reproducible install 단일 진실 | 커밋 |

`pyproject.toml` 만 있으면 사용자 마다 다른 transitive 버전 install (resolver 시점 의존). `uv.lock` 이 그 결과를 freeze — 같은 lockfile 로는 어디서나 정확히 같은 install.

CI (`ci.yml`·`alembic-check.yml`) 가 `uv sync --frozen` 으로 설치한다 — lockfile 을 재해석하지 않고 그대로 써서 빌드 시점과 무관하게 같은 버전 집합이 깔린다. drift 자체를 실패로 잡는 것은 `uv lock --check` 이며 현재 CI 에는 없다.

## 2. `pyproject.toml` 구조

빌드 대상 설정은 없다. `uv_build` 가 `src/` 아래 패키지를 자동으로 잡고, `migrations/` 와 `_alembic.ini` 는 패키지 디렉토리 안에 있어 확장자와 무관하게 함께 포장된다.

### 운영 vs dev 의존성

- `[project].dependencies` — 운영 의존성 (wheel 동봉, prod 운영자가 받는 의존성). 모든 환경 의무.
- `[dependency-groups].dev` — dev 한정 (pytest·ruff·testcontainers 등). wheel metadata 미포함 — `pip install assessment-engine` 으로는 설치 안 됨. dev 환경에서 `uv sync --group dev` 로만 설치.

PEP 735 가 PEP 621 의 `[project.optional-dependencies]` 보다 정공 — dev 의존성이 wheel 에 박히지 않아 prod install 깔끔.

### Python 버전

`requires-python` 과 `[tool.ruff].target-version` 은 같은 minor 를 가리켜야 한다 — drift 시 ruff 가 옛 syntax 를 잘못 허용·거부한다. 올리는 절차는 4절 "Python 버전 변경".

## 3. `uv.lock`

`uv sync` 또는 `uv lock` 호출 시 자동 생성·갱신. 사람이 직접 편집 X.

## 4. 운영 명령 카탈로그

### 일상 (운영자 관점)

설치는 `make setup` 이다 — lockfile 을 그대로 쓰는 frozen install 이라 워크플로와 같은 버전 집합이 깔린다. 검사·테스트도 make 타깃이 있다 (`make help`).

아래는 make 타깃이 없는 변형이다.

```bash
# lockfile 을 재해석해 설치 (범위 안에서 새 버전을 잡을 수 있다)
uv sync --group dev

# 운영 의존성만 (Docker prod 이미지 빌드 시점)
uv sync --no-dev
```

alembic 은 설정 파일이 패키지 안에 있어 호출 측이 경로를 줘야 한다 — 명령 형태는 `docs/guides/migrate.md` "명령" 절.

### 의존성 추가·갱신

```bash
# 운영 의존성 추가 — [project].dependencies 에 자동 entry + uv.lock 갱신
uv add httpx

# 운영 의존성 + 버전 제약
uv add 'fastapi>=0.136.0'

# dev 그룹 추가
uv add --group dev pytest-mock

# 특정 의존성만 최신 버전으로 (transitive 함께 resolve)
uv lock --upgrade-package fastapi

# 모든 의존성 최신 resolve (큰 변경, PR review 부담)
uv lock --upgrade

# pyproject.toml 수동 편집 후 lockfile 동기화
uv lock
```

### Python 버전 변경

```toml
# pyproject.toml
requires-python = ">=3.13"           # 1. 운영 의존성 호환 범위 변경

[tool.ruff]
target-version = "py313"             # 2. ruff modernize 룰 정합
```

```bash
uv sync --group dev                  # 3. lockfile 재-resolve (Python 3.13 wheel 선택)
```

같은 minor 가 워크플로에도 박혀 있어 함께 고친다 — `ci.yml` 은 job 마다 `setup-python` 을 따로 두므로 전 job 을 훑고, `alembic-check.yml`·`release.yml` 도 같은 값을 갖는다. 이미지 쪽은 `docs/reference/docker.md` 가 소유한다.

## 5. dependabot 미사용 정책

본 repo 는 GitHub Dependabot version updates 비활성 (`.github/dependabot.yml` 없음). 사유:

- Dependabot 이 `uv.lock` 직접 갱신 미지원 (uv 의 ecosystem 미통합) — PR 머지 시 `pyproject.toml` 만 갱신, `uv.lock` 은 drift 상태로 남음.
- lockfile 이 갱신되지 않은 채 머지되면 결국 운영자가 수동으로 `uv lock` 을 돌려야 한다. 자동화의 이점이 없다.
- 의존성 PR 폭주 + 자동 merge 패턴이 운영 흐름 방해.

대안 — 운영자 수동 주기 검토:

```bash
# 1. 현재 lockfile 의 outdated 패키지 확인
uv tree --outdated

# 2. 특정 패키지만 bump (보안 알림 따라)
uv lock --upgrade-package fastapi

# 3. 또는 전체 transitive resolve (보수적 주기 — 분기·반기)
uv lock --upgrade

# 4. 테스트 + commit
make setup && make test
git add pyproject.toml uv.lock
git commit -m "chore(deps): fastapi bump 0.135 -> 0.136"
```

보안 알림은 GitHub Dependabot alerts 로 수신한다 (Security 탭). 자동 PR 을 여는 security updates·version updates 는 둘 다 비활성 — 위 사유가 양쪽에 동일하게 적용된다. 설정 상태와 조회 명령은 `docs/guides/ci-setup.md` 4.2 가 소유한다.

CI 단계의 의존성 CVE 자동 gate 는 두지 않는다 — CVE 평가·대응(수정본 유무 판단·bump·예외 수용)은 alert 를 보고 운영자가 판단한다.

## 관련 문서·코드

- `pyproject.toml` — 실제 구성 단일 진실
- `uv.lock` — resolved 트리
- `docs/reference/docker.md` "uv 플래그" — Docker 빌드 안 lockfile 사용
- `docs/guides/testing.md` — pytest 실행·fixture
- `docs/guides/release.md` — OCI 이미지 발행(GHCR)·서명·SBOM·provenance
- `.github/workflows/ci.yml`·`alembic-check.yml` — frozen sync CI 검증
- `.github/workflows/release.yml` — OCI 이미지 발행
