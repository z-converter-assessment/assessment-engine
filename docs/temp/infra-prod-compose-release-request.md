# engine 레포 prod compose release artifact 요청 스펙 (infra 인바운드)

> 출처: infra 팀 (배포 B안 — pull-and-run prod compose)
> 대상 레포: `z-converter-assessment/assessment-engine`
> 작성 배경: 0.3.1 배포 실패 원인 분석 -> infra(B안)는 엔진 레포에서 pull-and-run 가능한 prod compose 를 릴리즈 에셋으로 제공받는다.
> 본 문서는 폐기 대상 — 엔진 레포가 아래 요건을 충족하는 릴리즈를 내면 삭제한다. (temp 디렉토리, self-contained, 인덱스 미등록)

## 1. 문제: 현행 0.3.1 `docker-compose.yml`은 dev/퀵스타트 전용

infra 파이프라인은 "GHCR 선빌드 이미지 pull -> compose up" 모델(ADR-0010·0011)인데, 릴리즈 compose 는 "repo clone -> 로컬 빌드 -> dev"용이라 단일 인스턴스 배포에서 깨진다.

| # | 현행 compose | 깨지는 지점 |
|---|---|---|
| 1 | `image: ${ENGINE_IMAGE:-assessment-engine:local}` + `build: context: .` | `ENGINE_IMAGE` 미주입 시 `assessment-engine:local`(로컬 전용)로 fallback -> `docker compose pull` 실패. compose_dir 에 Dockerfile·src 없어 build 도 실패 |
| 2 | `volumes: - ./src/assessment_engine:/usr/local/lib/python3.12/site-packages/assessment_engine` | VM 에 `./src` 가 없어 빈 디렉토리가 설치 패키지를 덮음 -> `python -m assessment_engine.*` 모듈 소멸 -> 컨테이너 즉사 |
| 3 | `migrate` init-container 도 `build: context: .` | 마이그레이션 컨테이너도 빌드 시도 -> 실패 (GHCR 이미지 미사용) |
| 4 | named volume `postgres_data`/`rabbitmq_data` | infra 가 Cinder 볼륨(/mnt/pgdata·/mnt/mqdata)에 영속화하려는데 compose 가 이를 참조 안 함 -> root 디스크에 저장, Cinder 미사용 |
| 5 | 이미지가 GHCR private (익명 pull 403) | infra 워크플로우의 `secrets.GITHUB_TOKEN` 은 infra 레포 스코프 -> cross-repo pull denied |

## 2. 요청 사항 (prod compose 요건)

### 2.1 빌드 없는 이미지 고정
- prod compose 의 앱 서비스(web·consumer·migrate·diagnostic-worker)는 `build:` 키 제거, `image:` 만 사용.
- 기본 이미지를 `ghcr.io/z-converter-assessment/assessment-engine:<version>` 으로 지정 (퀵스타트 fallback `:local` 금지).
- 권장: `image: ${ENGINE_IMAGE:-ghcr.io/z-converter-assessment/assessment-engine:<릴리즈버전>}` 형태로, env 미주입 시에도 GHCR 를 가리키게.
- 이미지 태그 규약 명시: 릴리즈 태그는 `v0.3.1` 이나 wheel 파일명은 `0.3.1`. 이미지 태그가 `0.3.1` 인지 `v0.3.1` 인지 문서화 요청 (infra 는 `engine_version`=`0.3.1` 기준으로 태그 조립).

### 2.2 dev 전용 요소 제거 또는 profile 격리
- prod 에서 `./src/...` bind mount 제거. (단일 파일 유지 시 `profiles: [dev]` 로 격리해 prod 에서 활성화 안 되게.)
- 단일 파일로 dev·prod 겸용을 고집한다면 compose profiles 로 분리하고, prod 활성 profile·`COMPOSE_PROFILES` 사용법을 release note 에 명기.
- 가장 깔끔한 형태: prod 전용 compose 를 별도 에셋으로 릴리즈 (`docker-compose.prod.yml` 또는 `compose.release.yml`).

### 2.3 영속 스토리지를 외부 볼륨에 바인딩 가능하게
- postgres·rabbitmq 데이터 경로를 env 로 host bind mount 지정 가능하게:
  - 예) `- ${PGDATA_HOST:-postgres_data}:/home/postgres/pgdata/data`
  - 예) `- ${MQ_DATA_HOST:-rabbitmq_data}:/var/lib/rabbitmq`
- infra 는 `PGDATA_HOST=/mnt/pgdata`, `MQ_DATA_HOST=/mnt/mqdata`(Cinder)로 주입할 예정. 최소한 named volume 이름이라도 고정·문서화.

### 2.4 diagnostic-worker 포함 + Ollama 외부 주입
- diagnostic-worker 는 prod compose 에 포함 (단일 노드 모델, ADR-0010). AI VM 은 Ollama 데몬만 호스팅.
- `OLLAMA_BASE_URL`·`OLLAMA_MODEL` 은 env 주입식 유지 (compose 내 ollama 서비스에 하드코딩 금지). infra 가 AI VM 사설 IP 를 주입한다.
- 미주입 시 narrative pending 으로 graceful degrade 동작 유지(현행 주석대로) 확인.

