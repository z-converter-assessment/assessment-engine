# 로컬 개발

정책: CLAUDE.md #A. 본 문서는 dev 환경에서 무엇을 어떻게 하는지를 다룬다. 이미지·compose 구성 사양은 `docs/reference/docker.md`, 배포는 `docs/guides/deploy.md` 가 갖는다.

---

## 기동

```bash
cp .env.dev.example .env && docker compose up -d   # web http://localhost:8000
docker compose ps                                  # 7 서비스 상태 확인
docker compose down                                # 종료 (볼륨 보존)
```

dev 는 `.env.dev.example` 을 쓴다 — 어떤 compose 파일이 합쳐져 로컬 빌드·핫리로드로 뜨는지는 `docs/reference/docker.md` "compose 3 파일" 절. 첫 기동은 의존성 설치(60s+)와 TimescaleDB 이미지 pull(~200MB)이 있어 5분쯤 걸린다.

배포 기동은 secret 파일 생성이 선행해야 성립한다 — `docs/guides/deploy.md`.

agent 가 붙는 VM 은 본 repo 범위 밖이다 (OpenStack 공급).

---

## 코드 변경 반영

`./src/assessment_engine` 가 컨테이너 가상환경에 bind mount 돼 있어 대부분 재빌드 없이 반영된다.

| 변경 위치 | web | consumer | worker | 추가 작업 |
|-----------|-----|----------|--------|----------|
| Python 코드 (web/) | uvicorn auto-reload | — | — | 없음 |
| Python 코드 (consumer/) | — | watchfiles 재시작 | — | 없음 |
| Python 코드 (worker/, db/, config.py) | uvicorn auto-reload | watchfiles 재시작 | watchfiles 재시작 | 없음 |
| 정적 자원 (web/static/) | 즉시 | — | — | 없음 (dev 는 매 요청 asset_v 재발급) |
| Jinja2 템플릿 (web/templates/) | 즉시 | — | — | 없음 |
| `pyproject.toml` (의존성) | 미반영 | 미반영 | 미반영 | `docker compose up --build -d` |
| `Dockerfile` | 미반영 | 미반영 | 미반영 | `docker compose up --build -d` |
| `docker-compose.yml` | 부분 | 부분 | 부분 | `docker compose up -d` (변경된 서비스만 재생성) |
| ORM 모델 (컬럼·제약 추가) | 코드는 reload 되나 DB 스키마는 미반영 | 동일 | 동일 | `alembic revision --autogenerate` -> `docker compose restart migrate` -> 앱 재기동 |

반영 메커니즘(bind mount 경로·watchfiles 래퍼·`asset_v` 재발급)은 `docs/reference/docker.md` "dev override" 절.

---

## 의존성 추가

`pyproject.toml` 을 고치면 `uv.lock` 도 같은 커밋에 넣는다 (#F9 "신규 의존성"). 둘 중 하나로 갱신한다.

```bash
uv add <pkg>          # pyproject.toml + uv.lock 동시 갱신
uv lock               # pyproject.toml 수동 편집 후 lockfile 만 재생성
```

이후 `docker compose up --build -d` 로 이미지를 다시 만든다. 근거·검사 방법은 `docs/guides/dependencies.md`.

---

## 초기화

```bash
docker compose down -v   # named volume 삭제
```

필요한 경우는 셋이다.

- ORM 모델에 컬럼·제약을 추가했는데 alembic revision 미적용분을 털어야 할 때
- TimescaleDB hypertable 정의를 바꿨을 때
- 테스트 시나리오를 처음부터 돌릴 때

---

## 디버깅

```bash
docker compose logs -f web                       # web 실시간 로그
docker compose logs consumer --since=10m         # consumer 최근 10분
docker compose exec postgres psql -U assessment -d assessment   # DB 접속
docker compose exec redis redis-cli              # Redis 접속
docker compose exec rabbitmq rabbitmqctl -p assessment list_queues name messages_ready   # 큐 적재량
```

---

## 흔한 트러블

| 증상 | 원인 | 해결 |
|------|------|------|
| web 헬스체크 unhealthy | lifespan 에서 모델 로딩 실패 (`TypeError: non-default argument follows default` 등) | `docker compose logs web` 확인 후 코드 수정 -> reload 가 처리 |
| 기동 직후 `Settings` ValueError | 비밀번호 미설정·빈값·뻔한 값 | `.env` 확인. 판정 기준은 `docs/reference/contracts/env.md` 6절 |
| consumer 가 metrics 를 받았는데 server 미등록 | inventory 도착 전 metrics 가 먼저 옴 (DB 초기화 직후) | 자동 처리된다 — placeholder inventory 생성 후 저장(`auto-registered server from metrics` 로그). 다음 inventory 에 풀 정보로 갱신 |
| `docker compose up` 후 시간 초과 | 첫 빌드 의존성 설치 + TimescaleDB 이미지 pull | 첫 기동만 5분 여유 |
| `pg_isready` 는 통과인데 web 이 connection refused | TimescaleDB 확장 로딩이 진행 중 | web `start_period` 가 흡수한다 — 이미 적용됨 |

---

## 관련 문서

- `docs/reference/docker.md` — 이미지·compose 구성 사양
- `docs/guides/testing.md` — 테스트 실행
- `docs/guides/migrate.md` — 마이그레이션 작성·적용
- `docs/guides/dependencies.md` — 의존성 관리 규약
- `docs/reference/rabbitmq.md` — broker 운영 (vhost·권한·토폴로지)
