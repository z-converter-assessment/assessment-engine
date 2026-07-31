# 작업 인수인계 — docker 검증 대기

임시 자료 — 내부 인수인계 전용, 외부 공유 대상 아님. 검증이 끝나면 삭제한다.

맥락 이전용이다. 이전 환경에 docker 가 없어 이미지 빌드를 실행 검증하지 못했고, docker 가 있는 환경에서 이어받아 확인한다.

```bash
git clone git@github.com:z-converter-assessment/assessment-engine.git
cd assessment-engine
git checkout chore/docker-verification
```

## 현재 상태

PR #107 이 `develop` 에 squash 머지됐다 (`8247a56`). 이 브랜치 `chore/docker-verification` 은 그 최신 develop 에서 팠고, 본 문서 하나만 담고 있다.

머지 전 develop CI 5종이 통과했다.

```
pass       ruff + hadolint          29s
pass       pytest (unit)            5m45s      790개
pass       frontend typecheck       23s
pass       alembic-check            1m4s
pass       pr title + metadata      4s
skipping   wheel build              main PR 전용
skipping   pytest (integration)     main PR 전용
```

`wheel build` 와 `pytest (integration)` 은 develop PR 에서 발화하지 않으므로 아직 한 번도 돌지 않았다. main 승격 시 처음 돈다.

## 머지된 변경 (PR #107)

빌드 백엔드를 hatchling + hatch-vcs 에서 uv_build 로 옮겼다. 버전은 git tag derive 를 버리고 `pyproject.toml` 의 `version` 을 단일 진실로 삼는다. `migrations/` 와 `alembic.ini` 를 `src/assessment_engine/` 안으로 옮겨 `force-include` 설정을 없앴다.

Dockerfile 을 `uv sync` 2단계 + 가상환경 복사로 재작성했다. builder 가 `/opt/venv` 를 만들고 runtime 이 그것만 가져간다. CMD 기본값을 없애 컴포넌트를 항상 인자로 명시하게 했다.

릴리즈 트리거를 tag push 에서 main push 로 바꿨다. 워크플로가 `pyproject.toml` 에서 버전을 읽고, 이미 릴리즈된 버전이면 건너뛰고, 발행·서명이 끝난 뒤 마지막에 tag 를 남긴다.

검증 게이트를 되돌리기 비용에 맞춰 3단계로 나눴다. 로컬 커밋은 lint, develop PR 은 코드 리뷰와 단위 테스트, main PR 은 문서 정합과 통합 테스트다. 로컬 git hook 과 local-ci 스크립트는 제거했다.

## 홈서버에서 할 검증

docker 가 필요한 항목만 모았다. 순서대로 진행하고 결과를 이 문서에 기록한다.

### 1. 이미지 빌드

미확인 지점이 셋이다. uv 이미지 태그 `0.11.16` 이 실제로 존재하는지, `--mount=from=<stage>` 문법이 통하는지, `uv sync --no-editable` 로 만든 `/opt/venv` 를 복사해도 동작하는지.

```bash
docker build -t engine:verify .
```

실패하면 어느 단계인지 기록한다. uv 태그가 없으면 `docker manifest inspect ghcr.io/astral-sh/uv:0.11.16` 로 확인하고 존재하는 버전으로 바꾼다.

### 2. 최종 이미지 내용

builder 산출물이 안 섞였는지, 필요한 자원이 들어갔는지 본다.

```bash
# uv 바이너리·소스 트리가 없어야 한다
docker run --rm engine:verify -c "import shutil; print('uv:', shutil.which('uv'))"
docker run --rm --entrypoint sh engine:verify -c "ls /app; ls /build 2>&1 | head -1"

# 패키지와 자원은 있어야 한다
docker run --rm engine:verify -c "
import assessment_engine.web.main, assessment_engine.consumer.main, assessment_engine.worker.main
from importlib.resources import files
from importlib.metadata import version
p = files('assessment_engine')
print('version:', version('assessment-engine'))
for f in ('_alembic.ini','migrations/env.py','web/templates/base.html','web/static/js/chart-utils.js'):
    print(' ', f, (p / f).is_file())
"
```

