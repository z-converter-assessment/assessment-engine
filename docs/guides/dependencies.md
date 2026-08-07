# 의존성 관리 (`pyproject.toml` + `uv.lock`)

본 문서는 본 repo 의 Python 의존성 관리 단일 진실.

도구: [uv](https://docs.astral.sh/uv/) 단일 — `pip` / `poetry` / `pipenv` 미사용. 쓸 수 있는 계열은 `pyproject.toml` `[build-system]` 의 `uv_build` 제약과 `Dockerfile` 의 이미지 핀이 정한다.

## 1. 두 파일의 책임

| 파일 | 역할 | git |
|------|------|-----|
| `pyproject.toml` | 프로젝트 메타·dependency specifier·도구 설정 (PEP 621 + PEP 735) | 커밋 |
| `uv.lock` | resolved transitive dependency 트리 — reproducible install 단일 진실 | 커밋 |

`pyproject.toml` 만 있으면 사용자 마다 다른 transitive 버전 install (resolver 시점 의존). `uv.lock` 이 그 결과를 freeze — 같은 lockfile 로는 어디서나 정확히 같은 install.

CI (`ci.yml`·`alembic-check.yml`) 가 `uv sync --locked` 로 설치한다 — lockfile 을 재해석하지 않고 그대로 써서 빌드 시점과 무관하게 같은 버전 집합이 깔린다. `--locked` 는 lockfile 이 `pyproject.toml` 과 어긋나면 설치 자체를 실패시킨다. `ci.yml` 의 `uv lock --check` 는 lint job 한 곳이라, 그 job 을 타지 않는 워크플로도 스스로 drift 를 잡게 하려는 것이다.

## 2. `pyproject.toml` 구조

빌드 대상 설정은 없다. `uv_build` 가 `src/` 아래 패키지를 자동으로 잡고, `migrations/` 와 `_alembic.ini` 는 패키지 디렉토리 안에 있어 확장자와 무관하게 함께 포장된다.

### 운영 vs dev 의존성

- `[project].dependencies` — 운영 의존성 (wheel 동봉, prod 운영자가 받는 의존성). 모든 환경 의무.
- `[dependency-groups].dev` — dev 한정 (pytest·ruff·testcontainers 등). wheel metadata 미포함 — `pip install assessment-engine` 으로는 설치 안 됨. dev 환경에서 `uv sync --group dev` 로만 설치.

dev 그룹을 PEP 735 로 두는 이유는 그 선언이 wheel 에 박히지 않아 prod install 이 운영 의존성만 받기 때문이다.

### Python 버전

`requires-python` · `[tool.ruff].target-version` · `[tool.pyright].pythonVersion` · `.python-version` 이 같은 minor 를 가리켜야 한다 — drift 시 ruff 가 옛 syntax 를 잘못 허용·거부하고 pyright 가 다른 표준 라이브러리 시그니처를 본다. 올리는 절차는 4절 "Python 버전 변경".

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

alembic 은 `python -m assessment_engine.migrate` 진입점으로만 부른다 — 명령 형태는 `docs/guides/migrate.md` "명령" 절.

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
requires-python = ">=3.15"           # 1. 운영 의존성 호환 범위

[tool.ruff]
target-version = "py315"             # 2. ruff modernize 룰 정합

[tool.pyright]
pythonVersion = "3.15"               # 3. 표준 라이브러리 시그니처 기준
```

```bash
uv python pin 3.15                   # 4. .python-version (uv 가 쓰는 인터프리터)
uv lock && uv sync --all-groups      # 5. lockfile 재-resolve (해당 minor wheel 선택)
```

같은 minor 가 워크플로에도 박혀 있어 함께 고친다 — `ci.yml` 은 job 마다 `setup-python` 을 따로 두므로 전 job 을 훑고, `alembic-check.yml`·`release.yml` 도 같은 값을 갖는다. 이미지 쪽은 `docs/reference/docker.md` 가 소유한다.

의존성 floor 는 실제로 resolve 된 버전으로 올린다 — 검증한 조합과 선언이 갈리면 lockfile 없이 설치한 환경이 테스트 안 된 조합을 받는다.

## 5. 버전 고정 정책 — 자동 갱신을 두지 않는다

의존성·베이스 이미지·액션 버전을 한 상태로 고정한다. Dependabot 의 자동 PR(version updates·security updates)은
쓰지 않고 `.github/dependabot.yml` 도 두지 않는다.

배포 산출물이 단일 이미지이고 배포 주기가 릴리즈 단위라 "문제가 없는데 버전만 올리는" 변경은 검증 비용만
만든다. 고정해 두면 로컬·CI·이미지가 같은 것으로 빌드한다는 사실을 매번 확인할 필요가 없다.

갱신은 사유가 있을 때만 한다 — 취약점 공지, 필요한 기능, 파이썬 minor 승격. 그때는 결합된 자리를 함께 올린다
(아래 uv 표).

Dependabot alerts(Security 탭 경고)는 켜 둔다. 자동 PR 과 별개 토글이고, 이 정책이 성립하려면 "문제가 생겼다"는
신호는 남아야 한다. 경고를 받으면 그때 사람이 판단해 올린다.

### 취약점 신호 채널

alerts 가 보는 것은 GitHub 의존성 그래프가 파싱한 선언뿐이다 — `uv.lock`(dev 그룹 포함)·`pnpm-lock.yaml`·워크플로의
`uses`. Dockerfile 의 `FROM` 은 대상이 아니라, digest 로 고정한 베이스 이미지 안 OS 패키지(glibc·openssl 등)는
alerts 로 오지 않는다. 고정 정책에서 이 계층은 스스로 낡는다 — 우리가 안 건드려도 debian 이 보안 갱신을 계속 낸다.

그 계층은 `image-scan.yml` 이 맡는다. 주 1회 GHCR 에 발행된 이미지를 trivy 로 스캔해 결과를 Security 탭 code
scanning alert 로 올린다. Dockerfile 이 아니라 발행된 이미지를 보는 이유는 빌드가 실제로 무엇을 담았는지가
결과물에만 있어서다. 게이트가 아니라 신호라서 판단은 그대로 사람 몫이고, 이 정책이 요구하는 "사유" 를 공급하는
쪽이다. 수정 있는 항목(`ignore-unfixed`)만 올린다 — debian 이 no-DSA 로 두는 건은 조치할 수 없어 신호를 덮는다.

| 계층 | 채널 | 무엇을 본다 |
|------|------|------------|
| 우리 코드 | `codeql.yml` | 취약 패턴 (SAST) |
| 의존성 | Dependabot alerts | lockfile 에 적힌 패키지 (SCA) |
| 베이스 이미지 | `image-scan.yml` | 이미지 안 OS 패키지 |
| 커밋 내용 | secret scanning | 커밋된 provider 토큰 |

스캔 대상은 `:latest` 이므로 가장 최근 발행본을 본다. 배포 중인 버전과 같지 않을 수 있다 — `deploy.sh` 는 인자로 받은
`vX.Y.Z` 를 핀하고, `latest` 태그는 `workflow_dispatch` 재발행에는 붙지 않는다. 다음에 배포할 것을 미리 보는
용도로 읽는다.

발화 조건 자체의 한계는 `docs/reference/automation.md` "시간으로 도는 것" 절이 갖는다.

### uv 버전은 세 자리가 함께 움직인다

| 자리 | 무엇 |
|------|------|
| `Dockerfile` `FROM ghcr.io/astral-sh/uv:X` | 이미지 빌드가 쓰는 uv |
| 워크플로 `astral-sh/setup-uv` 의 `version:` (전 job) | runner 가 쓰는 uv |
| `pyproject.toml` `requires = ["uv_build>=X,<Y"]` | 빌드 백엔드 |

셋이 어긋나면 로컬·CI·이미지가 서로 다른 uv 로 빌드한다. `uv_build` 상한(`<Y`)이 새 minor 를 배제하면 이미지의
uv 만 올라가고 백엔드는 옛 버전이 깔린다 — 빌드는 통과하므로 조용하다.

베이스 이미지 digest 핀도 같은 정책의 일부다. 태그만 쓰면 같은 커밋이 시점마다 다른 이미지로 빌드된다.

사유가 생겼을 때 운영자가 도는 절차:

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

하한을 고정해야 하면 `pyproject.toml` 에 직접 선언해 그 줄에 근거를 남긴다 — 전이 의존이라도 직접 선언하면 핀을 걸 자리가 생긴다. alerts 설정 위치와 조회 명령은 `docs/guides/ci-setup.md` 4.2 가 갖는다.

CI 단계의 의존성 CVE 자동 gate 는 두지 않는다 — CVE 평가·대응(수정본 유무 판단·bump·예외 수용)은 alert 를 보고 운영자가 판단한다.

## 관련 문서·코드

- `docs/reference/docker.md` "uv 플래그" — Docker 빌드 안 lockfile 사용
- `docs/guides/testing.md` — pytest 실행·fixture
- `docs/guides/release.md` — OCI 이미지 발행(GHCR)·서명·SBOM·provenance
- `.github/workflows/ci.yml`·`alembic-check.yml` — frozen sync CI 검증
- `.github/workflows/release.yml` — OCI 이미지 발행
