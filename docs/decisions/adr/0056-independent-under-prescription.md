# ADR 0056 — 자원 부족 처방 인과 억제 폐기 (자원별 독립 처방으로 3개 소비처 통일)

상태: Accepted (2026-07-14) — ADR 0052 "종합·근본원인 규칙"의 처방 억제 조항을 supersede(5자원 USE 판정
틀·근본원인 종합 자체는 존속). ADR 0054 4항의 "사이징에 인과 억제 미적용" 원칙을 assessment API 밖으로 확장.

## Context

`recommendation.rollup_host`는 5자원(CPU·메모리·디스크 용량·디스크 I/O·네트워크) 판정 후 인과 사슬(메모리 ->
디스크 I/O -> CPU)로 근본원인(root_cause)을 짚는다. ADR 0052 는 여기서 "root 에만 처방, 하류(증상)는 억제"를
결정했다 — 같은 병목에 여러 자원을 삼중 처방하는 걸 막기 위해서였다.

그런데 이 억제가 세 소비처에 일관되게 적용되지 않았다:

- 보고서(`under_prescription`)와 `/api/right-sizing`(`prescribed_under_kinds` 공유)은 억제를 적용 — 근본원인
  자원만 처방 문구/actions 에 냈다.
- `/api/assessment`(`sizing.axes`)는 ADR 0054 4항에서 이미 억제를 명시적으로 배제했다 — "어세스먼트는 1회성
  마이그레이션 산출이라 재평가 루프가 없어, 억제가 오히려 과소 사이징으로 흐른다."

같은 호스트를 보고서에서 조회하면 "메모리 증설"만 나오는데 `/api/assessment`로 조회하면 CPU 증설도 함께
나온다 — 같은 recommendation 엔진 출력을 소비처마다 다른 정책으로 가공해 서로 다른(표면상 모순된) 조치를
안내하는 상태였다.

억제 유지의 위험은 ADR 0054 가 이미 정확히 짚었다: 근본원인 추정은 발견적(heuristic)이다 — 원인 자원(예:
메모리)만 고친다고 하류(예: CPU)가 실제로 해소된다는 보장은 없다. 사이징 도구가 "낭비 방지" 목적으로 하류
자원의 처방을 억제하면, 추정이 틀렸을 때 실제로 부족한 자원을 그대로 방치하게 된다 — 이건 절감보다 훨씬
비싼 실패(가동 중단·재장애)다. 보고서·right-sizing API 도 같은 위험에 노출돼 있었을 뿐 ADR 0052 시점엔
인지되지 않았다.

## Decision

처방(무엇을 늘릴지)은 3개 소비처 전부 자원별 독립으로 통일한다 — `prescribed_under_kinds`가 인과와 무관하게
관측된 under 자원 전부를 반환한다(`_under_kinds`와 동치화). 근본원인(root_cause/symptom_of_root)은 계산을
유지하고 "왜 부족한가"를 알려주는 진단 근거(root_cause_display, "메모리 (CPU 유발)" 표시)로만 쓴다 — 처방
자체를 걸러내지 않는다.

- `recommendation.under_prescription(host)` — 관측된 under 자원 전부의 처방 문구를 " | "로 나열.
- `/api/right-sizing`의 `recommendation.actions[]` — 마찬가지로 전부 포함. `suppressed[]` 필드는 항상 빈
  배열로 유지(과거 소비자가 이 키의 존재를 가정할 수 있어 필드 자체는 제거하지 않는다 — 스키마 호환).
  `disk_io` io_bound advisory(tier_up)도 더 이상 증상이라고 억제하지 않는다.
- `/api/assessment`의 `sizing.axes[]` — 이미 독립이라 변경 없음(ADR 0054 유지).

호스트 종합 판정(`rollup_host`의 5자원 USE 틀, `host_status` 산출, "worst 자원 승" 폐기)은 그대로 존속한다 —
바뀌는 건 "종합 결과를 처방 단계에서 필터링하느냐"뿐이다.