`version` 은 `1.2.1` 이어야 한다.

### 3. ENTRYPOINT · CMD

CMD 기본값을 없앴으므로 인자 없이 실행하면 실패해야 한다.

```bash
docker run --rm engine:verify              # 실패 기대 (python -m 에 인자 없음)
docker run --rm engine:verify --version    # python -m --version
```

### 4. 라벨

정적 라벨이 Dockerfile 것으로 붙는지 본다. 릴리즈 이미지에는 워크플로가 `revision`·`version` 을 더한다.

```bash
docker inspect engine:verify --format '{{ json .Config.Labels }}' | python3 -m json.tool
```

`org.opencontainers.image.source` 가 `https://github.com/z-converter-assessment/assessment-engine` 이어야 한다.

### 5. compose 기동

dev override 의 bind mount 경로가 새 venv 위치(`/opt/venv/lib/python3.12/site-packages/assessment_engine`)와 맞는지, migrate 서비스가 `ALEMBIC_CONFIG` 로 도는지 확인한다.

```bash
cp env.dev.example .env
docker compose up -d --build
docker compose ps                  # migrate 가 exited(0), 나머지 running
docker compose logs migrate | tail -20
curl -s localhost:8000/health
```

bind mount 확인 — 호스트 소스를 고치면 컨테이너에 반영되는지.

```bash
docker compose exec web python -c "import assessment_engine; print(assessment_engine.__file__)"
```

### 6. 이미지 크기

wheel install 방식에서 venv 복사로 바뀌었다. 이전 대비 크기를 기록해둔다.

```bash
docker images engine:verify --format '{{.Size}}'
```

## 검증 결과

(홈서버에서 채운다)

| 항목 | 결과 | 비고 |
|------|------|------|
| 1. 이미지 빌드 | | |
| 2. 최종 이미지 내용 | | |
| 3. ENTRYPOINT·CMD | | |
| 4. 라벨 | | |
| 5. compose 기동 | | |
| 6. 이미지 크기 | | |

## 미결 항목

`wrap-up.md` Stage 1 의 `[1.7]~[1.15]` 가 규약 문서를 항목별로 재서술한다. 감사 권고는 규범(무엇이 금지인가)과 검사(어떻게 확인하나)를 갈라 9항목을 `조항 | grep 패턴` 표로 줄이는 것이다. 범위가 커 이번 PR 에서 제외했다.

머지 후 GitHub 설정 작업이 남는다. ruleset 3종 등록과 required status check 재등록이 필요하다 — job 이름이 `conventional commits` 에서 `pr title + metadata` 로 바뀌었다. 절차는 `docs/guides/ci-setup.md`.

`Restrict creations` 를 tag ruleset 에 켤 때 bypass 에 Actions 를 등록할 수 있는지 UI 에서 확인해야 한다. 등록 없이 켜면 릴리즈가 tag push 단계에서 실패한다.

## 원래 목표와의 관계

이 브랜치의 출발점은 collect 경로(에이전트 계약 -> 컨슈머 -> 매퍼 -> 저장)를 읽으며 파이썬을 익히는 것이었다. 계획은 `collect-review-notes.md` 에 8세션으로 정리되어 있고 아직 S1 도 시작하지 않았다.

관리 도구부터 보자는 판단으로 방향이 틀어졌고, 그 과정에서 빌드·릴리즈·CI 구조를 정석으로 재편하게 됐다. 패키징 개념은 `python-packaging-map.md` 에 5층 구조로 정리했다.

docker 검증이 끝나면 원래 계획으로 돌아간다 — S1 은 계약 문서와 JSON Schema 를 읽고 이슈 1(버전 게이트)·4(정본 강제력 부재)를 처리하는 세션이다.
