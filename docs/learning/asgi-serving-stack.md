# 서빙 스택 — nginx · uvicorn · ASGI 애플리케이션

학습 자료. 기준 시점 2026-08-02, 커밋 `7a0e4ec`. 갱신 의무 없음.

1절은 전체 그림을 세우는 오리엔테이션이다. 2절부터 7절이 L1·L2, 8절이 L3 다.

## 1. 참여자 4 와 경계 3

파이썬 웹 스택을 헷갈리게 만드는 원인은 참여자를 세는 방식이 아니라 경계를 세는 방식이다. 참여자가 넷이고 그 사이 경계가 셋인데, 경계 셋이 서로 다른 종류다.

```
  browser / agent
        |  (1) HTTP(S) over TCP
        v
  +-----------------------------+
  | nginx                       |  reverse proxy
  +-----------------------------+
        |  (2) HTTP over TCP or unix socket
        v
  +-----------------------------+
  | uvicorn                     |  ASGI server
  |      |                      |
  |      | (3) ASGI callable    |  in-process python call, NOT a network hop
  |      v                      |
  |  +---------------------+    |
  |  | FastAPI app object  |    |  ASGI application
  |  +---------------------+    |
  +-----------------------------+
            one OS process
```

경계 1 과 2 는 네트워크다. 소켓이 있고 HTTP 바이트가 오간다. 경계 3 은 네트워크가 아니다. uvicorn 이 앱 객체를 import 해서 `await app(scope, receive, send)` 로 직접 부른다. 같은 프로세스, 같은 이벤트 루프다.

이 차이를 놓치면 uvicorn 과 애플리케이션을 별개 프로그램처럼 세게 되고, 그러면 "uvicorn 이 애플리케이션" 같은 분류가 나온다.

## 2. "웹 서버" 는 변별력이 없는 이름이다

nginx 를 웹 서버라 부르고 uvicorn 을 그 뒤의 무언가라고 부르면 구분이 선 것 같지만 실은 안 섰다. 둘 다 소켓을 열고 `listen` 하고 들어온 바이트를 HTTP 로 파싱한다. 그 지점까지는 하는 일이 같다.

갈리는 곳은 파싱한 다음이다.

| | 파싱 후 하는 일 | 그래서 부르는 이름 |
|---|---|---|
| nginx | 다른 HTTP 서버로 전달하거나 디스크 파일로 응답 | 리버스 프록시 (겸 정적 파일 서버) |
| uvicorn | 파이썬 객체(scope·receive·send)로 번역해 앱 함수에 넘김 | ASGI 서버 (겸 애플리케이션 서버) |

즉 uvicorn 을 정확히 부르는 이름은 ASGI 서버다. HTTP 를 말하는 쪽이 아니라 ASGI 를 말하는 쪽이라서가 아니라, HTTP 를 받아 ASGI 로 번역하는 것이 그 프로그램의 존재 이유라서다.

## 3. ASGI 규격 — 애플리케이션의 정의

ASGI 에서 애플리케이션은 클래스도 프레임워크도 아니고 다음 모양의 async callable 하나다.

```python
async def app(scope, receive, send):
    ...
```

- `scope` — 이 연결에 대한 dict. `type`(http · websocket · lifespan), method, path, headers 등
- `receive` — await 하면 다음 이벤트를 주는 함수. `http.request` 본문 조각, `http.disconnect` 등
- `send` — 이벤트를 내보내는 함수. `http.response.start`, `http.response.body`

앞선 WSGI 규격은 동기 함수 한 번 호출하고 반환값을 받는 모양이었다. 요청 하나가 함수 호출 하나로 끝나므로 웹소켓이나 서버 전송 이벤트처럼 오래 살아 있는 연결을 표현할 방법이 없다. ASGI 는 호출 한 번 안에서 이벤트를 주고받게 바꿔 그 제약을 풀었다.

`scope["type"]` 이 셋이라는 점이 중요하다. `lifespan` 이 그중 하나다. uvicorn 은 기동할 때 `{"type": "lifespan"}` 으로 앱을 한 번 부르고, 앱이 `lifespan.startup` 을 받아 준비를 마친 뒤 `lifespan.startup.complete` 를 돌려준다. 종료도 같은 호출 안에서 `lifespan.shutdown` 으로 오간다. FastAPI 의 `lifespan` 컨텍스트 매니저가 이 프로토콜 위에 얹힌 API 다 (FastAPI 쪽 사용법은 `fastapi/01-app-lifespan.md`).

## 4. uvicorn 이 하는 일

| 하는 일 | 내용 |
|---------|------|
| 소켓 관리 | bind · listen · accept |
| HTTP 파싱 | 요청 라인·헤더·본문을 읽어 scope 와 이벤트로 변환 |
| 이벤트 루프 구동 | asyncio 루프를 띄우고 그 위에서 앱 코루틴을 실행 |
| 프로토콜 업그레이드 | 웹소켓 핸드셰이크 처리 |
| lifespan 호출 | 기동·종료 시 앱을 lifespan scope 로 호출 |
| 시그널 처리 | SIGTERM 수신 시 새 연결을 끊고 진행 중 요청을 마무리 |