### 2.5 GHCR 이미지 접근
- 둘 중 하나 선택해 명시:
  - (a) 이미지 public 공개 -> 토큰 없이 pull, 또는
  - (b) private 유지 시 필요 토큰 스코프(`read:packages`)와 주체 문서화. infra 는 별도 PAT 를 secret 으로 주입할 수 있어야 함 (infra 레포의 기본 `GITHUB_TOKEN` 으로는 cross-repo pull 불가).

### 2.6 env 계약 안정화 (키 드리프트 해소)
infra `.env.j2` <-> `default.env.example` 불일치 확정 목록. 엔진 코드가 read 하는 정규 키 이름을 확정해 달라:

| infra 현행(.env.j2) | 0.3.1 default.env.example | 엔진 코드 정규 키? |
|---|---|---|
| `WORKER_TASK_EXCHANGE` | `RABBITMQ_TASK_EXCHANGE` | 확인 요청 |
| `WORKER_TASK_RESULT_KEY` | `RABBITMQ_ROUTING_KEY_TASK_RESULT` | 확인 요청 |
| (없음) | `RABBITMQ_TASK_QUEUE_PREFIX` | 필수 여부 확인 |
| (없음) | `RABBITMQ_TASK_INSTALL_KEY_PREFIX` | 필수 여부 확인 |
| (없음) | `RABBITMQ_QUEUE_WORKER_RESULT` | 필수 여부 확인 |

- 가능하면 release 에셋에 prod 용 env 카탈로그(필수/선택 구분, 키별 설명) 동봉.

### 2.7 릴리즈 에셋 정합
- 현재 에셋명이 `default.env.example` 인데 운영 문서/요청에서는 `env.example` 로 통용됨 -> 명명 통일.
- `SHA256SUMS` 에 compose·env 에셋도 포함되는지 확인 (현재 wheel/tar 중심).

## 3. infra 측 대기 작업 (위 충족 후)

엔진 레포가 prod compose 를 제공하면 infra 에서:
1. `engine_compose` role 의 다운로드 대상 파일명을 prod compose 에셋명으로 교체.
2. `.env.j2` 에 `ENGINE_IMAGE`·`PGDATA_HOST`·`MQ_DATA_HOST`·`OLLAMA_BASE_URL`·`OLLAMA_MODEL` 및 정정된 RABBITMQ 키 추가.
3. 워크플로우에 `read:packages` PAT secret 주입 (private 유지 시).
4. 본 문서 폐기.

---

## 엔진 측 대응 (ADR 0035, 구현 완료) — infra 회신용

엔진은 base(prod-safe `docker-compose.yml`) + override(dev `docker-compose.override.yml`) 분리로 제공한다. 루트 base 가 곧 릴리즈 첨부 prod compose 다 — build 키 없음, GHCR 이미지 핀, `PGDATA_HOST`/`MQ_DATA_HOST` 볼륨 바인딩, diagnostic-worker 포함. dev 편의(빌드·bind mount)는 override 로 분리(릴리즈 미첨부). Dockerfile 은 단일 유지(dev-prod parity).

요청 항목별 확정:

- 2.1 이미지 핀·태그: base 기본 이미지 = `ghcr.io/z-converter-assessment/assessment-engine:<version>`. 태그는 `v` 없는 semver(`0.3.1`) — git tag 만 `v0.3.1`, 이미지 태그·wheel 버전은 `0.3.1`. release CI 가 base 의 `__ENGINE_VERSION__` 을 해당 버전으로 치환해 첨부.
- 2.2 dev 격리: bind mount·build 는 override.yml 에만. 릴리즈는 base 만 첨부라 infra 가 받는 compose 는 빌드 없는 pull-and-run.
- 2.3 외부 볼륨: `PGDATA_HOST`(`/home/postgres/pgdata/data`)·`MQ_DATA_HOST`(`/var/lib/rabbitmq`) env 주입. 미설정 시 named volume.
- 2.4 diagnostic-worker·Ollama: base 포함. `OLLAMA_BASE_URL`·`OLLAMA_MODEL` env 주입식, 미도달 시 narrative pending(graceful degrade).
- 2.5 GHCR: private 유지. infra 가 `read:packages` fine-grained PAT 주입해 pull.
- 2.6 env 정규 키: `RABBITMQ_*` (config.py 필드명 대문자, env_prefix 없음). infra 의 `WORKER_TASK_EXCHANGE`->`RABBITMQ_TASK_EXCHANGE`, `WORKER_TASK_RESULT_KEY`->`RABBITMQ_ROUTING_KEY_TASK_RESULT` 정정. `RABBITMQ_TASK_QUEUE_PREFIX`·`RABBITMQ_TASK_INSTALL_KEY_PREFIX`·`RABBITMQ_QUEUE_WORKER_RESULT` 는 default 보유(선택)이나 agent 발행 값과 일치 필수. 카탈로그·설명은 `docs/operations/env.md` 12절.
- 2.7 에셋명: `.env.example`(점 prefix). `default.env.example` 아님. SHA256SUMS 에 compose·env 포함.

infra 회신 후 본 문서 폐기.
