# 01. 앱 인스턴스와 lifespan

학습 자료. 기준 시점 2026-08-02, 커밋 `7a0e4ec`. 갱신 의무 없음.

"정석" 절이 L2, "현황" 이하가 L3 다.

## 정석

FastAPI 앱이 기동 시 한 번 만들고 종료 시 한 번 정리해야 하는 자원은 `lifespan` 컨텍스트 매니저에서 다룬다. DB 커넥션 풀, 메시지 브로커 연결, HTTP 클라이언트가 대표적이다.

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    client = httpx.AsyncClient()
    app.state.http_client = client
    yield                      # 여기서 앱이 요청을 받는다
    await client.aclose()

app = FastAPI(lifespan=lifespan)
```

`yield` 이전이 기동, 이후가 종료다. 구버전의 `@app.on_event("startup")` 과 `@app.on_event("shutdown")` 데코레이터는 deprecated 이며 lifespan 이 대체했다. 두 데코레이터는 서로 상태를 공유하려면 모듈 전역 변수를 거쳐야 했는데, 컨텍스트 매니저는 `yield` 앞뒤가 같은 함수 스코프라 지역 변수로 넘긴다.

요청마다 만들지 않고 lifespan 에 두는 이유는 커넥션 재사용이다. `httpx.AsyncClient` 를 요청마다 생성하면 매번 TCP 핸드셰이크와 TLS 협상을 다시 한다. 내부에 커넥션 풀을 가진 객체는 프로세스 수명 동안 하나만 두고 재사용하는 것이 그 객체의 설계 의도다.

정리는 생성의 역순으로 한다. 예외가 나도 정리가 돌아야 하면 `try/finally` 로 `yield` 를 감싼다.

## 현황

`src/assessment_engine/web/main.py` 의 lifespan 이 네 가지를 한다.

| 위치 | 하는 일 |
|------|---------|
| `main.py:28` | `setup_logging(get_web_settings().log_format)` 로 로그 sink 단일 등록 |
| `main.py:34` | `app.state.dev_assets` 에 dev 여부 1회 판정 저장 |
| `main.py:38-47` | `aio_pika.connect_robust` 로 브로커 연결 후 DIRECT exchange declare |
| `main.py:58-67` | `httpx.AsyncClient` 생성 (connect 5s, total 120s) |
| `main.py:71` | `yield` |
| `main.py:73-75` | `http_client.aclose()` -> `broker_conn.close()` -> Redis `close_pool()` |

정리 순서가 생성 역순이라는 점에서 정석과 어긋나지 않는다. `try/finally` 로는 감싸지 않았다.

이 저장소만의 결정이 세 개 있다.

### 설정을 lifespan 안에서 읽는다

`get_web_settings()` 는 `@lru_cache(maxsize=1)` 이 붙은 함수다.

```python
# web/settings.py
@lru_cache(maxsize=1)
def get_web_settings() -> WebSettings:
    return WebSettings()
```

모듈 최상단에 `settings = WebSettings()` 를 두지 않는다. import 만으로 설정을 읽으면 비밀번호를 필수 필드로 둘 수 없기 때문이다. 테스트가 모듈을 import 하는 것만으로 환경변수를 요구하게 되고, 검증이 언제 도는지도 흐려진다. 캐시 덕분에 여러 번 호출해도 인스턴스는 하나다.

`main.py` 안에서 `get_web_settings()` 를 여러 번 부르는 모양이 나오는데, 낭비가 아니라 캐시 조회다.

### DB 엔진은 lifespan 밖에 있다

브로커와 HTTP 클라이언트는 lifespan 에서 만드는데 DB 엔진은 아니다.

```python
# db/session.py
@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = WebSettings()
    return create_async_engine(
        settings.database_url,
        connect_args={"command_timeout": 30, "timeout": 10},
        pool_pre_ping=True,
        hide_parameters=True,
    )
```

이유는 이 모듈을 web 뿐 아니라 consumer 와 worker 프로세스가 같이 쓰기 때문이다. FastAPI 앱이 없는 프로세스도 있으므로 특정 앱의 lifespan 에 묶을 수 없다. 대신 첫 호출 때 lazy 생성하는 `lru_cache` 로 같은 효과를 낸다.

대가로 종료 시 엔진 `dispose()` 를 명시적으로 부르지 않는다. Redis 풀만 `close_pool()` 로 닫는다. 프로세스가 죽으면 소켓도 닫히므로 실무상 문제는 드물지만, 정석대로면 graceful shutdown 에서 풀을 비우는 편이 낫다.

`create_async_engine` 인자 중 `hide_parameters=True` 는 보안 목적이다. 끄면 `DBAPIError` 문자열에 SQL 전문과 바인드 파라미터가 붙어 재시도 로그와 DLQ 경로로 값이 새어 나간다.

### app 이 모듈 레벨 인스턴스다

```python
# main.py:78
app = FastAPI(title="ZConverter Assessment Portal", lifespan=lifespan)
```

`def create_app() -> FastAPI` 팩토리 패턴이 아니다.

## 정석과 갈리는 지점 — 모듈 레벨 app 과 앱 팩토리

팩토리 패턴을 권하는 근거는 테스트마다 다른 설정으로 앱을 새로 만들 수 있다는 점이다. 모듈 레벨 인스턴스는 import 시점에 앱이 고정된다.

이 저장소는 팩토리를 쓰지 않으면서 그 문제를 다른 층에서 막았다. 앱 생성 시점에 설정을 읽지 않고 설정 읽기를 `lru_cache` 함수 뒤로 미뤘기 때문에, `app` 객체 자체는 설정에 의존하지 않는다. 테스트에서 설정을 바꾸려면 `get_web_settings.cache_clear()` 로 캐시를 비운다. 팩토리의 이점을 lazy 설정으로 대체한 구조다.

대가는 있다. `lru_cache` 는 프로세스 전역이라 한 테스트 세션 안에서 서로 다른 설정을 동시에 쓰지 못한다. 캐시를 비우며 순차적으로 바꾸는 것만 가능하다. 병렬 테스트에서 설정을 달리해야 하는 상황이 오면 그때 팩토리로 전환할 근거가 된다.

## 발견한 논점 — app.state 는 타입이 안 잡힌다

`main.py:91` 이 `getattr(request.app.state, "dev_assets", False)` 로 읽고, `web/deps.py:44` 가 `request.app.state.broker_channel` 로 읽는다. Starlette 의 `State` 는 `__getattr__` 로 임의 속성을 받으므로 타입 검사기가 `Any` 로 본다. lifespan 에서 넣은 키를 라우터에서 오타로 꺼내도 정적 검사에서 안 걸리고 런타임 `AttributeError` 로만 드러난다.

FastAPI 의 알려진 한계이고 회피책이 몇 가지 있다. 주제 03 에서 다룬다.

## 미확인

- lifespan 이 `try/finally` 가 아니라서, `yield` 중 앱이 예외로 죽는 경로에서 브로커 연결이 닫히는지 실측하지 않았다
- `broker_channel` 을 단일 채널로 공유하는데 동시 발행 시 aio-pika 채널의 안전성 전제를 확인하지 않았다
