# 서버 보고서 (Server Report)

운영자가 선택한 N대 또는 단일 서버에 대해 row 단위 상세·자원 적정성 판단을 받는 산출물(scope=server)의 존재 의의와 구현 의도. 환경 단위 산출물(scope=environment, 분포 카운트·high-level 요약)은 `docs/explanation/products/environment-report.md` 별도.

## 산출물

선택 N대는 `/reports/servers`, 단일 1대는 `/servers/{id}/report` 로 나뉜다 (파라미터·핸들러는 `docs/reference/web/routers.md`). 어느 쪽이든 customer(양식 A)·engineer(양식 B) 두 view 를 낸다.

발행은 비동기다(T13). 운영자가 발행하면 parent job 이 pending 으로 쌓이고 즉시 `?job={id}` 로 이동하며, 전용 워커가 발행 시점 ViewModel 을 정적 스냅샷으로 만들어 보존한다(customer/engineer 동일). 이후 그 URL 은 저장된 스냅샷을 그대로 렌더해 재계산이 0 이고, 생성 중이면 진행 화면과 폴링을 보여준다. job 없는 GET 은 live read-only preview 다. 발행 이력은 `/reports/history`.

## 위치

- UI 진입점: 서버 목록에서 N대 선택 후 "고객 보고서" / "엔지니어 보고서" 버튼(선택 개수는 옆에 별도 표시). 단건은 서버 상세 페이지에서.
- 산출물 형태: HTML SSR. 브라우저 인쇄로 PDF/PPT 캡처 (백엔드 PDF export 미도입)

## 존재 의의

운영자가 단일 서버 또는 N대 batch 에 대한 정량 분석·자원 적정성 판단을 받기 위한 산출물. 다음 질문에 답한다.

질문 1: "이 N대, 어떤 부하 특성을 보이는가?"

세부 서버 목록 표로 상태·구동 서비스·OS·OS지원종료·인벤토리(vCPU·메모리·디스크)·운영 이벤트·프로비저닝(자원 적정성 분류)을 행 단위로 한눈에 비교(engineer 는 재부팅·재시작 추가). 정량 근거(CPU/메모리 p95·포화·변동성)는 자원 적정성 평가 표·심화 카드에서. row 단위로 정렬·복사·외부 분석 도구 입력 가능.

질문 2: "이 서버 한 대, 자원 배분이 적절한가?"

단일 서버 보고서(`/servers/{id}/report`)가 USE Method 분류·권장 action 을 노출. 운영자가 보고서 한 장에서 즉시 판단 가능 — 환경 분포 비교 불필요.

질문 3: "환경 분포에서 'under_provisioned 5대' 를 봤다 — 그 5대가 누구인가?"

환경 단위 산출물은 분포 카운트만 — 개별 식별 안 됨. 본 서버 단위 batch 발행으로 어떤 서버가 어떤 분류인지 행 단위 확인. 환경 단위 산출물의 행동 follow-up.

질문 4: "자원 적정성 결정의 근거를 어디서 확인하나?"

engineer view 의 진단·분류 칼럼이 USE Method 임계값 기반 자동 해석 노출. 운영자가 "왜 이 서버가 under_provisioned 인가" 를 자원 적정성 평가 표의 근본원인·CPU p95·메모리 p95·포화 축에서 즉시 검증. 별도 detail 페이지 없이 보고서 한 장에서 자원 적정성 의사결정 시그널 확인.

## 산출 정보

### 서버 보고서 — 두 view 공통 상단

상단은 환경 보고서 본문을 그대로 공유하고 대상만 선택 N대로 한정된다 — 영역 구성은 `docs/explanation/products/environment-report.md` "두 view 공통 상단" 단일 진실. 비교 표는 위험 우선 정렬(under -> attention -> normal, 동순위 cpu_p95 DESC)이다.

### view 분기 — customer (양식 A)

목적: 컨설턴트가 고객 미팅·내부 보고에 들고 가는 N대 자원 요약.

구성 = 환경 보고서 본문 공유(customer 분기, `docs/explanation/products/environment-report.md`) + 세부 서버 목록 표.

