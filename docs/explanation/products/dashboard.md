# 대시보드 (Dashboard)

사이드바 "모니터링" 그룹 6 페이지의 존재 의의·구현 의도·근거. 네비 정의 단일 진실은 `NAV_GROUPS`.

## 위치

네비 항목명 = 화면 제목:

- 환경 개요 — `GET /` (집계 위젯: 환경 요약·주요 워크로드·자원 적정성·자원 이용·포화·시스템 에러)
- 서버 목록 — `GET /servers` (검색·필터 + 선택 N대 액션)
- 환경 자원 평가 — `GET /environment/assessment` (자원 적정성 평가 분포 막대 + 서버별 자원 적정성 통합 표, 윈도우 override 가능)
- 네트워크 토폴로지 — `GET /environment/topology` (인터랙티브 Cytoscape L3 subnet 그래프)
- 실시간 현황 — `GET /environment/realtime` (현재 스냅샷 이용률 2 + 신호 4 도넛 + 서버별 실시간 부하 sortable-table)
- 환경 성능 추이 — `GET /environment/metrics` (환경 단위 시계열 차트)

공통:
- 진입점: 엔진 web 첫 페이지(`/`) — 운영자가 가장 자주 보는 화면
- 산출물 형태: HTML SSR. 환경 개요·성능 추이·자원 평가 집계 위젯은 정적 렌더(페이지 진입 시 1회). polling 은 두 곳 — 서버 목록 "ZDM Install" cell(install task 진행 추적)·실시간 현황(30초 fragment swap)
- 다른 산출물의 navigation hub — 보고서·Install·Export 모두 본 화면에서 진입

## 환경 개요 홈 (`/`)

운영자가 환경 전체 상태를 한 화면에서 파악·다음 행동 결정하기 위한 entry point. 집계형 위젯만 본 페이지에 남고, 서버 목록은 `/servers`, 환경 자원 평가는 `/environment/assessment`, 그 외 환경 단위 분석은 `/environment/*` 로 분리. 정적 집계라 새로고침 시 1회 렌더. 카드 4장 — 환경 요약 / 주요 워크로드·자원 적정성(한 행) / 자원 이용·포화 / 시스템 에러. 온라인/오프라인 대수는 상단 바 fleet 상태(`/api/fleet-status`)로 상시 표시.

### 영역 1: 환경 요약

- 총 N대 / 자원 합계 (vCPU·메모리·디스크) KPI + OS 분포 + OS 지원 4분기 카운트(지원중·보안패치만·미상·무상종료 — `lookup_os_eol` 단일 진실, 서버 목록 `os_eol_status` 와 동일 판정)

답: "지금 환경에 몇 대 있고 자원 총량은? OS 지원 종료 위험이 있나?"

### 영역 2: 주요 워크로드

- 시그니처 카테고리(web·db·cache·mq·container·monitor) 인스턴스 분포 원형 도넛 + 단일 열 범례(카운트). 환경 성격 규정 티어만 — baseline·관리(remote·file·mail·infra)는 어디에나 있어 성격 구분에 무의미하므로 제외(`SIGNATURE_CATEGORIES`)
- 인스턴스 합산(호스트 dedup 아님, mq 2개면 2). container 는 호스트당 1. 카운트 0 카테고리도 범례 노출(E9 — "원래 나타내는데 0대")
- 서버 목록 badge 도 동일 시그니처 필터 적용

답: "이 환경이 무슨 성격인가? 어떤 워크로드가 몇 개 떠 있나?"

### 영역 3: 자원 적정성 분포

- 자원 적정성 5분류 카운트 가로 막대 — 환경 자원 평가 페이지와 같은 매크로 공유(단일 소스)
- 윈도우는 분류와 같은 창이다 (아래 "평가 윈도우" 절)
- 홈에는 분포 요약만 — 서버별 자원 적정성 상세 표는 환경 자원 평가 페이지(`/environment/assessment`)

답: "환경이 자원 관점에서 적정한가? 부족·과다 서버 분포는?"

### 영역 4: 자원 이용·포화 도넛

