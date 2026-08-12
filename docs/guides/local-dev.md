# 로컬 개발

정책: AGENTS.md #A. dev 환경에서 무엇을 어떻게 하는지를 다룬다. 이미지·compose 구성 사양은 `docs/reference/docker.md`, 배포는 `docs/guides/deploy.md`, 테스트는 `docs/guides/testing.md` 가 갖는다.

---

## 기동

```bash
make setup        # python + node 개발 의존성
make dev          # 기동 — web http://localhost:8000
make dev-down     # 종료 (볼륨 보존)
docker compose ps -a # 7 서비스 상태 확인 (migrate 는 1회 실행 후 exited 0 이 정상)
```

`make dev` 는 `.env` 가 없으면 `.env.dev.example` 에서 만든 뒤 기동한다. 그 템플릿에 `COMPOSE_FILE` 이 없어야 compose 가 base 와 override 를 자동 머지해 핫리로드 스택이 뜬다 (머지 규칙은 `docs/reference/docker.md` "compose 3 파일").

첫 기동만 5분쯤 걸린다 — 의존성 설치(60s+)와 TimescaleDB 이미지 pull(~200MB)이 있다.

배포 기동은 secret 파일 생성이 선행해야 성립한다 — `docs/guides/deploy.md`.

agent 가 붙는 VM 은 본 repo 범위 밖이다 (OpenStack 공급).

---

## 코드 변경 반영

override 가 `./src` 를 컨테이너 `/app/src` 에 얹고 `PYTHONPATH` 로 앞에 세워 대부분 재빌드 없이 반영된다.

| 변경 위치 | 반영 | 추가 작업 |
|-----------|------|----------|
| `src/assessment_engine/` 아래 `.py` | web·consumer·worker 셋 다 재기동 | 없음 |
| 정적 자원 (`web/static/`) | 다음 요청부터 즉시 | 없음 |
| Jinja2 템플릿 (`web/templates/`) | 다음 요청부터 즉시 | 없음 |
| `pyproject.toml` (의존성) · `Dockerfile` | 미반영 | `make dev-build` |
| `docker-compose.yml` | 미반영 | `make dev` (변경된 서비스만 재생성) |
| ORM 모델 (컬럼·제약 추가) | 코드는 재기동되나 DB 스키마는 그대로 | 아래 "스키마 반영" |

`.py` 변경이 세 프로세스 모두를 건드리는 것은 셋 다 패키지 디렉토리 전체를 감시하기 때문이다 — web 만 고쳐도 consumer·worker 가 함께 재시작한다. 반영 메커니즘(bind mount 경로·watchfiles 래퍼·`asset_v` 재발급)은 `docs/reference/docker.md` "dev override" 절.

### 스키마 반영

ORM 모델을 고치면 코드는 재기동되지만 DB 는 그대로다. 재빌드가 아니라 마이그레이션이 필요하다.

```bash
make migration M="add boot_time to server_metrics"   # 리비전 초안 생성
# 생성된 파일을 검토·보강한다 (자동 생성이 놓치는 항목은 docs/guides/migrate.md)
make migrate                                          # 적용
docker compose restart web consumer worker            # 커넥션 풀 재생성
```

절차 상세와 라운드트립 검증은 `docs/guides/migrate.md`.

---

## 의존성 추가