세부 서버 목록 컬럼(customer): 상태 · 서버 · 구동 서비스(시그니처 워크로드만 — 서버 목록 뱃지와 동일 기준) · OS · OS 지원(지원 종료·무상 종료·보안 패치만·지원 중·미상, 보고서 발행 기준 시각 고정) · 인벤토리(vCPU·메모리·디스크) · 운영 이벤트(보고서 window 내 OOM·MCE·메모리손상·net/disk 에러 발생 유무) · 프로비저닝 · 개별 보고서 링크. 표 자체는 환경 보고서와 같은 매크로(`_shared.html` `detail_server_list`)를 쓴다. CPU/MEM 평균·디스크 최대 칼럼은 지엽적 원시 수치라 제외 — 자원 적정성 분류(프로비저닝 칼럼)가 그 판정 결론.

정성 요약 불릿은 환경 보고서 본문 공유 — view 무관 단일 (구성·항목은 `docs/explanation/products/environment-report.md` "정성 요약" 절).

판단 근거(임계값 전문)는 인쇄본에 임베드하지 않는다 — 보고서 하단에는 화면 전용 경량 링크만 두고, 임계값 전문은 사이드바 "참고" 그룹의 `/reference` 페이지에서 본다(인쇄 분량 절약).

### view 분기 — engineer (양식 B)

목적: 운영자·엔지니어 정량 분석 + 자원 적정성 근거 검증.

구성 = 환경 보고서 본문 공유(engineer 분기, `docs/explanation/products/environment-report.md`) + 세부 서버 목록 표.

세부 서버 목록 컬럼(engineer): customer 컬럼 + 재부팅 · 에이전트 재시작 (시스템 안정성 — anchor+window 안 카운트).

인쇄 2분할도 이 컬럼 세트에 맞춰 표A(구성: 상태·서버·구동서비스·OS·OS지원종료·인벤토리)/표B(평가: 서버·운영이벤트·프로비저닝{engineer 는 +재부팅·재시작})로 재편.

서버별 자원 적정성 표는 환경 보고서 본문 공유분이라 칼럼이 환경 자원 평가 페이지와 같고(`docs/explanation/products/environment-report.md`), 단일 보고서의 자원 적정성 평가 표와도 같은 판독 프레임이다.

### 개별 서버 보고서 — 서버 인벤토리 (구성 계층)

단일 서버 보고서(`/servers/{id}/report`)는 "이 서버가 무엇인가"를 좌우 2열 카드로 노출 — 자원 적정성 평가 앞에 배치.

- 좌열: vCPU·메모리·디스크 요약 카드 + 식별·구성 정보(OS·Kernel·CPU·Swap·내부/외부 IP·Boot Time·Agent Started·Last Inventory, engineer 는 +Agent ID·Composite ID). 서버 상세가 보여주는 인벤토리에 보고서 집계가 가져온 재현 필드(CPU arch/bits·boot firmware·Secure Boot·OS edition·timezone)를 합쳐, 두 페이지가 나눠 보여주던 정보를 한 카드에 종합한다.
- 우열: 서비스 요약 — 워크로드 카테고리별 제품명 묶음(뱃지, 예: "web: nginx, gunicorn") + (customer 전용) 주요 메트릭(CPU/메모리 평균·디스크) 컴팩트 표. 카테고리 판정은 service_classifier 단일 진실이고 listen 소켓만 있는 서비스도 카테고리로 보강한다.
- Listen 포트 카드(engineer 전용, 자원 적정성 카드 다음) — listen 소켓 원시 표(proto·addr·port·uid·pid·process). 카테고리 분류는 서비스 요약이 이미 담당이라 중복 없이 원시 사실만.

### 개별 서버 보고서 — engineer 심화 계층 (단일 deep-dive)

N대 selection 은 서버 간 비교를 위해 행 단위 정량 표(양식 B)로 압축하지만, 단일 1대는 비교 대상이 없어 그 1대를 카드 계층으로 펼친다. engineer 카드 순서는 서버 인벤토리 -> 자원 적정성·운영 평가(통합 1표) -> Listen 포트 -> CPU/메모리/스토리지/네트워크 상세 -> 에러 신호 -> 이용률 추이 + 포화 여부 추이다. customer 는 서버 인벤토리·서비스 요약·주요 메트릭 뒤에 자원 적정성·운영 평가 표 1개만 두고 심화 카드를 붙이지 않는다 — 표 자체는 같은 패턴이고 컬럼 수만 다르다.