- 이용률 3(CPU·메모리·디스크 capacity-weighted 창 평균, 게이지 단색 — 채움 길이로만 정도 표현) + 포화 4(CPU 포화·메모리 압박·디스크 I/O 포화·네트워크 혼잡 호스트 수 / 표본)
- 전부 같은 창이고, 포화는 dual-gate(CPU·메모리 신호 AND 이용률) 판정 호스트 수
- 순간 스냅샷 아닌 윈도우 통계 (실시간 순간값은 `/environment/realtime`)

답: "환경 전체 자원 활용·포화 수준은?"

### 영역 5: 시스템 에러

- fleet 에러 발생 호스트 수 표시자 — 하드웨어(MCE·EDAC)·OOM·디스크·NIC 에러 5종만. 전체 기간 조회(에러는 드문 이벤트라 창 제한 부적합), 발생 대수만 badge (정상=0 발화, E9). OS 지원 종료(EOL)는 이 카드에 없음 — "환경 요약" 카드의 "OS 지원" KPI(지원중/보안패치만/미상/무상종료 4분기 카운트)로만 노출(영역 1)

답: "지금 손대야 할 하드웨어 이벤트가 있나?"

서버별 자원 적정성 상세 표는 본 홈이 아니라 환경 자원 평가 페이지(`/environment/assessment`)로 분리(홈은 분포 요약만). 운영 신호(통신 끊김·에이전트 재시작)는 실시간 현황·서버 상세 등 발화 지점에서 노출.

## 서버 목록 (`/servers`)

검색·필터 + 선택 N대 액션. 전체 로드 후 client-side clip 이고 서버사이드 pagination 은 적용하지 않는다.

### 서버 테이블 (행별)

- 컬럼: 선택 / 상태 (online dot) / 워크로드 (분류 칩) / Hostname / CPU · 메모리 · 디스크 (spec_display) / OS / OS 지원 종료 / 자원 적정성 (분류 라벨) / 운영 이벤트 (전체 기간 에러 발생 유무) / ZDM Install / 상세
- 외부 IP 컬럼 없음 — 식별·분류에 미사용
- 워크로드 컬럼 — 시그니처 워크로드 카테고리(`SIGNATURE_CATEGORIES`) 칩(known_services, 카테고리명). 환경 개요 주요 워크로드와 동일 필터(baseline·관리 제외, 목록 노이즈 회피). 미분류는 "—"
- CPU · 메모리 · 디스크 컬럼 — 정적 배정 사양(`spec_display`, 실측 이용률 아님)
- OS 지원 컬럼 — 5상태. 지원 중(무채) / 보안 패치만(amber, 기능 업데이트 종료) / 미상(보라, 카탈로그 미수록·미매칭) / 무상 종료(빨강, 유상 연장만) / 지원 종료(빨강, 패치 없음)
- 자원 적정성 컬럼 — `rollup_host().recommendation` 배지(`under_provisioned`/`over_provisioned`/`idle`/`optimal`/`insufficient_data`), under_provisioned 만 빨강 강조
- 운영 이벤트 컬럼 — 수집 전체 기간 에러 이벤트(OOM kill·MCE·메모리 손상·네트워크/디스크 에러) 발생 유무만(문제 있음/이상 없음), 발생 시점·건수는 서버 상세에서 확인
- ZDM Install 컬럼 — 최근 install task badge(success/failure/pending) + 클릭 시 modal 로 stdout/stderr/failure_reason 디버깅. modal 본문은 server fragment endpoint (`GET /api/tasks/{id}/detail`) HTML 반환 (P3 정공)
- 기본 표시 20행 후 "전체보기"(CLIP 초과 행 노출)/"접기" 토글 — 필터 비활성 상태에서만 적용
- 필터: 선택 액션 버튼과 같은 툴바 행에 놓인 통합 텍스트 입력 1개 — 행의 상태·워크로드·Hostname·OS·OS 지원 종료·자원 적정성을 합친 문자열에 부분일치. 검색 버튼 없이 입력 즉시 client-side hide/show, "초기화" 링크로 해제
- 딥링크용 query 파라미터: `search`·`is_online`·`service`·`os_distro`·`classification`·`os_eol` — 서버사이드 필터로 진입 시 적용(화면 안 필터 UI 는 통합 입력 하나)

