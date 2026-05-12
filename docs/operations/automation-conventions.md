# 자동화 변환 규약 — 검증 책임 분담

본 문서는 `.claude/CLAUDE.md` F9 "자동화 변환 — 책임 분담"의 상세 매뉴얼. CLAUDE.md는 채널 분담·자가 검증 의무·금지 사항 등 결정만 담고, 본 문서는 변환 유형별 추가 체크리스트와 과거에 발생한 사고 패턴(반면교사)을 보존한다.

## 변환 유형별 추가 체크

| 유형 | 추가 검증 |
|------|---------|
| sed / Edit `replace_all` | 들여쓰기 무관 패턴 (`^[[:space:]]*` 사용 여부), 줄 시작·끝 스코프, 문자열 리터럴 안까지 영향 위치 grep |
| 디렉토리 mv | `from X` import (들여쓰기 포함), `import X` (단순), 문자열 형태 모듈 경로 (`"web.main:app"`, target=`"X.Y"` 등), 동적 import (`importlib.import_module`) 모두 grep |
| DTO·모델 타입 변경 | mapper / cache serializer / 템플릿 / inline JS / view_models 체인 — 한 곳 누락 시 cache 역직렬화 또는 attribute access 깨짐 |
| 동시성 코드 (consumer / 핸들러) | placeholder는 `ON CONFLICT DO NOTHING` 의무 (`DO UPDATE`는 진짜 데이터에만). race 시나리오 명시 검증 |
| Frontend JS | 외부 `.js` 파일에서 작업 (inline 신규 금지). 변환 후 `node --check` + 사용자 IDE에서 경고 0건 |

## 누적 사고 패턴 (반면교사)

- sed `^from` 패턴이 함수 안 들여쓰기 import 놓침 → `^[[:space:]]*from` 또는 별도 grep 라운드.
- sed가 함수-local 변수(예: `globalRange->capturedRange`)를 함수 외부까지 변환 → awk로 함수 경계 마킹 후 사용 위치 검증.
- 문자열 형태 모듈 경로 (`uvicorn.run("web.main:app")`) 잔존 → import 변환 후 `grep '"[a-z_.]*:'` 별도 라운드.
- placeholder upsert(`ON CONFLICT DO UPDATE`)가 진짜 inventory 덮어쓰는 race → placeholder 전용 메서드는 `ON CONFLICT DO NOTHING` + 충돌 시 다시 find.
- inline JS 변경은 도구 적용 어려움 → 외부 `.js`로 옮긴 후 변경.

누락 시 사용자 회귀 사고 발견의 책임은 검증 누락에 있음. 같은 패턴 재발 시 본 절에 추가하고 CLAUDE.md F9 메인 자가 검증 절차에 누락된 단계 보강.

## 관련 문서

- `.claude/CLAUDE.md` #F9 — 채널별 책임·메인 자가 검증 의무·Must Not (정책 단일 진실)
- `.claude/CLAUDE.md` #F13 — 변경 영향도 체크리스트 (의미적 단일 진실 보장, F9와 분리)
- `.claude/hooks/conventions-check.sh` — Hook 강제 위반 패턴 카탈로그
