#!/usr/bin/env bash
# 로컬 CI 재현 — PR/push/release 전에 GitHub Actions 워크플로를 로컬에서 검증한다.
#
# CI 는 PR 대상별로 발화 범위가 다르다 — 모드를 그에 맞춘다:
#   develop PR/push : ruff + hadolint + unit + alembic + integration
#   main PR/release : 위 전부 + wheel build + codeql + release 산출물(액션버전·image build·compose 정합)
# release 파이프라인은 tag 가 찍혀야 도므로 "PR 만 보고 통과로 단정"하면 release 버그를 머지 후 발견한다
# (액션 버전 resolve 버그가 그렇게 늦게 드러난 적 있다). main 모드가 그 갈래까지 재현한다.
#
# 사용:
#   scripts/local-ci.sh --fast    # commit 전 빠른 회귀 (ruff + unit, docker 0)
#   scripts/local-ci.sh develop   # develop PR/push 대응 (+ hadolint·alembic·integration)
#   scripts/local-ci.sh main      # main PR/release 대응 (전부). 인자 없으면 main
#
# 모드 포함관계: fast ⊂ develop ⊂ main. 종료 코드: 실패 항목 있으면 1.

set -uo pipefail
cd "$(dirname "$0")/.."

MODE=main
case "${1:-}" in
  --fast|fast)       MODE=fast ;;
  develop|--develop) MODE=develop ;;
  main|--main|"")    MODE=main ;;
  *) echo "usage: $0 [--fast|develop|main]" >&2; exit 2 ;;
esac
case "$MODE" in fast) RANK=1 ;; develop) RANK=2 ;; main) RANK=3 ;; esac
need() { [ "$RANK" -ge "$1" ]; }   # need <min>: 현재 모드가 그 이상이면 실행 (fast=1 develop=2 main=3)

fail=0
section() { printf '\n\033[1m== %s ==\033[0m\n' "$1" 2>/dev/null || printf '\n== %s ==\n' "$1"; }
ok() { printf '  OK   %s\n' "$1"; }
ng() { printf '  NG   %s\n' "$1"; fail=1; }
skip() { printf '  SKIP %s\n' "$1"; }

echo "모드: $MODE"

# ─── 0. git hook 설치 (commit-msg 컨벤션·AI footer 차단 / pre-push main 보호 — 모든 모드) ───
# core.hooksPath 는 clone 별 로컬 설정 — 본 스크립트가 idempotent 하게 보장 (자주 실행되는 진입점).
section "git hook 설치"
if [ "$(git config core.hooksPath 2>/dev/null || true)" = ".githooks" ]; then
  ok "core.hooksPath = .githooks"
else
  git config core.hooksPath .githooks && ok "core.hooksPath -> .githooks 설정" || ng "core.hooksPath 설정 실패"
fi

# ─── 1. 워크플로 액션 버전 정합 (release — main 전용) ────────────────────────
section "workflow 액션 버전 정합"
if ! need 3; then
  skip "$MODE 모드 — release 검증은 main"
else
  if grep -rqnE 'uses:[^#]*@v[0-9]' .github/workflows/ 2>/dev/null; then
    ng "floating 태그(@vN) 잔존 — SHA 고정 필요 (액션이 floating major 를 안 만들면 resolve 실패)"
    grep -rnE 'uses:[^#]*@v[0-9]' .github/workflows/ | sed 's/^/      /'
  else
    ok "floating 태그 0 — 모든 액션 commit SHA 고정"
  fi
fi

# ─── 2. ruff lint (ci.yml lint job — 모든 모드) ─────────────────────────────
section "ruff lint"
uv run ruff check . >/dev/null 2>&1 && ok "ruff check" || ng "ruff check ('uv run ruff check .' 로 상세)"

# ─── 3. pytest unit (ci.yml test-unit job — 모든 모드) ──────────────────────
section "pytest unit"
if uv run pytest tests/unit -q >/dev/null 2>&1; then ok "unit 통과"; else ng "unit 실패 ('uv run pytest tests/unit' 로 상세)"; fi

# ─── 4. wheel build + smoke (ci.yml build job — main 전용) ──────────────────
section "wheel build + smoke"
if ! need 3; then
  skip "$MODE 모드 — wheel build 는 main"