답: "어떤 서버가 어떤 상태인가? 어떤 행동을 권장받나?"

### 행동 버튼 (selection-driven)

list에서 N대 선택 → 다음 액션 활성화:
- 실시간 현황 (선택 N대 스코프로 `/environment/realtime` 진입)
- 성능 추이 (선택 N대 스코프로 `/environment/metrics` 진입)
- 고객 보고서 (양식 A 발행)
- 엔지니어 보고서 (양식 B 발행)
- Export (JSON, 자동화 도구 입력)
- Install (zconverter task 발행)

답: "선택한 N대에 어떤 다음 단계를 진행할 것인가?"

## 환경 자원 평가 (`/environment/assessment`)

환경 전체 자원 적정성 평가 — 분류 분포 + 서버별 자원 적정성 통합 표. 엔지니어 환경 보고서 본문과 같은 표를 화면용으로 노출한다(매크로 공유).

- 자원 적정성 평가 막대: 분류 카운트 막대. 평가 대상 N대 표기.
- 서버별 자원 적정성 표: 전 서버(자원 부족·과다 할당·유휴·정상·표본 부족) 한 표에 — 호스트·사양(CPU·메모리·디스크)·분류(근본원인 병합)·권고(자원별 독립 처방)·네트워크 상태·디스크 I/O 상태·신뢰도. 초과 행은 더보기/접기 토글.
- 구간·앵커 선택: `?time_range=`(15분~30일) + 기준 시각 override. 변경 즉시 결과 fragment 만 swap. 대시보드 6 페이지 중 윈도우 override 를 허용하는 유일한 화면이다 (아래 "평가 윈도우" 절).
- Windows 포화 3축은 perflib 실측이고, 신호가 안 붙는 축만 coverage_gap 으로 부분 평가한다 (임계·신호원은 `docs/reference/right-sizing.md`).

답: "환경 안 자원 부족·자원 과다 서버는 누구이고, 무엇부터 손대야 하나?"

## 네트워크 토폴로지 (`/environment/topology`)

L3 subnet 공동소속 추론 그래프 — 인터랙티브 Cytoscape.js (vendored) 렌더. 화면 전용 (보고서는 정적 서브넷 요약 표만, `docs/explanation/products/environment-report.md`).

