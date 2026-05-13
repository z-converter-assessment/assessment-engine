---
name: test-write
description: TRIGGER when user requests test writing for current/specified file ("이 파일 테스트 작성", "/test-write", "write tests for X"). Apply project testing policy — pytest-asyncio loop_scope=session, testcontainers fixtures, factories from tests/factories.py, layer 분리(unit vs integration).
---

# test-write — 프로젝트 패턴 기반 테스트 작성

테스트 정책 단일 진실: `docs/operations/testing.md` (계층·인프라·fixture·명령·원칙).

## 절차

1. 대상 파일 Read.
2. 레이어 결정:
   - DB·Redis·외부 의존 없음(단순 함수·dataclass·계산) -> unit (`tests/unit/`)
   - Repository / DB query / Schema 통합 -> integration (`tests/integration/`)
   - 라우터·E2E → 별도 (E2E는 Lima 검증, pytest 범위 외)
3. `tests/{layer}/`에서 비슷한 파일이 이미 있는지 확인 → 있으면 그 패턴 그대로 따름. 없으면 새 파일.
4. 픽스처는 `tests/conftest.py` / `tests/integration/conftest.py`에서 가져옴. 새로 만들지 말 것.
5. 테스트 데이터는 `tests/factories.py`의 `make_inventory()` / `make_metrics()` 활용.

## 패턴 핵심

- 비동기 테스트: pytest-asyncio v1+ `asyncio_mode=auto` (pyproject 설정됨). `@pytest.mark.asyncio` 명시 불필요.
- 픽스처·테스트 양쪽에 `loop_scope=session` 적용된 상태. 이벤트 루프 분리 이슈 없음.
- function-scope `db_session`은 자동 rollback — 테스트 간 격리.
- `collect_repo` / `query_repo` 픽스처가 session 주입까지 처리.
- AsyncMock(Redis) 패턴: unit에서 `safe_*` helper 검증 시 사용.

## 작성 후 검증

1. `python -m pytest tests/{layer}/<new_test_file> -v` 단독 실행 — 통과 확인.
2. 통합 테스트는 testcontainers가 docker 띄우므로 시간 소요 (세션 첫 실행만).
3. 작성한 테스트가 실제로 의미있는 분기를 검증하는지 review (assert 누락 금지).

## 금지

- 새 픽스처를 함수 안에 정의 — `conftest.py`에 추가.
- factory 패턴 우회해서 dict 직접 생성 — `make_*()` 사용.
- E2E 테스트는 pytest로 작성 안 함 — Lima 파이프라인.
- 사용자 명시 요청 없이 pytest 자동 실행 — `feedback_no_test_runs.md` 메모리 정책.