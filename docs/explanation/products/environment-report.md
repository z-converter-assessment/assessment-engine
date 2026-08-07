# 환경 보고서 (Environment Report)

본 문서는 환경 단위 (scope=environment) 산출물의 존재 의의·구현 의도·근거를 정리한다. 서버 단위 산출물(scope=server, 선택 N대 row 단위 상세) 은 `docs/explanation/products/server-report.md` 별도.

## 산출물

환경 보고서 — `GET /reports/environment?view=customer|engineer&time_range=14d`. 환경 안 모든 등록 서버를 묶어 자원 합계·이용률·분류 분포를 high-level 한 장으로 합성한다. customer(양식 A) vs engineer(양식 B) view 분기.

발행 흐름:
- 발행(`POST /reports/environment/emit`)은 parent job 을 pending 으로 만들고 즉시 `?job={id}` 로 이동 — 전용 워커 프로세스가 스냅샷 생성 후 succeeded, 그때 본문 표시(생성 중엔 진행 화면 + 폴링). 발행 전 GET 은 컨트롤(보고서 양식·윈도우·앵커 select + 발행 버튼)만 노출, live preview 본문 없음. 발행된 스냅샷은 `GET /reports/environment?job={id}` 정적 렌더 (서버 scope `/reports/servers` 는 발행 전에도 live preview 본문 유지 — 환경 보고서만 컨트롤-only).
- 워커가 발행 시점(anchor 고정) SQL 집계 + 스냅샷을 `diagnostic_jobs` 테이블 row 의 `result` JSONB 에 정적 보존 (#C1).
- 이력 표시: 보고서 이력 `/reports/history` (customer + engineer union, view 필터).

## 위치

- UI 진입점: 홈/네비 "환경 보고서" 또는 `/reports/environment?view=customer|engineer` 직접 호출
- 산출물 형태: HTML SSR. 브라우저 인쇄로 PDF/PPT 캡처 (백엔드 PDF export 미도입)

## 존재 의의

운영자·고객이 다음 질문에 한눈에 답하기 위한 산출물.

질문 1: "지금 우리 환경, 자원 배분이 적절한가?"

수십·수백 대 서버를 가진 환경에서 개별 서버 상세를 다 확인하지 않고도 환경 전체의 자원 배분 상태(과다·부족·정상)를 분포로 본다. 서버 단위 detail은 너무 많고, 카드 한 장으로는 환경 전반을 못 본다. 그 사이를 메우는 산출물이 환경 보고서.

질문 2: "다음에 어디부터 손대야 하는가?"

분포에서 가장 시급한 카테고리(보통 자원 부족 위험 또는 과다 할당 비용)를 조치 필요 호스트·효율화 검토 대상 표가 이어받는다. 운영자가 "오늘은 과다 할당 5대 다운사이즈 검토"처럼 다음 단계 행동을 결정. 그 다음 단계는 서버 단위 산출물 (`docs/explanation/products/server-report.md`) 로 개별 서버 후보 식별.

질문 3: "고객사·내부 보고 시 자원 현황을 어떻게 요약하는가?"

고객 미팅·내부 정기 보고에서 환경 자원 현황을 한 줄로 표현 가능 — "평가 기준 등록 25대 중 과다 할당 5대·자원 부족 2대·정상 16대·표본 부족 2대". customer view 보고서는 한 장 요약, engineer view 는 정량 분석 추가.

## 산출 정보

### 환경 보고서 — 두 view 공통 상단

| 영역 | 내용 | 데이터 source |
|------|------|--------------|
| 인벤토리 카드 4개 | 등록 서버(+오프라인) / 총 vCPU / 총 메모리 / 총 디스크 | inventory 합산 |
| OS 구성 | 환경 요약(customer)·환경 현황(engineer) 카드 안 소제목 — OS family Windows/Linux 분포(0대 포함 #E9) | `os_family_dist` |
| 서비스 구성 (별도 카드) | 시그니처 워크로드 카테고리별 뱃지. engineer 는 카테고리별 서비스명·개수까지, customer 는 카테고리+개수만. count 0 카테고리는 미노출 | `_aggregate_service_catalog` |
| 분류 분포 | 자원 적정성 5분류 카운트 막대 (한국어 분류명 RECOMMENDATION_LABEL_KO, 영어 enum 미노출) | `classify_host`(호스트별) 결과 카운트 |

### view 분기 — customer (양식 A)

목적: 컨설턴트가 고객 미팅·내부 보고에 들고 가는 한 장짜리 환경 자원 요약.

- 환경 요약: 인벤토리(등록 서버·총 vCPU/메모리/디스크) + 메트릭(CPU/메모리/디스크 평균) + OS 구성(Linux/Windows, 0대 포함 #E9) metric-card 소제목 — 카드는 `.env-stat-card` 너비·높이 통일. 서비스 구성은 별도 카드. engineer 환경 현황과 동일 구조.
- 자원 적정성 평가: 분류 분포(조치 방향) + 효율화 검토 대상(과다 할당·유휴 호스트의 자원 합) + 조치 필요 호스트(자원 부족, high 만).
- 운영 신호: OS 지원 종료 카드만 (2축 정책, 디스크 capacity 는 자원 적정성 평가가 흡수).
- 정성 요약: view 무관 단일 불릿 (아래 "정성 요약" 절).
- 발화 항목은 제목 + placeholder (데이터 0 이어도 노출, #E9).
- Print 우선 — 임계값 전문은 인쇄본에 임베드하지 않는다(사이드바 "참고" 그룹 `/reference`에서 별도 확인, 보고서 본문 인쇄 분량 절약).

### view 분기 — engineer (양식 B)

목적: 운영자·엔지니어가 환경 단위 정량 패턴 분석 + 자원 적정성 근거 검증. customer 와 동일 어휘 + 정량 상세.

- 요약: customer 와 동일 (아래 "정성 요약" 절).
- 환경 현황 카드: 인벤토리(등록 서버·총 vCPU/메모리/디스크) / 메트릭 / OS 구성 소제목. 메트릭 = metric-card 6축 — 이용률 3(CPU·메모리 = capacity-weighted avg + p95 부기, 디스크 용량) + 포화 3(CPU 포화·메모리 압박·디스크 I/O 포화, 발화 호스트 수/표본). 대시보드 '자원 이용·포화' 도넛의 부분집합 — 절대 처리량(네트워크·디스크 I/O rate)은 기준선이 없어 건강 판단이 어려워 제외. 디스크는 p95 를 내지 않는다 — 시점별 capacity 합이 Windows 물리 디스크 인식 불완전으로 신뢰 불가. 인벤토리/메트릭/OS 카드 `.env-stat-card` 높이 통일. 에이전트 버전은 보고서 헤더 메타.
- 환경 부하 추이(시계열 CPU/메모리/디스크, 발행 윈도우 정적 스냅샷) + 네트워크 토폴로지(정적 서브넷 요약 표 — 서브넷 대역·호스트 수만, 인터랙티브 Cytoscape 그래프는 화면 토폴로지 페이지 `/environment/topology` 전용) — 한 카드 2열. 둘 다 engineer 전용.
- 자원 적정성 평가: 분류 분포(소제목 "자원 적정성 분포") + 서버별 자원 적정성(전 서버 통합 표, `action_targets_table` — 환경 자원 평가 페이지와 칼럼 동일: 호스트·사양(CPU·메모리·디스크)·분류(근본원인 병합)·권고(`recommendation_action`, 자원별 독립 처방)·네트워크 상태·디스크 I/O 상태·신뢰도). 조치 호스트 노출은 이 한 표가 단일 진실(별도 효율화 표 없음 — customer view 만 "효율화 검토 대상"/"조치 필요 호스트" 2표로 분리).
- 세부 서버 목록: 환경 보고서는 미표시 (전수 인쇄 폭주 회피 — 조치 대상은 효율화/자원 부족 표가 담음). 선택 N대 보고서(selection)만 표시.
- 운영 신호 = OS 지원 종료만(2축 정책) — 보고서는 전수 표시(절단 없음, 대시보드 카드 한도와 분리). 재부팅·에이전트 재시작은 selection 세부 서버 목록 표에 표시.
- 화면 분석 우선 (인쇄 가능).

분기 메커니즘:
- 같은 endpoint·SQL·템플릿. `summary.view` 로 `_env_report_body.html` 의 customer/engineer 블록을 토글.
- 갈리는 건 조치 대상 선정뿐 — customer 는 위험 호스트(자원 부족)만, engineer 는 전 호스트를 위험도 순으로 낸다. 양쪽 다 운영 검토 list 라 상위 N 절단이 없다 (전수 노출).
- 요약 불릿은 view 무관 단일.

### 정성 요약 — 발행 시점 합성

발행 시점에 고정 3 항목 + 조건부 2 항목을 계산해 불릿으로 합성 (결정론 템플릿, customer·engineer 동일).

| 항목 | 내용 | 조건 |
|------|------|------|
| 등록 서버 | 등록 대수 + 총 vCPU·메모리·디스크 | 항상 |
| 온라인·오프라인 | 온라인 N대 / 오프라인 M대 | 항상 |
| 분류 분포 | 자원 적정성 5분류 한국어 라벨(RECOMMENDATION_LABEL_KO)별 카운트 (표본 부족은 0이면 생략) | 항상 |
| 자원 부족 원인 또는 효율화 여지 | 자원 부족이 있으면 원인 축별 집계, 없고 과다·유휴가 있으면 그 합 | 자원 부족 우선, 둘 다 0이면 생략 |
| OS 지원 종료 | 지원 종료 호스트 수 | 해당 시 |

조치 지시 없이 현상·진단만 담는다 — 우선순위 권고 문장은 요약이 아니라 자원 적정성 평가 표가 담당.

산출 결과 예시:
```
등록 서버 25대 (vCPU 200 | 메모리 512.0 GB | 디스크 8000 GB)
온라인 23대 | 오프라인 2대
자원 적정성 분류 — 자원 부족 2 · 과다 할당 5 · 유휴 0 · 정상 16 · 표본 부족 2
자원 부족 — 메모리 이용률 2대
OS 지원 종료 3대
```

## 의사결정 근거

### 분류 임계값·판정

5분류·트리거 조건 상세는 `docs/reference/right-sizing.md` 4절, 운영자 카탈로그는 `right_sizing_thresholds.html`. 호스트 요약 상태 판정 순서는 같은 문서 3절(`rollup_host`/`classify_host`).

Windows 포화 3축은 perflib 실측이고, 신호가 안 붙는 축만 coverage_gap 으로 부분 평가한다 (임계·신호원은 `docs/reference/right-sizing.md`).

분류 표시 (customer·engineer 공통): 자원 적정성 한국어 분류명(RECOMMENDATION_LABEL_KO) 단일. 내부 risk_level(high/attention/normal)은 조치 필요 호스트 선정·강조용으로만 쓰고, 화면 라벨로 노출하지 않는다 (영어 enum·평행 어휘 금지).

운영 신호 (2축 분리): 자원 적정성 평가(축1, 디스크 capacity·IO 포함)와 별개로 AttentionSignals 3종(통신 끊김·OS 지원 종료·에이전트 재시작)이 운영 신호 축. 보고서는 그중 OS 지원 종료만 카드로 표시(통신 끊김·에이전트 재시작은 윈도우 의미 불일치로 전역 카드 미표시 — 에이전트 재시작은 engineer 호스트 상세 컬럼).

### 평가 윈도우

화면·보고서가 분류와 한 창을 공유한다 (#F10). 길이와 근거는
`docs/reference/right-sizing-thresholds.md` "무엇을 어떻게 평가하나" 절.

### 규칙 기반 한정

분류·권장은 결정론 임계값으로 산출하고, 자연어 요약도 결정론 템플릿으로 만든다.

## 평가 대상 범위의 표현

분모는 별도 커버리지 수치가 아니라 등록 서버 전수다. 시계열 누적이 평가 윈도우에 비해 짧은 신규 서버나 메트릭 누적이 부족한 서버는 분류가 불가한데, 그 대수를 분포 밖으로 빼지 않고 표본 부족 카테고리로 분포 안에 세운다. 분포 자체가 "몇 대가 아직 판단 근거 부족인지"를 드러내므로 "N대 분포가 환경 전체에 적용된다"는 오해가 별도 커버리지 문구 없이 차단된다.

## 서버 단위 산출물과의 분기

| 항목 | 환경 (본 문서) | 서버 (`server-report.md`) |
|------|---------------|--------------------------|
| 발행 단위 | 환경 전체 1건 | 1대 또는 N대 batch (각 1건씩) |
| 보고서 라우터 | `/reports/environment` | `/reports/servers?ids=...` |
| scope | environment | server |
| 산출물 | 분류 분포 카운트 + 우선순위 권장 | 개별 서버 분류·action |
| 답 | "환경 안 over-provisioned 5대 있음" | "이 서버는 under_provisioned, 업사이즈 검토" |
| 운영 단계 | 1단계 — 환경 전체 현황 한눈 | 2단계 — 개별 서버 판단 |

운영자 표준 흐름: 환경 단위로 분포 확인 → 시급한 카테고리의 서버 list 식별 → 서버 단위 batch 로 개별 판단 → detail 화면에서 검증.

## 한계

1. 평균 활용률은 자원 총량 가중(capacity-weighted) 단일 값 — 환경 안 서버 부하 분포가 양극화 (절반 고부하·절반 저부하) 되면 평균만으로는 misleading. 서버 간 분포 (p50·p95) 표시도 검토 후보.
2. 워크로드 역할 무관 임계 — DB·캐시·앱서버가 같은 자원별 임계를 공유한다 (값은 `docs/reference/right-sizing.md` 4절 단일 진실). DB 는 메모리 압박이 정상 운영일 수 있는데도 자원 부족으로 잡힐 가능성.
3. 평가 윈도우 안의 일회성 부하 — 단발 부하 (월 1회 배치 등) 가 그 윈도우 안에 들면 평상 부하로 오인. 외부 윈도우 (30일·90일)·요일/시간대 분리 미적용.
4. 정성 요약의 표현 한정 — 결정론 템플릿이라 운영자가 추가 컨텍스트 (예: "이 서버는 신규 도입 한 달째"·"비용 절감 우선") 를 요약에 반영 불가.
5. 인쇄 색상 — 브라우저 인쇄 시 색 처리가 브라우저별 다름. 흑백 PDF 에서 위험도 색이 비슷해 보일 수 있음. `print` CSS 에서 별도 처리.

## 관련 문서

- `docs/reference/web/routers.md` — 보고서 라우터·view 분기
- `docs/reference/web/services.md` "Recommendation 분류" — USE Method 임계값 출처
- `docs/reference/web/static-assets.md` "인쇄 CSS" — 인쇄 색 처리
- `docs/explanation/tradeoffs.md` T13 — 보고서 = diagnostic_jobs 스냅샷 보존
- 구현 위치(조립 서비스·매퍼·템플릿)는 `docs/reference/web/services.md`·`view-models.md` 카탈로그가 갖는다
- `docs/explanation/products/server-report.md` — 서버 단위 산출물 (cross-reference)