- 서브넷 연결도: 3계층 그래프 — 라우터(게이트웨이) -> 서브넷 -> 호스트. gateway·subnet·route 엣지가 라우팅 골격으로 기본 표시, 호스트는 서브넷 클릭 시 펼침(collapsed 초기, 대규모 hairball 회피). 같은 gateway 공유 서브넷은 한 라우터 노드로 묶여 라우팅 계층이 드러남. 가상망·IPv6·단독 subnet 제외. 호스트 색=OS(linux/windows), 멀티홈(2+ 서브넷=브리지/라우터 후보)은 사각+테두리 강조. 노드 hover 시 이웃만 강조 + 상세 툴팁(인터페이스명·MAC·MTU·게이트웨이). breadthfirst 계층 레이아웃.
- 서브넷 분리 규칙: 한 서브넷(IP·prefix)에 서로 다른 non-null 게이트웨이가 2개 이상이면 다른 물리망으로 보고 게이트웨이별로 쪼갠다 — 사설 대역 중복 오병합 방지. 게이트웨이를 발행하지 않는 호스트는 게이트웨이가 1개뿐인 서브넷에는 합류하지만, 게이트웨이가 갈리는 서브넷에서는 귀속시킬 근거가 없어 그래프에서 빠진다.
- 게이트웨이 노드는 2개 이상 서브넷이 공유할 때만 라우터로 승격한다 — 서브넷당 1:1 게이트웨이는 대개 .1 이라 유추 가능해서 노드로 띄우지 않고 서브넷 노드 데이터·툴팁·표에만 노출한다.
- 서브넷별 소속 서버 표: 호스트(+멀티홈 뱃지)·온라인 상태·IP(+origin dhcp/static)·MTU·링크 속도·OS·시그니처 워크로드. 게이트웨이는 행마다 반복 안 하고 서브넷 헤더행에 1회만(서브넷당 이미 1개로 disambiguation 완료).
- 추론이라 실측 reachability 아님 — caveat 노출(#E9).

답: "어떤 서버가 같은 서브넷에 묶여 있나?"

## 실시간 현황 (`/environment/realtime`)

최신 스냅샷 기준 현재 자원 현황 — 30초 주기로 본문 fragment 를 fetch 해 통째로 swap 하고 갱신 시각을 함께 표시한다 (P3 정공 — 1회 fetch 아니라 polling). 클릭 위임(정렬·더보기)은 swap 으로 안 바뀌는 마운트 요소에 걸어 매 swap 후에도 유지한다.

- "현재 자원 현황" 카드 — 이용률 도넛 2(CPU·메모리, 환경 평균 도넛과 동일 컴포넌트·단색 게이지, 단 창 평균 통계 아닌 현재 스냅샷. 디스크 용량(fill%)은 느린 누적 축이라 실시간 신호에서 제외, 디스크 I/O 이용률은 장치 종류별 신뢰도 편차라(SSD/NVMe 병렬 처리, right-sizing-thresholds.md "Disk IO" 절 Gregg 근거) 환경 평균 도넛으로 안 묶고 아래 부하 표 칼럼 전용) + 신호 도넛 4(실행 큐 임계·페이징·디스크 응답지연 임계·네트워크 혼잡 — 순간 단일신호 임계 초과 호스트 수/표본. 개요·보고서의 평가 윈도우 dual-gate 포화와 다른 정의, 신호명 라벨이지 판정어 아님)
- "서버별 실시간 부하" 카드 — CPU·메모리 이용률/실행 큐/페이징/디스크 이용률/디스크 응답지연/네트워크 7축을 호스트당 1행으로(top-N 절단 없음), 서버 목록·자원 부족 표와 동일 sortable-table 관례(칼럼 클릭 정렬 + 20개 초과 시 더보기/접기). 디스크 이용률(Utilization, 도넛 없이 표 전용)·응답지연(Saturation)은 USE Method상 별개 축 — 이용률 낮은 호스트는 응답지연이 표본 부족("—")이어도 정상, 판정 없이 raw 값만. 페이징은 소수점 2자리 표시(Linux 임계가 "> 0"이라 정수 반올림하면 신호 도넛 카운트와 표 값이 안 맞아 보임). 네트워크 칼럼은 처리량이 아닌 재전송·드롭·conntrack 혼잡 판정만 정상/혼잡으로 표시한다. 처리량은 판정 대상이 아니라 칼럼에서 제외한다. 특정 축 부하 순 랭킹이 필요하면 그 칼럼을 클릭

답: "지금 이 순간 환경 부하는 어떤가?"

## 환경 성능 추이 (`/environment/metrics`)

환경 단위 시계열 차트 — 전 서버 capacity-weighted 평균 추이.

- 이용률 추이: CPU·메모리·스토리지(capacity-weighted, 0~100% 고정 y축) + 네트워크 처리량(rx/tx bytes/s, floating y축).
- 포화 추이 4종은 강도가 아니라 임계를 넘은 서버 수(count)로 낸다 — 강도는 도메인 지식이 있어야 읽히고 온라인 대수 변동에 왜곡되는데, count 는 그렇지 않다. 축 카탈로그와 원자료 매핑은 `docs/reference/db/repositories.md` 차트 집계 표.
- 넷 다 강도(연속 지수·worst 단일값)가 아니라 count 로 통일한다 — 도메인 지식 없이 바로 읽히고 분모(온라인 대수) 변동에 왜곡되지 않는 절대치다. Linux 페이징은 magnitude 가 아닌 존재 판정이라 지수화 자체가 불가능해 나머지 셋도 여기 맞춘다.
- 구간(globalRange)·앵커 토글 — `?time_range=` + 기준 시각, 차트 P4 동적 fetch (`AUTO_BUCKET[range]` 동적 bucket, #F10).
- 선택 N대 진입(`?ids=`) 시 "선택 N대 성능 추이" 로 제목·집계 범위 한정.

환경 스코프에서 뺀 축:

- CPU 분류(User/System/I·O Wait/Nice) — Windows 가 해당 신호를 미발행이라 "환경" 명목의 차트가 사실상 Linux 전용이 되는 오인 소지가 있다. 서버 상세 성능 추이는 단일 호스트라 계속 보유한다.
- CPU/메모리/디스크 PSI — 추이선은 정보 밀도 대비 판단 기여가 낮아 전 스코프(환경·서버 상세)에서 빼고 실시간 카드의 포화 열 순간값으로만 표시한다.
- 디스크 IOPS·처리량·네트워크 PPS(합산 절대값) — 이기종 장치를 더한 숫자는 비교 기준선이 없어 해석이 안 된다.
- 스토리지 사용량(절대 총량) — 서버마다 프로비저닝 용량이 달라 위험도를 못 읽는다. 사용률(%)로 대체한다.
- TCP 재전송율·패킷 드롭율 — 두 % 라인이 시각적으로 거의 겹쳐 구분이 안 돼 네트워크 이상 서버 수로 통합한다.

답: "환경 전체 자원 추이가 시간에 따라 어떻게 변했나?"

## 의사결정 근거

활용률 임계 신호:
- 색 분기를 갖는 건 서버 badge 뿐 — "warn"(노랑)·"danger"(빨강) 두 단계로 시각 구분. 임계 상수는 표시 계층 단일 진실이고 대시보드는 표현만 한다 (`docs/reference/web/view-models.md`)
- 환경 평균 이용률 게이지는 단색 — 색으로 임계 의미를 주지 않고 채움 길이가 정도를 전달 (색 상수도 같은 자리)

대시보드 평가 윈도우:
- 분류·평균 활용률·포화 도넛이 전부 한 창으로 통일된다 (#F10 · #E3 화면 간 정합)
- 환경 개요 홈·성능 추이는 표준 윈도우 고정. 환경 자원 평가 페이지(`/environment/assessment`)만 `?time_range=` override 허용 (기본값 `DIAGNOSTIC_DEFAULT_TIME_RANGE`)
- 실시간 현황 페이지만 창 무관 — 최신 순간 스냅샷

자원 적정성 평가 분류 막대 (환경 자원 평가 페이지):
- 5분류(under_provisioned/over_provisioned/idle/optimal/insufficient_data) 카운트 막대 — `rollup_host().recommendation` -> `build_risk_donut_segments`
- 분류명은 한국어(RECOMMENDATION_LABEL_KO) 단일 진실 — 영어 enum 노출 금지, 보고서·화면 통일
- 막대 색은 게이지 테마 단색 통일 (라벨이 의미 전달) — `UTIL_GAUGE_COLOR`
- 임계 색 단일 진실 — 동일 의미는 동일 hex (활용률·자원 적정성·capacity trigger 일관, AGENTS.md #E8)

모든 카테고리 항상 노출 (count 0 포함, #E9):
- 카드·범례 위치가 데이터에 따라 움직이면 운영자가 화면을 다시 읽어야 한다 — 슬롯을 고정하고 발화 없는 카테고리는 옅은 회색으로 남긴다

## 한계

1. 활용률 도넛은 환경 평균만 — 분포(p50·p95)는 미노출. 양극화 환경에서 misleading (`docs/explanation/products/environment-report.md` 한계와 동일 패턴).
2. 행별 권장 단일 라벨 — recommendation 분류 1개만 표시. 다중 신호(예: CPU 정상 + 메모리 부족)는 우선순위 평가 후 1개만.
3. 실시간 갱신 지연 — `docs/explanation/tradeoffs.md` T5.

## 관련 문서

- `docs/reference/web/layering.md` — 라우터 흐름·다이어그램
- `docs/reference/web/services.md` — query 패키지·service_classifier
- `docs/reference/web/view-models.md` — ViewModel 카탈로그·도넛 SVG 상수
- `docs/reference/web/static-assets.md` — list-table.js·차트 P4 규약
- `docs/explanation/products/{environment-report,server-report}.md` — 보고서 산출물 (scope별)
- `docs/explanation/products/install-task.md` — "ZDM Install" column source
- 구현 위치(라우터·서비스·템플릿)는 위 `docs/reference/web/` 카탈로그가 갖는다
- AGENTS.md #E1·#E2·#E3·#E8 — 표시 계층 원칙·데이터 흐름·임계 색 단일 진실