else
  rm -rf dist
  if uv build >/dev/null 2>&1; then
    ok "uv build (wheel + sdist)"
    tmp=$(mktemp -d)
    # uv 관리 python 3.12 로 격리 venv — 호스트 PATH 에 python3.12 가 없어도 동작 (CI runner·로컬 동일).
    uv venv "$tmp/venv" --python 3.12 >/dev/null 2>&1
    uv pip install --python "$tmp/venv/bin/python" --quiet dist/*.whl >/dev/null 2>&1
    if "$tmp/venv/bin/python" -c "import assessment_engine.web.main, assessment_engine.consumer.main" >/dev/null 2>&1; then
      ok "2컴포넌트 import"
    else
      ng "wheel import 실패"
    fi
    if "$tmp/venv/bin/python" -c "from importlib.resources import files; p=files('assessment_engine'); assert (p/'migrations'/'env.py').is_file(); assert (p/'_alembic.ini').is_file()" >/dev/null 2>&1; then
      ok "migrations + alembic.ini 동봉"
    else
      ng "wheel 안 migrations/alembic.ini 누락"
    fi
    rm -rf "$tmp"
  else
    ng "uv build 실패"
  fi
fi

# ─── 5. compose config 정합 (배포 — main 전용) ──────────────────────────────
# 배포는 GHCR 이미지 pull compose (base+secrets). prod compose 가 ENGINE_IMAGE override 로 유효
# 파싱되는지 재현 — deploy.yml rollout 이 대상 VM 에서 실행할 compose 정합 (ADR 0048).
section "compose config (base+secrets, ENGINE_IMAGE override)"
if ! need 3; then
  skip "$MODE 모드 — compose 정합은 main"
elif ENGINE_IMAGE=ghcr.io/x/assessment-engine:v0 ENV_FILE=env.example \
     docker compose -f docker-compose.yml -f docker-compose.secrets.yml config >/dev/null 2>&1; then
  ok "prod compose (base+secrets) config 유효"
else
  ng "prod compose config 실패 — 'docker compose -f docker-compose.yml -f docker-compose.secrets.yml config'"
fi

# ─── 6. pytest integration (ci.yml test-integration — develop 이상, docker) ──
section "pytest integration (testcontainers)"
if ! need 2; then
  skip "$MODE 모드 — integration 은 develop 이상"
elif docker info >/dev/null 2>&1; then
  if uv run pytest tests/integration -q >/dev/null 2>&1; then ok "integration 통과 (alembic upgrade 포함)"; else ng "integration 실패 ('uv run pytest tests/integration' 로 상세)"; fi
else
  skip "docker 미가용 (testcontainers 불가)"
fi

# ─── 6b. alembic check — ORM vs migrations drift (alembic-check.yml — develop 이상) ─
# integration 의 upgrade 와 별개로, 모델만 바꾸고 마이그레이션을 누락한 drift 를 검출한다.
section "alembic check (ORM-migration drift)"
if ! need 2; then
  skip "$MODE 모드 — alembic check 는 develop 이상"
elif docker info >/dev/null 2>&1; then
  cid=$(docker run -d -e POSTGRES_DB=ci -e POSTGRES_USER=ci -e POSTGRES_PASSWORD=ci -P timescale/timescaledb-ha:pg16 2>/dev/null)
  if [ -n "$cid" ]; then
    port=$(docker port "$cid" 5432/tcp | head -1 | sed 's/.*://')
    ready=0   # timescaledb-ha 는 init 후 본 서버로 재기동 — asyncpg 실연결로 ready 판정
    for _ in $(seq 1 40); do
      if uv run python -c "import asyncio,asyncpg; asyncio.run(asyncpg.connect(host='localhost',port=$port,user='ci',password='ci',database='ci')).close()" >/dev/null 2>&1; then ready=1; break; fi
      sleep 1
    done
    if [ "$ready" = 1 ]; then
      env APP_ENV=dev POSTGRES_HOST=localhost POSTGRES_PORT="$port" POSTGRES_DB=ci POSTGRES_USER=ci POSTGRES_PASSWORD=ci uv run alembic upgrade head >/dev/null 2>&1
      if env APP_ENV=dev POSTGRES_HOST=localhost POSTGRES_PORT="$port" POSTGRES_DB=ci POSTGRES_USER=ci POSTGRES_PASSWORD=ci uv run alembic check >/dev/null 2>&1; then
        ok "alembic upgrade head + check (drift 0)"
      else
        ng "alembic check 실패 — 모델 변경 후 마이그레이션 누락? 'alembic check' 로 상세"
      fi
    else
      ng "postgres 기동 대기 timeout"
    fi
    docker rm -f "$cid" >/dev/null 2>&1
  else
    skip "postgres 컨테이너 기동 실패"
  fi
else
  skip "docker 미가용"
fi

# ─── 8. hadolint Dockerfile (ci.yml lint job — develop 이상, docker) ────────
section "hadolint Dockerfile"
if ! need 2; then
  skip "$MODE 모드 — hadolint 는 develop 이상"
elif docker info >/dev/null 2>&1; then
  if docker run --rm -i hadolint/hadolint hadolint --failure-threshold warning - < Dockerfile >/dev/null 2>&1; then
    ok "hadolint 통과 (warning 이상 0)"
  else
    ng "hadolint 위반 — 'docker run --rm -i hadolint/hadolint hadolint - < Dockerfile' 로 상세"
  fi
else
  skip "docker 미가용"
fi

# ─── 9. docker image build (release.yml release-image 의 빌드 step — main 전용) ─
# multi-arch push 는 GHCR 인증이 필요해 제외하고, 호스트 arch 로 이미지 빌드까지 재현.
section "docker image build (Dockerfile)"
if ! need 3; then
  skip "$MODE 모드 — docker image 는 main"
elif docker info >/dev/null 2>&1; then
  if docker build -t assessment-engine:local-ci -f Dockerfile . >/dev/null 2>&1; then
    ok "docker build (2컴포넌트 단일 이미지)"
  else
    ng "docker build 실패 — 'docker build -f Dockerfile .' 로 상세"
  fi
else
  skip "docker 미가용"
fi

# ─── 10. codeql SAST (codeql.yml — python, security-extended — main 전용) ────
section "codeql SAST (python, security-extended)"
if ! need 3; then
  skip "$MODE 모드 — codeql 은 main"
else
  CODEQL=""
  if command -v codeql >/dev/null 2>&1; then CODEQL="codeql"
  elif gh codeql version >/dev/null 2>&1; then CODEQL="gh codeql"; fi
  if [ -n "$CODEQL" ]; then
    cdb=$(mktemp -d)
    $CODEQL pack download codeql/python-queries >/dev/null 2>&1
    if $CODEQL database create "$cdb/db" --language=python --source-root=. --overwrite >/dev/null 2>&1 \
       && $CODEQL database analyze "$cdb/db" --format=sarif-latest --output="$cdb/r.sarif" \
            codeql/python-queries:codeql-suites/python-security-extended.qls >/dev/null 2>&1; then
      n=$(python3 -c "import json; d=json.load(open('$cdb/r.sarif')); print(sum(len(r.get('results',[])) for r in d['runs']))" 2>/dev/null || echo '?')
      if [ "$n" = 0 ]; then ok "codeql alert 0 (security-extended)"; else ng "codeql alert $n 건 — $cdb/r.sarif"; fi
    else
      ng "codeql 실행 실패 (pack: '$CODEQL pack download codeql/python-queries')"
    fi
    rm -rf "$cdb"
  else
    skip "codeql 미설치 (gh extension install github/gh-codeql)"
  fi
fi

# ─── CI 전용 (본질적 로컬 재현 불가) — 인지용 안내 ───────────────────────────
# 아래는 GitHub OIDC 토큰·레지스트리 인증·GitHub 이벤트 컨텍스트가 필수라 로컬 자동 재현 불가.
section "CI 전용 (OIDC·인증·이벤트 필요 — 안내만)"
skip "cosign 이미지 서명 + SBOM/provenance attestation — GitHub OIDC 토큰 필요"
skip "GHCR push — 레지스트리 인증 + 외부 부작용"
skip "deploy.yml rollout — self-hosted runner + production Environment 승인 (실 VM)"
skip "pr-title-check — GitHub PR 이벤트 컨텍스트 필요 (릴리즈는 main 에 tag push, ADR 0030)"

# ─── 결과 ───────────────────────────────────────────────────────────────────
section "결과"
rm -rf dist
if [ "$fail" = 0 ]; then
  echo "  $MODE 모드 전부 통과"
  exit 0
else
  echo "  실패 항목 있음 — 위 NG 수정 후 재실행"
  exit 1
fi