양식은 단일·selection·환경이 하나를 공유하고 단일 전용 필드는 나머지 스코프에서 비어 있다 (#C1). CPU 분류(user/system/iowait)·메모리 구성(used/available/cached/buffers)은 N대 표에 없는 단일 전용 집계다.

자원 적정성·운영 평가 표 — 분류·진단(engineer)/근본원인(customer)·권고·신뢰도 + 시스템 에러(윈도우 내 OOM·MCE·메모리손상·net/disk 에러 발생 유무)·네트워크 상태(사이징과 별개 품질 판정)·OS 지원종료(4상태). engineer 만 재부팅·에이전트 재시작(윈도우 카운트) 2칼럼 추가. 세부 서버 목록(N대)의 동명 신호와 같은 산식을 써 화면 간 정합을 지킨다.

CPU/메모리/스토리지/네트워크 상세 카드(engineer 전용) — 윈도우 평균·p95·peak 정량 표 아래 이용률·포화 축 2열(서버 세부·자원 세부 탭과 동일 신호·임계·판정, 네트워크는 포화 열만). 스토리지 카드는 마운트별 표 대신 스토리지 레이아웃 트리로 RAID·LVM·파티션 계층과 마운트별 사용률·inode율을 한 번에 노출한다 — 트리가 마운트 표를 상위호환하므로 마운트 표는 별도로 두지 않는다. 네트워크 카드는 정적 인터페이스 구성(MAC·Speed·MTU·Gateway·주소)도 함께 노출한다.

에러 신호 카드(engineer 전용) — 서버 세부 페이지와 동일한 전 자원 통합 배지(MCE·OOM·EDAC·디스크·네트워크 에러).

이용률 추이·포화 여부 추이(engineer 전용, 2열) — 이용률은 CPU·메모리·디스크 사용률 연속선, 포화 여부는 CPU 실행 큐·메모리 페이징·디스크 I/O 3축의 이진 0/1 상태를 lane 오프셋으로 나란히 놓는다. 같은 구간을 연속값과 판정 두 각도로 겹쳐 읽게 하는 배치다.

### 자원 적정성 평가 — 서버 1대당 산출

| 항목 | 내용 | source |
|------|------|--------|
| 평가 윈도우 | 자원 적정성 표준 창이 기본, URL `?time_range=` override | `right_sizing.WINDOW_DAYS` / `DIAGNOSTIC_DEFAULT_TIME_RANGE` |
| Anchor 시점 | 현재 또는 발행 시점 | default now |
| 분류(배지) | under_provisioned / over_provisioned / idle / optimal / insufficient_data | `classify_host`(배지) + `rollup_host`(근본원인) |
| 권장 action | 자원별 독립 한국어 처방 (증설 검토·축소 검토·종료·통합 검토·적정 유지·표본 부족) | `under_prescription`/`recommend_action` -> `RECOMMENDATION_ACTION_KO` |
| 근본원인 | 부족 자원과 인과 — 단일 자원명 / "메모리 (CPU·디스크 I/O 유발)" / 복수 독립 나열 | `root_cause_display` |

평가 결과는 표 셀로 나간다 — 사이징 목표가 산출되면 처방이 총량 표기("메모리: 22GB | CPU: 8코어")로, 없으면 기본 문구로 들어간다.

## 의사결정 근거

### 분류 임계값·판정

`docs/reference/right-sizing.md` 단일 진실 — 5분류·트리거 조건은 4절, 호스트 요약 상태 판정 순서는 3절, Windows 포화 3축과 미관측 축 처리는 5절. 운영자용 카탈로그 화면은 `/reference`.

### 지표 정의 (engineer view)

engineer view 는 p95·peak·CPU%·MEM%·Saturation·변동성(peak/p95)·DISK/NET I/O baseline 로 근거를 노출한다. 각 지표 정의·임계·출처는 `/reference` 화면의 "엔지니어 보조 지표" 절이 단일 진실.

### 진단 칼럼 (engineer view)

진단 라벨은 최상위 신호 1개만 노출한다 — 엔지니어가 가장 시급한 문제를 즉시 집는 것이 목적이라 신호를 나열하지 않는다. 우선순위 11단과 각 단의 임계는 `/reference` 화면 "진단 칼럼 해석" 표가 단일 진실. 예외 둘 — 표본 부족 호스트는 신호 대신 원인(오프라인 / 누락 메트릭 / 윈도우 내 표본 부족)을 진단하고, 오프라인 호스트는 진단 앞에 "오프라인" 접두를 붙인다(분류는 윈도우 측정 기반 유지).

### 평가 윈도우

기본은 자원 적정성 표준 창이고 URL `?time_range=` 로 바꾼다. 짧은 창은 단발 부하·실시간 시연 검증용이고, 긴 창은 신뢰도가 오르는 대신 최근 변동 반영이 늦다. 창 길이와 그 근거는 `docs/reference/right-sizing-thresholds.md` "무엇을 어떻게 평가하나" 절.

### view 분기 의도

| 항목 | customer (양식 A) | engineer (양식 B) |
|------|-------------------|-------------------|
| 목적 | 고객 의사결정 한 장 요약 | 정량 분석 + 자원 적정성 근거 |
| 정성 요약 | view 무관 단일 (환경 보고서 본문 공유) | 좌동 |
| 위험도 표시 | 5분류 한국어 라벨 — 조치 필요 호스트만 강조 | 5분류 + 진단·근본원인 텍스트 |
| Print 우선 | 인쇄 PDF 대응 | 화면 분석 우선 |

두 view 는 같은 endpoint·SQL·템플릿을 쓰고 Jinja2 블록만 토글한다. 실제로 갈리는 것은 조치 대상 선정과 세부 목록 2칼럼뿐이다.

## 환경 단위 산출물과의 분기

`docs/explanation/products/environment-report.md` 가 갖는다.

## 한계

1. 분류 라벨 어휘가 운영자에게 항상 직관적이지 않음 — "over_provisioned"·"under_provisioned" 의미는 명시적 가이드 (`right_sizing.py` 상수) 에 의존. 한국어 라벨이 한국어 사용자에게 더 명확하지만 영어 분류 식별자는 코드·메시지에 박힘.
2. 워크로드 역할 무관 임계 — DB·캐시·앱서버가 같은 자원별 임계를 공유한다 (값은 `docs/reference/right-sizing-thresholds.md` 단일 진실). DB 는 메모리 압박이 정상 운영일 수 있는데도 자원 부족으로 잡힐 가능성.
3. anchor 임의 선택 가능 — 운영자가 특정 시점 (부하 spike 발생 직후 등) anchor 로 잡으면 분류가 그 윈도우 한정. 표준 창 외 사용 시 운영자가 의도 인지 의무.
4. 정성 요약의 표현 한정 — 결정론 템플릿이라 운영자가 추가 컨텍스트 반영 불가.
5. engineer 세부 서버 목록은 화면에서 최대 11 컬럼이라 인쇄에서 2분할이 필요. 백엔드 PDF export 미도입이라 브라우저 인쇄 의존.
6. 표 정렬은 위험 우선 고정 — 사용자 임의 재정렬·필터 미지원.
7. URL 길이 한계 — `ids` query string 에 N개 public_id 넣음. N 이 매우 크면 URL 한계.

## 관련 문서

- `docs/reference/web/routers.md` — 보고서 라우터·파라미터·view 분기
- `docs/reference/web/services.md` "Recommendation 분류" — USE Method 출처, 구현 위치(조립 서비스·매퍼) 카탈로그
- `docs/reference/db/timescaledb.md` — counter reset 정밀 식별
- `docs/reference/web/static-assets.md` "인쇄 CSS" — 인쇄 색 처리
- `docs/explanation/tradeoffs.md` T13 — 보고서 = diagnostic_jobs 스냅샷 보존
- `docs/explanation/products/environment-report.md` — 환경 단위 산출물 (cross-reference)