`pyproject.toml` 을 고치면 `uv.lock` 도 같은 커밋에 넣는다 (#F9 "신규 의존성"). 둘 중 하나로 갱신한다.

```bash
uv add <pkg>          # pyproject.toml + uv.lock 동시 갱신
uv lock               # pyproject.toml 수동 편집 후 lockfile 만 재생성
```

이후 `make dev-build` 로 이미지를 다시 만든다. 근거·검사 방법은 `docs/guides/dependencies.md`.

---

## OS EOL 카탈로그 갱신

OS 지원 종료일은 외부 데이터를 미리 받아 저장소에 커밋한 정적 카탈로그로 판정한다. 갱신은 스크립트를 다시 돌려 그 파일을 재커밋하는 것이다.

```bash
make eol
```

인터넷이 되는 곳에서 돌린다. 갱신 주기는 분기 1회면 충분하다 — EOL 날짜는 그보다 자주 움직이지 않는다. 새 릴리즈가 나왔거나 벤더가 날짜를 바꿨을 때도 돌린다.

실패해도 기존 카탈로그가 그대로 남아 운영에 영향이 없다. 워크플로에 최신 여부를 확인하는 게이트는 없다 — 원본이 외부라 재생성 결과가 달라지는 것이 정상이고, 그것을 실패로 처리하면 우리 잘못이 아닌 이유로 통합이 막힌다.

판정 규약은 `src/assessment_engine/web/services/mappers/os_eol.py` 의 `_eol_info` 가 갖는다.

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

web 실시간 로그는 `make logs` 다. 나머지는 대상·옵션이 매번 달라 raw 로 쓴다.

```bash
docker compose logs consumer --since=10m         # consumer 최근 10분
docker compose exec postgres psql -U assessment -d assessment   # DB 접속
docker compose exec redis redis-cli              # Redis 접속
docker compose exec rabbitmq rabbitmqctl -p assessment list_queues name messages_ready   # 큐 적재량
```

vhost·권한·토폴로지 등 broker 자체 운영은 `docs/reference/rabbitmq.md`.

### 화면 캡처

렌더된 페이지를 PNG 로 찍고 브라우저 콘솔 에러를 함께 걷는다. 표시 계층이 실제 브라우저에서 어떻게 그려지는지는 띄워 봐야 알고, 화면이 멀쩡해 보여도 차트 로더가 조용히 죽은 경우를 이 콘솔 수집이 잡는다.

```bash
pnpm exec playwright install chromium               # 최초 1회
make screenshot OUT=shots SERVER=<public_id>        # 표준 페이지 세트 캡처
```

브라우저 설치가 별도인 이유는 실물이 150MB 안팎이라 npm 패키지에 담기지 않기 때문이다 — `make setup` 이 받는 것은 playwright 패키지까지다. Chromium 기동이 시스템 라이브러리 누락으로 실패할 때만 `pnpm exec playwright install --with-deps chromium` 을 실행한다. 이 명령은 apt 로 라이브러리를 설치하므로 sudo 를 묻는다.

| 옵션 | 기본값 | 뜻 |
|------|--------|-----|
| `--base <url>` | `http://localhost:8000` | 기준 오리진 |
| `--server <id>` | 없음 | public_id. 주면 개요·목록·환경·서버 상세 탭까지 표준 세트를 일괄 캡처 |
| `--vw` · `--vh` | 1440 · 1000 | 뷰포트 크기. full-page 라 세로는 스크롤 전체가 담긴다 |
| `--settle <ms>` | 2500 | load 후 차트가 그려질 때까지 대기 |
| `--scale <n>` | 2 | 픽셀 배율. 텍스트 선명도 |

`--server` 대신 URL 을 직접 나열하면 그 경로만 찍는다. 결과는 `<outDir>/<페이지이름>.png` 이고, 터미널에는 페이지별 HTTP 상태와 콘솔 에러가 나온다.

---

## 흔한 트러블

| 증상 | 원인 | 해결 |
|------|------|------|
| web 헬스체크 unhealthy | lifespan 에서 모델 로딩 실패 (`TypeError: non-default argument follows default` 등) | `docker compose logs web` 확인 후 코드 수정 -> reload 가 처리 |
| 기동 직후 `Settings` ValueError | 비밀번호 미설정·빈값·뻔한 값 | `.env` 확인. 판정 기준은 `docs/reference/contracts/env.md` 6절 |
| consumer 가 metrics 를 받았는데 server 미등록 | inventory 도착 전 metrics 가 먼저 옴 (DB 초기화 직후) | 자동 처리된다 — placeholder inventory 생성 후 저장(`auto-registered server from metrics` 로그). 다음 inventory 에 풀 정보로 갱신 |
| `docker compose up` 후 시간 초과 | 첫 빌드 의존성 설치 + TimescaleDB 이미지 pull | 첫 기동만 5분 여유 |
| `pg_isready` 는 통과인데 web 이 connection refused | TimescaleDB 확장 로딩이 진행 중 | web `start_period` 가 흡수한다 — 이미 적용됨 |
