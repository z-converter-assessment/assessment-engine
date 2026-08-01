# ADR 0060 — 컨테이너 실행 명령을 compose 단일 소스로, 이미지 핀을 기동 조건으로

상태: Accepted (2026-08-01)

Refines: ADR 0035 (compose base/override 분리), ADR 0059 (compose 3파일 표준 배치)

## Context

ADR 0035 는 단일 이미지에 `ENTRYPOINT ["python", "-m"]` 을 두고 compose `command` 로 모듈명만 넘기는 구조를 세웠다. 같은 ADR 이 base 의 이미지 기본값을 `${ENGINE_IMAGE:-ghcr.io/.../assessment-engine:__ENGINE_VERSION__}` 으로 정하고, `__ENGINE_VERSION__` 은 release CI 가 태그 semver 로 치환한다고 규정했다.

두 전제가 모두 성립하지 않는다.

치환 스텝이 존재하지 않는다. ADR 0048 이 릴리즈 에셋 첨부를 폐기하면서 그 치환이 일어나던 자리도 함께 사라졌고, 현행 `release.yml` 에는 compose 를 가공하는 단계가 없다. `__ENGINE_VERSION__` 은 아무도 치환하지 않는 문자열로 남아, 실재하지 않는 태그를 가리키는 기본값이 되었다.

실행 명령이 두 파일에 흩어진다. 어느 컴포넌트가 뜨는지 알려면 Dockerfile 의 ENTRYPOINT 와 compose 의 command 를 합쳐 읽어야 한다. dev override 가 consumer·worker 를 watchfiles 로 감싸는 자리에서는 `entrypoint` 까지 덮어야 해서, 최종 argv 를 세 곳에서 조립하게 된다.

## Decision

이미지는 실행할 컴포넌트를 정하지 않는다.

- Dockerfile 에서 ENTRYPOINT 를 제거하고 `CMD []` 를 둔다. 빈 CMD 는 베이스 이미지가 물려주는 python REPL 을 지워, 명령 없이 실행하면 기동 대신 거부되게 한다.
- compose `command` 가 완결된 명령을 넘긴다 (`["python", "-m", "assessment_engine.web"]`). dev override 는 `entrypoint` 를 덮지 않고 command 한 줄로 watchfiles 호출을 담는다.
- base 의 이미지 기본값을 `${ENGINE_IMAGE:-}` 로 비운다. `__ENGINE_VERSION__` placeholder 를 폐기하고, 미핀 상태를 compose 파싱 단계에서 거부한다.

`:latest` 를 기본값으로 두지 않는다. 그 값은 compose 파일과 다른 버전의 이미지를 조용히 띄우고, `deploy.sh` 가 이미지 태그와 compose 토폴로지를 같은 git ref 로 묶는 구조를 무의미하게 만든다.

## Consequences

실행 형태가 compose 한 파일에서 읽힌다. 서비스마다 무엇이 뜨는지 알기 위해 ENTRYPOINT 와 command 의 결합 규칙을 알 필요가 없고, dev 가 hot reload 를 거는 자리도 command 교체 하나로 끝난다.

이미지 소비 인터페이스가 바뀐다. `docker run <image> assessment_engine.web` 이 더 이상 동작하지 않는다 — 호출자가 `docker run <image> python -m assessment_engine.web` 처럼 완결된 명령을 넘겨야 한다. 공식 배포 채널은 `deploy.sh` + compose 하나이고 그 경로는 영향을 받지 않으므로, 이 변경은 compose 밖에서 이미지를 직접 쓰는 소비자에게만 해당한다.

`ENGINE_IMAGE` 를 빠뜨린 배포는 기동하지 않는다. `deploy.sh` 는 그 줄을 항상 쓰므로 정상 경로에서는 마주칠 일이 없고, 수동 기동에서만 드러난다. 파싱 단계에서 멈추므로 컨테이너가 뜬 뒤 pull 실패로 발견되는 것보다 이르다.

`command` 가 길어진다. `python -m` 이 서비스마다 반복되지만, 분산된 조립보다 눈에 보이는 반복이 낫다는 판단이다.
