# 스키마 계약 감사 프롬프트

assessment-engine과 assessment-agent 사이의 wire 계약 drift만 읽기 전용으로 감사한다. 엔진 내부 ORM, DTO, mapper 정합은 코드 리뷰가 담당한다.

## 외부 저장소 탐색

1. 엔진 저장소의 형제 디렉토리에서 `schema/wire.schema.json`을 찾는다.
2. 후보가 `docs/payload-contract.md`와 Linux, Windows 발행 소스를 함께 가지는지 확인한다.
3. 후보가 하나면 그 저장소를 사용한다. 여러 개면 git remote와 현재 브랜치로 실제 assessment-agent 작업본을 식별한다.
4. 식별할 수 없거나 후보가 없으면 추측하지 않고 경로를 요청한다.

개인 홈 디렉토리의 절대 경로를 기본값으로 두지 않는다. 디렉토리 이름보다 계약 파일과 git remote를 근거로 저장소를 고른다.

## 진실원 5개

| 위치 | 역할 |
|------|------|
| assessment-agent `schema/wire.schema.json` | producer 기계 계약 정본 |
| assessment-agent `docs/payload-contract.md` | producer 사람용 계약 |
| assessment-agent Linux, Windows 발행 소스 | 실제 직렬화 동작 |
| assessment-engine `consumer/schemas.py` | consumer 런타임 검증 계약 |
| assessment-engine `docs/reference/contracts/agent-data.md`와 `wire.schema.json` | consumer 사람용 계약과 기계 사본 |

발행 소스 파일명은 고정 목록으로 가정하지 않는다. `schema_version`, `message_type`, `cJSON_Add`, payload builder 참조를 검색해 inventory, metrics, task.result, error 직렬화 경로를 찾는다. Linux `src/`와 Windows 소스 트리를 모두 본다.

## 감사 절차

1. 두 저장소의 현재 브랜치와 변경 상태를 기록한다.
2. 두 JSON Schema의 `schema_version`, 메시지 discriminator, required, properties, 타입, nullable, enum, 단위를 메시지 종류별로 비교한다.
3. agent 발행 코드가 schema required 필드를 실제로 싣고 optional 필드를 허용 범위 안에서 생략하는지 확인한다.
4. engine Pydantic 모델이 같은 필드와 타입을 받고 `extra=ignore` 계약을 유지하는지 확인한다.
5. 식별과 라우팅은 `agent_id`, `composite_id`와 `machine_id`는 감사 필드라는 `AGENTS.md` #B, #C1 결정이 양 저장소 코드와 문서에 일치하는지 확인한다.
6. task.result의 수집 시각 필드 nullable, 종료 신호 상호배타, `task_policy` 우선순위를 비교한다.
7. metric namespace, attr, 단위 drift는 agent `schema/metric-vocab.json`, 실제 발행 코드, engine mapper와 `agent-data.md`를 비교한다.
8. 불일치가 producer, consumer, 문서 중 어디의 결함인지 근거와 함께 판정한다. 한쪽 문서를 다른 문서의 근거로 삼지 않는다.

## 출력

```text
# 스키마 계약 감사

## 검사 범위
- engine: <branch와 파일>
- agent: <경로, branch와 파일>

## Drift
| 메시지/필드 | producer schema/code | consumer schema/code | 영향 | 수정 위치 |

## Soft Drift
| 위치 | 내용 | 영향 |

## 일치 확인
- 확인한 메시지 종류와 계약 축

## 요약
- reject 위험과 우선 수정 대상
```

Drift는 메시지 reject, 잘못된 라우팅, 단위 오해처럼 런타임 계약이 깨지는 차이다. Soft Drift는 런타임은 호환되지만 문서, optional 의미, 미사용 필드가 어긋난 경우다.

## 금지

- 두 저장소의 파일 수정
- 계약 파일을 찾지 못한 상태에서 필드 추측
- 한 OS 발행 경로만 보고 합의 판정
- 엔진 내부 계층 정합을 본 감사 범위에 포함
- 테스트 실행