`uvicorn[standard]` 로 설치하면 순수 파이썬 구현 대신 C 확장이 붙는다. HTTP 파서는 httptools, 이벤트 루프는 uvloop 다. 둘 다 규격을 바꾸지 않고 같은 일을 빠르게 하는 교체품이다.

## 5. nginx 를 앞에 두는 이유

uvicorn 만으로도 HTTP 응답은 나간다. 그런데도 앞에 프록시를 세우는 이유가 여섯 가지다.

1. TLS 종단 — 인증서 갱신·암호군 정책·프로토콜 버전 관리를 애플리케이션 밖으로 뺀다
2. 정적 파일 — 디스크 파일을 `sendfile` 로 커널이 직접 내보낸다. 파이썬 프로세스를 거치지 않는다
3. 느린 클라이언트 버퍼링 — 요청 전체를 받아 두었다가 한 번에 넘긴다. 저속 연결이 앱 워커를 붙들고 있지 못하게 막는다
4. 부하 분산 — uvicorn 인스턴스 여러 개에 뿌린다
5. 요청 제한 — 본문 크기 상한, 커넥션·요청 rate limit, 타임아웃
6. 헤더 정규화 — `X-Forwarded-For`·`X-Forwarded-Proto` 를 주입해 앱이 원래 클라이언트 주소와 스킴을 알게 한다

3 번이 가장 자주 간과된다. uvicorn 은 비동기라 연결 하나가 스레드를 점유하지는 않지만, 느린 업로드는 여전히 그 코루틴을 살려 두고 메모리를 잡는다. 프록시가 앞에서 다 받아 주면 앱은 완성된 요청만 본다.

6 번을 쓸 때는 앱 쪽에서 그 헤더를 신뢰하도록 켜야 한다. uvicorn 은 `--proxy-headers` 와 `--forwarded-allow-ips` 로 어느 출발지의 헤더를 믿을지 정한다. 아무 곳에서나 온 `X-Forwarded-For` 를 믿으면 클라이언트가 자기 IP 를 위조한다.

## 6. 프로세스와 워커

uvicorn 은 기본이 단일 프로세스, 단일 이벤트 루프다. 코어를 여러 개 쓰려면 프로세스를 늘려야 한다. 파이썬은 프로세스 하나가 GIL 때문에 CPU 바운드 작업을 병렬로 못 돌린다.

늘리는 방법이 셋이다.

| 방법 | 프로세스 관리자 | 비고 |
|------|----------------|------|
| `uvicorn --workers N` | uvicorn 자체 supervisor | 부모가 소켓을 bind 하고 자식이 상속 |
| gunicorn + `UvicornWorker` | gunicorn | 워커 재시작·타임아웃 정책이 더 풍부 |
| 컨테이너 N 개 | 오케스트레이터 (compose·k8s) | 앞단 프록시가 분산 |

세 번째를 쓰면 프로세스 관리가 컨테이너 런타임의 일이 되므로 애플리케이션 이미지 안에 supervisor 를 둘 이유가 없어진다.

## 7. FastAPI 와 Starlette 의 분담

FastAPI 를 ASGI 애플리케이션이라 부를 때 실제로 그 일을 하는 코드는 두 층에 나뉘어 있다.

| 층 | 담당 |
|----|------|
| Starlette | ASGI callable 구현, 라우팅, 미들웨어 체인, Request·Response 객체, 웹소켓, 정적 파일, lifespan 프로토콜 처리 |
| FastAPI | Pydantic 기반 요청 검증·응답 직렬화, 의존성 주입(`Depends`), OpenAPI 스키마 자동 생성과 문서 UI |

`class FastAPI(Starlette)` 이라 상속 관계다. `app = FastAPI(...)` 로 만든 인스턴스가 곧 경계 3 에서 uvicorn 이 부르는 그 callable 이다. 별도의 어댑터가 끼어 있지 않다.

그래서 "FastAPI 프레임워크 + 애플리케이션 로직" 이라는 묶음은 맞다. 다만 그 묶음의 정확한 이름은 웹 애플리케이션이 아니라 ASGI 애플리케이션이고, 프레임워크 자리에 Starlette 이 한 겹 더 있다.

### fastapi 서브모듈 상당수는 Starlette 재수출이다

`fastapi.staticfiles` · `fastapi.templating` · `fastapi.websockets` 처럼 fastapi 패키지 안에 있는 이름 중 여럿은 자기 구현이 아니라 Starlette 것을 그대로 내보내는 한 줄짜리 모듈이다.

```python
# fastapi/staticfiles.py 전문
from starlette.staticfiles import StaticFiles as StaticFiles  # noqa
```

