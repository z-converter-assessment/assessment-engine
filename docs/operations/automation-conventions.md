# 자동화 변환 규약 — 검증 책임 분담

본 문서는 `.claude/CLAUDE.md` F5 "자동화 변환 — 책임 분담"의 상세 매뉴얼. CLAUDE.md는 채널 분담·자가 검증 의무·금지 사항 등 결정만 담고, 본 문서는 변환 유형별 추가 체크리스트와 과거에 발생한 사고 패턴(반면교사)을 보존한다.

## 변환 유형별 추가 체크

| 유형 | 추가 검증 |
|------|---------|
| sed / Edit `replace_all` | 들여쓰기 무관 패턴 (`^[[:space:]]*` 사용 여부), 줄 시작·끝 스코프, 문자열 리터럴 안까지 영향 위치 grep |
| 디렉토리 mv | `from X` import (들여쓰기 포함), `import X` (단순), 문자열 형태 모듈 경로 (`"web.main:app"`, target=`"X.Y"` 등), 동적 import (`importlib.import_module`) 모두 grep |
| DTO·모델 타입 변경 | mapper / cache serializer / 템플릿 / inline JS / view_models 체인 — 한 곳 누락 시 cache 역직렬화 또는 attribute access 깨짐 |
| 동시성 코드 (consumer / 핸들러) | placeholder는 `ON CONFLICT DO NOTHING` 의무 (`DO UPDATE`는 진짜 데이터에만). race 시나리오 명시 검증 |
| Frontend JS | 외부 `.js` 파일에서 작업 (inline 신규 금지). 변환 후 `node --check` + 사용자 IDE에서 경고 0건 |

## 누적 사고 패턴 (반면교사)

### 코드 변환

- sed `^from` 패턴이 함수 안 들여쓰기 import 놓침 → `^[[:space:]]*from` 또는 별도 grep 라운드.
- sed가 함수-local 변수(예: `globalRange->capturedRange`)를 함수 외부까지 변환 → awk로 함수 경계 마킹 후 사용 위치 검증.
- 문자열 형태 모듈 경로 (`uvicorn.run("web.main:app")`) 잔존 → import 변환 후 `grep '"[a-z_.]*:'` 별도 라운드.
- placeholder upsert(`ON CONFLICT DO UPDATE`)가 진짜 inventory 덮어쓰는 race → placeholder 전용 메서드는 `ON CONFLICT DO NOTHING` + 충돌 시 다시 find.
- inline JS 변경은 도구 적용 어려움 → 외부 `.js`로 옮긴 후 변경.

### Lima provisioning (도입 검증 round 1~3)

상세 12 사고 패턴은 `docs/operations/lima.md` "누적 사고 패턴" 절 단일 진실. 자동화 변환 측 교훈:

- distro별 패키지명·repo 명명 규약 다름 — `cJSON-devel`(zypper 대문자) vs `libcjson-devel`(apt) vs `cjson-devel`(dnf), `crb`(RHEL 9) vs `powertools`(RHEL 8) vs `ol9_codeready_builder`(OL9), EPEL repo는 OL에서 직접 fetch 필요. dispatch case 추가 시 모든 family 검증 의무.
- cloud image 가용성·크기는 distro별 차이 — 일부 EOL OS(CentOS 7 aarch64)는 origin down, 일부 image는 disk 16 GiB 강제(OL9). yaml `disk` 자원은 image 원본 크기 이상 의무.
- `mountType` 기본 virtiofs가 일부 distro(openSUSE Leap 15)에서 silent fail — yaml에 `mountType: "reverse-sshfs"` 명시로 fallback. mount 검증 = `limactl shell <vm> mount | grep agent` (출력 없으면 fail).
- lima `start_or_resume_vm` final requirement(`boot scripts must finished`)가 distro별 cloud-init 호환성으로 5분+ stuck 가능 — wrapper로 SSH ready+60s 후 PID kill로 우회. SSH 작동이면 boot 성공 판정.
- 검증 사이클은 한 라운드에서 fix 누적 — 첫 시도 실패 markers를 즉시 abort + 진단 + 다음 fix → 최종 round에서 모든 VM boot OK + post-provision exit 0 + agent message 발행 검증.

### 단일 진실 sync

- `dev-down.sh`가 `LIMA_VMS` hardcoded라 `dev-up.sh`와 sync 안 됨 → `source dev-up.sh`로 단일 진실 해결 (BASH_SOURCE source guard로 main 자동 실행 안 함).
- 사용자 reject 후 진행 결정 시 같은 silent block 반복 금지 — 진행 단계마다 1줄 알림 + 결과 즉시 보고 + 1분 cap 룰 준수.

누락 시 사용자 회귀 사고 발견의 책임은 검증 누락에 있음. 같은 패턴 재발 시 본 절에 추가하고 CLAUDE.md F5 메인 자가 검증 절차에 누락된 단계 보강.

## 관련 문서

- `.claude/CLAUDE.md` #F5 — 채널별 책임·메인 자가 검증 의무·Must Not (정책 단일 진실)
- `.claude/CLAUDE.md` #F9 — 변경 영향도 체크리스트 (의미적 단일 진실 보장, F9와 분리)
- `.claude/hooks/conventions-check.sh` — Hook 강제 위반 패턴 카탈로그
