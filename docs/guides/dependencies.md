# 의존성 관리 (`pyproject.toml` + `uv.lock`)

본 문서는 본 repo 의 Python 의존성 관리 단일 진실. 의존성 추가·갱신·lockfile 동기화·Python 버전 변경·운영자 수동 bump 정책 모두 포함.

도구: [uv](https://docs.astral.sh/uv/) 0.4+. `pip` / `poetry` / `pipenv` 미사용 — 단일 도구로 통일.

## 1. 두 파일의 책임

| 파일 | 역할 | git |
|------|------|-----|
| `pyproject.toml` | 프로젝트 메타·dependency specifier·도구 설정 (PEP 621 + PEP 735) | 커밋 |
| `uv.lock` | resolved transitive dependency 트리 — reproducible install 단일 진실 | 커밋 |

`pyproject.toml` 만 있으면 사용자 마다 다른 transitive 버전 install (resolver 시점 의존). `uv.lock` 이 그 결과를 freeze — 같은 lockfile 로는 어디서나 정확히 같은 install.

CI (`ci.yml`·`alembic-check.yml`) 가 `uv sync --frozen` 사용 — `pyproject.toml` 과 `uv.lock` drift 시 fail. lockfile 갱신 누락이 즉시 노출.

## 2. `pyproject.toml` 구조

```toml
[build-system]
requires = ["uv_build>=0.11.16,<0.12.0"]
build-backend = "uv_build"

[project]
name = "assessment-engine"
version = "0.1.2"       # 릴리즈 시 `uv version --bump <part>` 가 이 값을 올린다
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.136.0",
    "uvicorn[standard]>=0.45.0",
    # ...
]

[dependency-groups]                       # PEP 735 (uv 권장)
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.15.13",
    # ...
]

[tool.pytest.ini_options] # ...
[tool.ruff] # ...
```

빌드 대상 설정은 없다. `uv_build` 가 `src/` 아래 패키지를 자동으로 잡고, `migrations/` 와 `_alembic.ini` 는 패키지 디렉토리 안에 있어 확장자와 무관하게 함께 포장된다.

### 운영 vs dev 의존성

- `[project].dependencies` — 운영 의존성 (wheel 동봉, prod 운영자가 받는 의존성). 모든 환경 의무.
- `[dependency-groups].dev` — dev 한정 (pytest·ruff·testcontainers 등). wheel metadata 미포함 — `pip install assessment-engine` 으로는 설치 안 됨. dev 환경에서 `uv sync --group dev` 로만 설치.

PEP 735 가 PEP 621 의 `[project.optional-dependencies]` 보다 정공 — dev 의존성이 wheel 에 박히지 않아 prod install 깔끔.

### Python 버전

`requires-python = ">=3.12"` + `[tool.ruff].target-version = "py312"`. 두 값 동시 갱신 의무 — drift 시 ruff 가 옛 syntax 허용/거부 잘못.

## 3. `uv.lock`

`uv sync` 또는 `uv lock` 호출 시 자동 생성·갱신. 사람이 직접 편집 X.

구조 (개략):
- `[[package]]` 블록 — transitive 의존성 별 entry (이름·버전·source·해시).
- `[package.dependencies]` — 그 package 가 의존하는 것들.
- `[package.dev-dependencies]` — `[dependency-groups].dev` 매핑.

git diff 보면 큰 lockfile 변경이 흔함 — transitive 트리 resolver 결과라 사용자 의도와 무관한 변경 다수. 운영자 review 어려움 — `pyproject.toml` 변경 + `uv lock` 결과로만 신뢰.

## 4. 운영 명령 카탈로그

### 일상 (운영자 관점)

```bash
# dev 의존성 + 운영 의존성 모두 설치 (가장 흔함)
uv sync --group dev

# CI 와 동일한 frozen install (lockfile drift 검증 + reproducible)
uv sync --frozen --group dev

# 운영 의존성만 (Docker prod 이미지 빌드 시점)
uv sync --no-dev

# 단일 명령 실행 (의존성 자동 sync 포함)
uv run pytest
uv run ruff check .
uv run alembic upgrade head
```

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

# pyproject.toml 수동 편집 후 lockfile 동기화 (dependabot PR merge 후 흐름)
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

CI matrix 확장 시 `.github/workflows/ci.yml` `setup-python` 의 `python-version` 도 동시 갱신 의무.

## 5. dependabot 미사용 정책

본 repo 는 GitHub Dependabot version updates 비활성 (`.github/dependabot.yml` 없음). 사유:

- Dependabot 이 `uv.lock` 직접 갱신 미지원 (uv 의 ecosystem 미통합) — PR 머지 시 `pyproject.toml` 만 갱신, `uv.lock` 은 drift 상태로 남음.
- CI `uv sync --frozen` 이 다음 PR 에서 drift fail → 운영자가 결국 수동 `uv lock` 실행 의무. 자동화의 이점 없음.
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
uv sync --group dev
uv run pytest
git add pyproject.toml uv.lock
git commit -m "chore(deps): fastapi bump 0.135 -> 0.136"
```

보안 알림은 GitHub Dependabot alerts + security updates (UI 활성, 자동 PR 없음 — 알림만)로 수신. CI 단계의 의존성 CVE 자동 gate 는 두지 않는다 — CVE 평가·대응(수정본 유무 판단·bump·예외 수용)은 Dependabot alerts 로 운영자가 판단.

## 6. 흐름·체크리스트

### 새 의존성 추가 시

1. `uv add <package>` (또는 `uv add --group dev <package>`)
2. `pyproject.toml` + `uv.lock` 동시 갱신 자동
3. `uv sync --group dev` 로 venv 설치
4. import + 동작 검증
5. commit (두 파일 함께)

### 의존성 bump 시

1. `uv lock --upgrade-package <name>` 또는 `uv lock --upgrade` (전체)
2. `uv.lock` diff review (transitive 영향 확인)
3. `uv sync --group dev`
4. `uv run pytest` (전체 회귀 검증)
5. commit (`pyproject.toml` + `uv.lock`)

### CI fail "lockfile drift" 대응

증상: PR CI 에서 `uv sync --frozen` 단계 fail — "lockfile is out of date".

원인: 누군가 `pyproject.toml` 만 편집하고 `uv.lock` 미갱신.

해결: `uv lock` 호출 → `uv.lock` 갱신 → commit + push.

## 관련 문서·코드

- `pyproject.toml` — 실제 구성 단일 진실
- `uv.lock` — resolved 트리
- `docs/guides/local-dev.md` "uv sync --frozen 패턴" — Docker 빌드 안 lockfile 사용
- `docs/guides/testing.md` — pytest 실행·fixture
- `docs/guides/release.md` — OCI 이미지 발행(GHCR)·서명·SBOM·provenance
- `.github/workflows/ci.yml`·`alembic-check.yml` — frozen sync CI 검증
- 현행 CI 산출물 = OCI 이미지 (결정 기록: `docs/decisions/adr/`)
