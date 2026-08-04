# ADR 0063 — 런타임과 코드 규약을 현행 Python 기준선으로 올린다

상태: Accepted (2026-08-04) — ADR 0062 의 "규칙 단위 래칫" 절차는 존속하고, 그 절차로 도달한 기준선을 본 ADR 이 정한다.

## Context

런타임은 3.12 에 묶여 있었고 lint 는 5개 규칙 계열(E·F·I·B·UP)만 봤으며 포매터는 없었다. 그 사이 이 저장소의
명문 규약은 늘어났는데 — UTC 저장(#F2), 예외 타입 명시(#F6), 시크릿 노출 금지(#F8), 주석·docstring 규약(#F12),
키보드 직타 문자만 쓰는 표기 규칙 — 강제하는 도구가 없어 사람과 리뷰가 유일한 그물이었다.

포매터 부재는 측정 가능한 비용으로 나타났다. 따옴표 스타일만 303곳이 갈려 있었고, 같은 파일 안에서도 줄바꿈
관례가 섞였다.

## Decision

세 가지를 한 기준선으로 묶는다.

### 런타임

`requires-python = ">=3.14"` 단일. 하위 호환 범위를 두지 않는다 — 배포 산출물이 이미지 하나라 다른 minor 에서
도는 경로가 없다. 같은 minor 를 `[tool.ruff].target-version` · `[tool.pyright].pythonVersion` · `.python-version` ·
Dockerfile · 워크플로가 함께 갖는다.

의존성 floor 는 실제로 resolve 된 버전으로 올린다. 검증한 조합과 선언이 갈리면 lockfile 없이 설치한 환경이
테스트되지 않은 조합을 받는다.

### 포맷

`ruff format` 이 정본이고 검증 워크플로가 `--check` 로 게이트를 건다. 따옴표·줄바꿈·괄호 배치를 리뷰에서 논하지
않는다. markdown 안 python 코드블록도 대상이되 결정 아카이브(`docs/decisions`)와 시점 스냅샷(`docs/learning`)은
제외한다 — 당시 그대로 두는 문서다.

### lint

선택 목록을 28개 계열로 넓힌다. 고르는 기준은 하나다 — 이미 명문으로 있는 규약을 사람이 아니라 도구가 강제하게
만드는 것. 규약과 규칙의 대응표는 `docs/guides/conventions.md` 1절이 갖는다.

제외는 사유를 설정 주석에 남긴다. `PLR09xx`(인자·분기 개수)는 SQL 조립부를 나누면 흐름이 흩어지고, `ERA001` 은
이 저장소의 구획 주석을 코드로 오인하며, `TRY003` 은 설정 검증 예외가 운영자에게 이유를 알려야 해서 뺀다.

### 코드 관용구

- `from __future__ import annotations` 를 쓰지 않는다. 3.14 는 어노테이션을 지연 평가한다(PEP 649).
- 타입 별칭은 `type X = ...`(PEP 695).
- 상위 메서드 재정의는 `@override`(PEP 698). pyright `reportImplicitOverride` 를 error 로 켠다 — ADR 0062 가
  "별개 결정"으로 미뤄둔 항목이고, 여기서 결정한다.

## 소진한 것

규칙 확장이 낸 위반은 2339건이고 728건이 자동 수정됐다. 나머지를 손으로 고치는 과정에서 규칙이 아니라 구조가
문제인 자리가 드러났다.

`TC` 규칙이 `from X import Y as Y` 재export 사슬을 끊었다. `REPORT_KIND_ENV` 와 anchor helper 는
`diagnostic.report_result` 가 원천인데 `report_serializer`·`diagnostic_service` 를 통과해 들어오고 있었고,
`MatchedPort` 는 정의가 `service_classifier` 인데 `view_models.server` 를 통과했다. 소비자가 원천에서 직접
가져오게 고쳤다. 세 모듈이 쓰는 `_normalize_anchor`·`_compute_hash` 는 패키지 밖에서 쓰이는데 private 표기였으므로
밑줄을 뗐다.

`N818` 이 서비스 예외 넷에 `Error` 접미가 없는 것을 잡았고, `RUF001`~`RUF003` 이 주석·docstring 의 비키보드
문자 8건을 잡았다 — 후자는 표기 규칙 위반이 코드에 남아 있던 것이다.

`ASYNC221` 은 통합 테스트 fixture 가 async 안에서 alembic 을 blocking 실행하는 것을 잡았다.

## Consequences

어노테이션을 런타임에 읽는 세 지점이 `TC` 규칙과 부딪힌다 — Pydantic 모델 필드, SQLAlchemy `Mapped[...]`,
FastAPI endpoint·의존성 callable. 앞의 둘은 `runtime-evaluated-base-classes`·`runtime-evaluated-decorators`
등록으로, 뒤는 per-file 제외로 처리한다. 이 목록은 프레임워크가 어노테이션을 읽는 방식이 바뀌면 함께 바뀐다.

PEP 695 별칭은 `TypeAliasType` 이라 `get_args(X)` 가 빈 튜플을 준다. 런타임에 별칭 안을 들여다보는 코드는
`X.__value__` 를 거쳐야 한다 — 이걸 놓치면 루프가 0회 돌면서 테스트가 조용히 통과한다.

새 규칙을 켤 때 기준은 ADR 0062 와 같다. 위반을 남긴 채 켜지 않는다 — 그 PR 안에서 0 으로 만들거나, 못 만들
이유를 설정 주석에 적고 제외한다.