`from fastapi.staticfiles import StaticFiles` 와 `from starlette.staticfiles import StaticFiles` 가 같은 클래스를 가리킨다. fastapi 경로를 두는 이유는 사용자가 import 출처를 하나로 유지하게 하려는 편의다.

여기에 함정이 하나 붙는다. `fastapi.templating` 은 import 가 되지만 jinja2 를 설치하지 않았으면 쓸 때 터진다. Starlette 이 jinja2 를 `full` extra 로 두고 `try: import jinja2` 로 감싸기 때문이다 (선택 의존 일반론은 `python-packaging.md` 4층).

### fastapi 가 끌고 오는 것과 안 끌고 오는 것

fastapi 를 설치하면 필수 의존 넷이 무조건 따라온다 — starlette, pydantic, typing-extensions, typing-inspection (여기에 문서화 헬퍼 annotated-doc 이 붙는다). uvicorn 은 여기 없다. 서버가 애플리케이션의 의존이 아니기 때문이다.

pydantic 은 오지만 pydantic-settings 는 오지 않는다. 두 패키지의 책임이 다르다.

| 패키지 | 책임 | 부수효과 |
|--------|------|----------|
| pydantic | 이미 파이썬 값으로 손에 있는 데이터를 타입·제약으로 검증하고 변환·직렬화 | 없음 (순수) |
| pydantic-settings | 그 값을 어디서 읽어올지 — 환경변수·`.env`·secrets 디렉토리·CLI·설정 파일 | 있음 (환경·파일시스템 읽기) |

pydantic v1 시절에는 `pydantic.BaseSettings` 로 코어 안에 있었고 v2 에서 별도 배포로 갈라졌다. 검증 엔진을 부수효과 없는 순수 라이브러리로 유지하려면 OS 를 건드리는 설정 로딩이 그 안에 있으면 안 되기 때문이다. 의존은 단방향이라 pydantic-settings 가 pydantic 을 필요로 하고 반대는 없다.

FastAPI 가 pydantic 만 필수로 두는 것도 같은 이유다. 요청 본문 검증은 프레임워크의 일이지만 설정을 어디서 읽을지는 애플리케이션이 정할 일이다.

## 8. 이 저장소의 현황

프록시가 없다. compose 가 web 서비스의 8000 포트를 그대로 퍼블리싱하고 uvicorn 이 `0.0.0.0` 에 직접 bind 한다.

```python
# web/__main__.py
uvicorn.run(
    "assessment_engine.web.main:app",
    host="0.0.0.0",
    port=get_web_settings().web_port,
    reload=get_web_settings().web_reload,
    timeout_graceful_shutdown=3,
)
```

`--workers` 를 주지 않으므로 단일 프로세스다. 5절이 프록시에 맡기는 일 중 정적 파일은 애플리케이션이 직접 한다 — `main.py` 가 `StaticFiles` 를 `/static` 에 mount 한다.

이 구성이 성립하는 전제가 셋이다.

- 사용자가 고객사 담당자와 운영자로 한정된 B2B 내부 포털이라 동시 접속이 크지 않다
- 정적 자원이 자체 CSS·JS 뿐이라 파이썬이 서빙해도 부담이 되지 않는다
- TLS 가 필요한 배포에서는 앞단 ingress 가 종단하고 애플리케이션은 plain HTTP 로 받는다

세 번째가 이 저장소가 프록시를 스스로 두지 않는 이유다. 인터넷에 노출되는 배포라면 그 환경이 compose 앞단에 프록시를 추가하는 몫이며, 애플리케이션은 인증서를 모르는 상태로 남는다. 배포 형태의 한계 서술은 `docs/guides/deploy.md` 8절이 정본이다.

프록시를 도입하게 되면 함께 손대야 할 자리가 둘 있다. uvicorn 의 `--proxy-headers` 허용 출발지 설정과, `/static` 을 프록시가 가져갈지 애플리케이션에 남길지의 판단이다.

## 9. 용어

| 용어 | 뜻 |
|------|-----|
| WSGI | 동기 파이썬 웹 서버-애플리케이션 인터페이스. 요청 하나 = 함수 호출 하나 |
| ASGI | 비동기 인터페이스. 호출 한 번 안에서 이벤트를 주고받아 오래 사는 연결을 표현 |
| ASGI 애플리케이션 | `async def app(scope, receive, send)` 형태의 callable |
| ASGI 서버 | HTTP·웹소켓을 받아 ASGI 이벤트로 번역해 애플리케이션을 부르는 프로그램. uvicorn·hypercorn·daphne |
| 리버스 프록시 | 클라이언트 요청을 받아 뒤쪽 서버로 전달하는 서버. nginx·caddy·traefik |
| scope | 연결 하나의 메타데이터 dict. type·method·path·headers |
| lifespan | 기동·종료를 다루는 ASGI scope type |
| uvloop | libuv 기반 asyncio 이벤트 루프 교체 구현 |
| httptools | C 로 쓰인 HTTP 파서 |
