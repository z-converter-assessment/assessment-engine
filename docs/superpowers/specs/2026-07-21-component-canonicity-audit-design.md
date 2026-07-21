# 컴포넌트 정석성 감사 — 설계 (spec)

상태: Draft (사용자 리뷰 대기)
날짜: 2026-07-21
성격: superpowers 브레인스토밍 산출 spec. 내부 방법론 작업 자료 (docs/temp 아님 — 본 문서는 repo 내부 코드·규약 인용을 포함하며 외부 공유용 self-contained 문서가 아니다).

## 1. 배경

- 원 요청: `.claude/CLAUDE.md`를 모던 컨벤션 기반으로 재정립/재편.
- 조사 결과: CLAUDE.md는 stale 초기설계 문서가 아니다 — 최근 3개월 최다 갱신 파일(52회), 56개 ADR로 진화, Diátaxis docs 구조·supply-chain 보안(cosign/SBOM)·OpenAPI->TS 타입계약 등 이미 상당수가 모던 정석. 구체 production 문제는 부재.
- 실제 동기(사용자 확인): 학습 + "현재 컴포넌트가 정석인가" 검증 + 완성도. 유일한 구체 의심은 통계 쿼리 최적화 여지 1건.
- 판단: 문제 없는 working system의 big-bang 재설계는 churn(새 버그 유입·검증된 결정 폐기·수개월 소요). 대신 컴포넌트별 정석성 감사로 학습·검증하고, 근거 있는 개선만 후속 단계로 스코프한다.

## 2. 목표 / 비목표

목표
- 각 컴포넌트를 모던 정석 대비 판정한다(근거 포함).
- 근거 있는 개선 backlog(우선순위)를 산출한다.
- 학습 산출물을 남긴다: 왜 정석인지 / 어디가 왜 이탈인지.

비목표
- 본 감사 단계에서 코드 변경 0 (읽기·판정·문서화만).
- 도메인 불변식을 미학적 이유로 뒤집지 않는다.
- CLAUDE.md 표준 형식 재편은 본 감사 산출물 확정 후 별도 단계.

## 3. 감사 대상 (4 도메인 / 13 유닛)

수집 파이프라인
- U1 메시지 계약·wire 버저닝 (extra=ignore, schema_version, CONTRACT_VERSION)
- U2 consumer·멱등성 2단 방어 (aio-pika, safe_set_nx + DB UNIQUE)
- U3 MQ 토폴로지·큐 정책 (RabbitMQ, routing key, task 큐)
- U4 DLQ·retry·실패 모델 (fail-close/open, 백오프)

저장 계층
- U5 DB 모델·repository 인터페이스·DTO in/out (SQLAlchemy, Alembic 단일진실, pg_insert on_conflict)
- U6 시계열 hypertable·continuous aggregate·counter_agg·쿼리 pruning (통계 쿼리 관심사)
- U7 Redis 캐시·fail-open (safe_* helper, 캐시-aside)

표현 계층
- U8 web 라우팅·SSR+JSON·pagination (FastAPI, page/cursor)
- U9 표시 원칙 P1~P4·service·mapper·viewmodel
- U10 타입 계약 (OpenAPI->TS codegen, tsc --checkJs)
- U11 도메인 로직 (recommendation right-sizing, service_classifier)

플랫폼
- U12 worker·disposability(12-factor)·config·DI·secret (composition root, SecretStr)
- U13 관측·로깅 / 배포·CI (loguru, compose base+override+secret, cosign·SBOM, deploy.sh)

리뷰는 4 도메인 단위로 순차 진행한다 (13 유닛은 세부 판정 대상).

## 4. 판정 루브릭

- canonical (정석): 현행 best practice와 일치. 무변경. 근거 + 정석 참조 명시.
- acceptable (수용가능): 교과서 정석은 아니나 도메인·제약이 정당화. 유지 + 이유 기록.
- improvable (개선여지): 정석 이탈이 실제 비용을 유발. 개선 후보. named cost(성능/정합성/유지보수) + 대략 규모 명시 의무. "덜 모던해 보임" 같은 미학적 사유는 금지 — 감사가 churn 생성기가 되는 걸 막는 핵심 규율.

## 5. 컴포넌트별 포맷

각 유닛: 현재 패턴 -> 정석 참조 -> 판정 -> 근거 -> (improvable 시) 비용 + 대략 규모.

정석 참조의 출처: context7로 당긴 현행 라이브러리 공식 docs(FastAPI·SQLAlchemy·TimescaleDB·aio-pika·Pydantic·Redis 등) + 확립된 아키텍처 패턴. 모델 기억이 아닌 현행 출처 기반 — 학습·모던성 담보.

## 6. 실행 모델

순차 대화형. 도메인 순서: 수집 -> 저장 -> 표현 -> 플랫폼.

- 각 도메인: 관련 코드 읽기 -> 정석 참조 수집 -> 유닛별 판정 초안 -> 사용자와 리뷰·심화·확정 -> 다음 도메인.
- 저장 도메인(U6)에 통계 쿼리 관심사가 있어 초반에 실질 가치가 나온다.

## 7. 산출물 / 위치

- 감사 리포트: docs/superpowers/audit/ 아래 누적 (도메인별 파일 또는 단일 파일 — 실행 중 결정).
- 위치 근거: docs/temp/는 cross-repo 받은편지함이라 self-contained·코드 path 인용 금지 규약 — 코드 내부를 깊게 인용하는 감사엔 부적합. docs/superpowers/는 내부 방법론 작업 트리(git 추적, Diátaxis 거버넌스 밖, 영구 docs가 인용하지 않음).
- 확정된 규약 갱신·결정만 후속에 ADR·영구 docs로 정공 격상.

## 8. 후속 흐름

감사 -> 우선순위 backlog -> 개선건별 개별 사이클(brainstorm -> plan -> 구현 -> verify -> review) -> 최종 CLAUDE.md 표준 형식 재편(검증·갱신된 규약 반영).

## 9. 성공 기준

- 13 유닛 전부 판정 완료.
- 모든 improvable 항목에 named cost + 규모 기재.
- backlog 우선순위화 완료.
- 본 감사 단계 코드 변경 0.
