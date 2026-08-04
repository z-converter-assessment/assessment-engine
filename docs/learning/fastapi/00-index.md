# FastAPI 연작 — 목차

학습 자료. 기준 시점 2026-08-02, 커밋 `7a0e4ec`. 갱신 의무 없음.

## 두 축

각 주제를 두 축으로 나눠 기록한다.

- 정석 — FastAPI, Starlette, Pydantic 공식 문서가 권장하는 형태와 그 근거. 계층으로는 L2 다
- 현황 — 이 저장소의 `src/assessment_engine/web/` 가 실제로 쓰는 형태, 정석과 갈리는 지점의 근거. L3 다

현황은 추측하지 않고 파일을 열어 확인한 것만 적는다. 확인하지 않은 것은 각 문서 끝 "미확인" 절에 남긴다.

## 기준 환경

| 항목 | 값 |
|------|-----|
| Python | >=3.12 |
| fastapi | >=0.136.0 |
| starlette | >=1.3.1 |
| pydantic | >=2.13.4 |
| pydantic-settings | >=2.14.0 |
| uvicorn[standard] | >=0.45.0 |
| SQLAlchemy | >=2.0.49 (asyncio) |

## 주제 목록

요청 수명주기 순서로 8개다.

| 번호 | 주제 | 대상 코드 | 상태 |
|------|------|-----------|------|
| 01 | 앱 인스턴스와 lifespan | `web/main.py` | 작성 완료 |
| 02 | 설정 주입 — pydantic-settings, Composition Root | `web/settings.py`, `config.py` | 미착수 |
| 03 | 의존성 주입 (Depends) — 스코프와 합성 | `web/deps.py` | 미착수 |
| 04 | 라우팅 — APIRouter 분할, path/query 검증 | `web/routers/` 라우터 9개 | 미착수 |
| 05 | 응답 모델 — response_model 과 return 어노테이션, OpenAPI | `web/view_models/` | 미착수 |
| 06 | 비동기 — async def 와 def, 이벤트 루프 블로킹 | `db/session.py`, repository 계층 | 미착수 |
| 07 | 미들웨어와 예외 처리 | `web/main.py` 미들웨어, `HTTPException` 사용처 | 미착수 |
| 08 | 테스트 — ASGI transport, DI override | `tests/` | 미착수 |

## 이월 항목

주제를 진행하다 뒤로 미룬 논점을 여기 모은다. 해소되면 해당 주제 파일로 옮기고 이 목록에서 지운다.

- `app.state` 속성 접근이 타입 검사에 안 잡히는 문제와 회피책 (01 에서 발견, 03 에서 다룬다)