## Options Considered

1. 처방을 자원별 독립으로 통일(전부 억제 폐기) — 채택
   - 장점: 3개 소비처가 같은 호스트에 항상 같은 조치를 안내. 근본원인 추정 오류가 실제 부족 누락으로 이어지는
     위험 제거. ADR 0054 4항의 안전 논리를 전 소비처로 일관 적용.
   - 단점: 보고서·right-sizing API 응답이 인과 결합 시 더 길어진다(예: "메모리: 22GB | CPU: 12코어"). 삼중
     처방처럼 보일 수 있으나 근본원인 칼럼이 인과를 옆에서 전달해 완화.
2. 억제를 전부 유지(assessment API 도 근본원인만 처방하도록 되돌림)
   - 장점: 응답이 간결, ADR 0052 원안 유지.
   - 단점: assessment API 는 1회성 마이그레이션 산출물이라 재평가 기회가 없다 — 억제가 틀리면 대상 VM 이
     실제로 부족한 채 프로비저닝된다. ADR 0054 가 이미 이 위험을 이유로 배제한 정책을 되돌리는 것이라 후퇴.
3. 소비처별 다른 정책 유지(현행) + 문서화만
   - 장점: 코드 변경 없음.
   - 단점: 같은 호스트, 다른 조치 안내가 그대로 남는다 — 사용자가 보고서와 API 를 같이 쓰면 모순을 직접
     맞닥뜨린다. "왜 다른지" 문서화해도 실제 혼동은 해소 안 됨.

옵션 1 채택 — ADR 0054 가 이미 확립한 안전 우선 논리(추정 오류 시 과소 사이징보다 약간의 정보 과다가 낫다)를
전 소비처로 확장하는 쪽이, 소비처마다 다른 정책을 유지하며 문서로만 차이를 설명하는 것보다 사용자 혼동이
적고 근거도 일관적이다.

## Consequences

장점:
- 같은 호스트를 어느 소비처(보고서/right-sizing API/assessment API)에서 조회해도 "무엇이 부족한가"에 대한
  답이 동일해진다 — 정책 drift 0.
- 근본원인 추정이 틀려도(원인 자원만 고쳐서 하류가 실제로 안 풀리는 경우) 처방에서 실제 부족 자원이 누락되지
  않는다.
- 코드가 단순해진다 — `prescribed_under_kinds`가 `_under_kinds`의 얇은 wrapper로 수렴, symptom_of_root 를
  처방 경로에서 분기하던 로직 제거.

단점·한계:
- 인과 결합 호스트의 처방 문구/actions 가 길어진다 — 사람이 읽을 때 "다 늘리라는 건가" 오인 여지. root_cause
  칼럼이 옆에서 "메모리 (CPU 유발)" 식으로 우선순위를 알려주지만, UI 가 이 칼럼을 안 보여주는 화면이 있다면
  보완 필요(현재 보고서·right-sizing API 응답 모두 root_cause 를 함께 노출해 이 한계를 완화).
- `/api/right-sizing`의 `suppressed[]` 필드가 항상 빈 배열이 되어 사실상 사문화(dead) — 스키마 호환을 위해
  필드는 유지하되, 다음 major 계약 개정 시 제거 검토 대상.

## 관련 문서·코드

- ADR 0052 — 처방 억제 조항(종합·근본원인 규칙 중 "root 에만 처방") supersede. 5자원 USE 판정 틀·근본원인
  종합 자체는 존속.
- ADR 0054 — 4항("사이징에 인과 억제 미적용")의 안전 논리를 assessment API 밖 전 소비처로 확장.
- `src/assessment_engine/recommendation.py` — `prescribed_under_kinds`/`under_prescription`/`rollup_host`.
- `src/assessment_engine/web/services/mappers/right_sizing_api.py` — `_recommendation`(actions/suppressed).
- `docs/reference/right-sizing.md` — 구현 상태 명세 갱신(#F9 문서-코드 정합).
