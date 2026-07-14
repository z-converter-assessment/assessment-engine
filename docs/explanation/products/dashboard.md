# 대시보드 (Dashboard)

본 문서는 사이드바 "모니터링" 그룹 6 페이지 — 환경 개요 홈(`/`)·서버 목록(`/servers`)·환경 자원 평가(`/environment/assessment`)·네트워크 토폴로지(`/environment/topology`)·실시간 현황(`/environment/realtime`)·환경 성능 추이(`/environment/metrics`) — 산출물의 존재 의의·구현 의도·근거를 정리한다. 라우터·서비스·매퍼·정적 자원 deep dive는 `docs/reference/web/` 별도. 사이드바 네비 정의 단일 진실은 `web/templating/setup.py` `NAV_GROUPS`.

## 위치

사이드바 "모니터링" 그룹 (네비 항목명 = 화면 제목):

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

운영자가 환경 전체 상태를 한 화면에서 파악·다음 행동 결정하기 위한 entry point. 집계형 위젯만 본 페이지에 남고, 서버 목록은 `/servers`, 환경 자원 평가는 `/environment/assessment`, 그 외 환경 단위 분석은 `/environment/*` 로 분리. 정적 집계라 새로고침 시 1회 렌더. 본문 partial = `servers/_environment_overview.html` (4 카드: 환경 요약 / 주요 워크로드·자원 적정성(한 행) / 자원 이용·포화 / 시스템 에러). 온라인/오프라인 대수는 상단 바 fleet 상태(`/api/fleet-status`)로 상시 표시.

### 영역 1: 환경 요약

- 총 N대 / 자원 합계 (vCPU·메모리·디스크) KPI + OS 분포 + OS 지원(EOL) 3상태 카운트(지원중·미상·종료 — `lookup_os_eol` 단일 진실, 서버 목록 `os_eol_status` 와 동일 판정)

답: "지금 환경에 몇 대 있고 자원 총량은? OS 지원 종료 위험이 있나?"

### 영역 2: 주요 워크로드

- 시그니처 카테고리(web·db·cache·mq·container·monitor) 인스턴스 분포 원형 도넛 (`overview.workload_donut`) + 단일 열 범례(카운트). 환경 성격 규정 티어만 — baseline·관리(remote·file·mail·infra)는 어디에나 있어 성격 구분에 무의미하므로 제외(`SIGNATURE_CATEGORIES`)
- 인스턴스 합산(호스트 dedup 아님, mq 2개면 2). container 는 호스트당 1. 카운트 0 카테고리도 범례 노출(E9 — "원래 나타내는데 0대")
- 서버 목록 badge 도 동일 시그니처 필터 적용

답: "이 환경이 무슨 성격인가? 어떤 워크로드가 몇 개 떠 있나?"

### 영역 3: 자원 적정성 분포

- 자원 적정성 6분류 카운트 가로 막대 (`overview.risk_donut`) — 환경 자원 평가 페이지와 `provisioning_dist_bar` 매크로 공유(단일 소스)
- 윈도우 = `recommendation.WINDOW_DAYS`(14일) — 서버 목록·보고서 분류와 정합(#E3). 이용률·포화 도넛도 같은 14일 창(화면 간 한 창 통일)
- 홈에는 분포 요약만 — 서버별 자원 적정성 상세 표는 환경 자원 평가 페이지(`/environment/assessment`)

답: "환경이 자원 관점에서 적정한가? 부족·과다 서버 분포는?"

### 영역 4: 자원 이용·포화 도넛

- 이용률 3(CPU·메모리·디스크 capacity-weighted 14일 평균, 색 분기 60·80% UI badge 임계) + 포화 4(CPU 포화·메모리 압박·디스크 I/O 포화·네트워크 혼잡 호스트 수 / 표본)
- 전부 `recommendation.WINDOW_DAYS`(14일) 창 — 분류와 한 창 통일(#E3). 포화는 dual-gate(CPU·메모리 신호 AND 이용률) 판정 호스트 수
- 순간 스냅샷 아닌 윈도우 통계 (실시간 순간값은 `/environment/realtime`)

답: "환경 전체 자원 활용·포화 수준은?"

### 영역 5: 시스템 에러

- fleet 에러 발생 호스트 수 표시자 — 하드웨어(MCE·EDAC)·OOM·디스크·NIC 에러 5종만. 전체 기간 조회(에러는 드문 이벤트라 창 제한 부적합), 발생 대수만 badge (정상=0 발화, E9). OS 지원 종료(EOL)는 이 카드에 없음 — "환경 요약" 카드의 "OS 지원" KPI(지원중/미상/종료 3상태 카운트)로만 노출(영역 1)

답: "지금 손대야 할 하드웨어 이벤트가 있나?"

서버별 자원 적정성 상세 표는 본 홈이 아니라 환경 자원 평가 페이지(`/environment/assessment`)로 분리(홈은 분포 요약만). 운영 신호(통신 끊김·에이전트 재시작)는 실시간 현황·서버 상세 등 발화 지점에서 노출.

## 서버 목록 (`/servers`)

검색·필터 + 선택 N대 액션. page 기반 pagination, 기본 20대 표시 후 전체보기/접기.

### 서버 테이블 (행별)

- 컬럼: 선택 / 상태 (online dot) / 워크로드 (분류 칩) / Hostname / CPU · 메모리 · 디스크 (spec_display) / OS / OS 지원 종료 (3상태) / 자원 적정성 (분류 라벨) / 운영 이벤트 (전체 기간 에러 발생 유무) / ZDM Install / 상세
- 외부 IP 컬럼 없음 — 식별·분류에 미사용
- 워크로드 컬럼 — 시그니처 워크로드 카테고리(`SIGNATURE_CATEGORIES`) 칩(known_services, 카테고리명). 환경 개요 주요 워크로드와 동일 필터(baseline·관리 제외, 목록 노이즈 회피). 미분류는 "—"
- CPU · 메모리 · 디스크 컬럼 — 정적 배정 사양(`spec_display`, 실측 이용률 아님)
- OS 지원 종료 컬럼 — 지원 중(무채) / 미상(amber, EOL 카탈로그 미수록·미매칭) / 지원 종료(빨강) 3상태
- 자원 적정성 컬럼 — `recommendation.classify` 결과(`under_provisioned`/`over_provisioned`/`idle`/`shutdown`/`optimal`/`insufficient_data`), under_provisioned 만 빨강 강조
- 운영 이벤트 컬럼 — 수집 전체 기간 에러 이벤트(OOM kill·MCE·메모리 손상·네트워크/디스크 에러) 발생 유무만(문제 있음/이상 없음), 발생 시점·건수는 서버 상세에서 확인
- ZDM Install 컬럼 — 최근 install task badge(success/failure/pending) + 클릭 시 modal 로 stdout/stderr/failure_reason 디버깅. modal 본문은 server fragment endpoint (`GET /api/tasks/{id}/detail`) HTML 반환 (P3 정공)
- 기본 표시 20대 후 "전체보기"(CLIP 초과 행 노출)/"접기" 토글 — 필터 비활성 상태에서만 적용
- 필터(별도 행으로 분리): search(hostname) / is_online (전체·온라인·오프라인) / service (web/db/cache/mq/container/monitor/remote/file/mail/infra) / os_id (distro) / classification (자원 적정성 6 분류) — 검색 버튼 없음, dropdown/checkbox 변경 즉시 client-side filter + URL 갱신
- pagination: page=1 default, limit=20 (max 100)

답: "어떤 서버가 어떤 상태인가? 어떤 행동을 권장받나?"

### 행동 버튼 (selection-driven)

list에서 N대 선택 → 다음 액션 활성화:
- 고객 보고서 (양식 A 발행)
- 엔지니어 보고서 (양식 B 발행)
- JSON Export (자동화 도구 입력)
- Install (zconverter task 발행)

답: "선택한 N대에 어떤 다음 단계를 진행할 것인가?"

## 환경 자원 평가 (`/environment/assessment`)

환경 전체 자원 적정성 평가 — 분류 분포 + 서버별 자원 적정성 통합 표. 엔지니어 환경 보고서 본문과 동일 표(`reports/_resource_tables.html` 공유, `action_targets_table`)를 화면용으로 노출. 본문 partial = `servers/_assessment_result.html`.

- 자원 적정성 평가 막대: 분류 카운트 막대 (`overview.risk_donut`). 평가 대상 N대 표기.
- 서버별 자원 적정성 표: 전 서버(자원 부족·과다 할당·유휴·정상·표본 부족) 한 표에 — 호스트·사양(CPU·메모리·디스크)·분류(근본원인 병합)·권고(자원별 독립 처방)·네트워크 상태·디스크 I/O 상태·신뢰도. 초과 행은 더보기/접기 토글.
- 구간·앵커 선택: `?time_range=`(15분~30일) + 기준 시각 override. 변경 즉시 `assessment.js` 가 `?fragment=result` 로 본문 swap. 기본 윈도우는 `DIAGNOSTIC_DEFAULT_TIME_RANGE`(14d) — 보고서·서버 목록 분류 표준 평가(`recommendation.WINDOW_DAYS`=14일)와 동일. 본 평가 페이지만 대시보드 중 윈도우 override 허용(#F10).
- Windows (원칙 P2): swap 축 제외(pagefile baseline)·saturation 축 OS 부재라 utilization 축만으로 분류(부분 평가). 상세 `docs/reference/web/services.md` "OS 분기" 절.

답: "환경 안 자원 부족·자원 과다 서버는 누구이고, 무엇부터 손대야 하나?"

## 네트워크 토폴로지 (`/environment/topology`)

L3 subnet 공동소속 추론 그래프 — 인터랙티브 Cytoscape.js (vendored) 렌더. 화면 전용 (보고서는 정적 서브넷 요약 표만, `docs/explanation/products/environment-report.md`).

- 서브넷 연결도: 3계층 그래프 — 라우터(게이트웨이) -> 서브넷 -> 호스트. gateway·subnet·route 엣지가 라우팅 골격으로 기본 표시, 호스트는 서브넷 클릭 시 펼침(collapsed 초기, 대규모 hairball 회피). 같은 gateway 공유 서브넷은 한 라우터 노드로 묶여 라우팅 계층이 드러남. 가상망·IPv6·단독 subnet 제외. 호스트 색=OS(linux/windows), 멀티홈(2+ 서브넷=브리지/라우터 후보)은 사각+테두리 강조. 노드 hover 시 이웃만 강조 + 상세 툴팁(인터페이스명·MAC·MTU·게이트웨이). breadthfirst 계층 레이아웃.
- 서브넷별 소속 서버 표: 호스트(+멀티홈 뱃지)·온라인 상태·IP(+origin dhcp/static)·MTU·링크 속도·OS·시그니처 워크로드. 게이트웨이는 행마다 반복 안 하고 서브넷 헤더행에 1회만(서브넷당 이미 1개로 disambiguation 완료).
- 추론이라 실측 reachability 아님 — caveat 노출(#E9). 매퍼 `mappers/topology.build_network_topology` 단일 진실, 렌더는 `static/js/pages/network-topology.js`(P4).

답: "어떤 서버가 같은 서브넷에 묶여 있나?"

## 실시간 현황 (`/environment/realtime`)

최신 스냅샷 기준 현재 자원 현황 — `realtime.js` 가 30초 주기로 `?fragment=realtime` fetch 후 `#rt-mount` swap + 갱신 시각 표시 (P3 정공 — 1회 fetch 아니라 polling). 클릭 위임(정렬·더보기)은 swap 으로 안 바뀌는 `#rt-mount` 자체에 걸어 매 swap 후에도 유지.

- "현재 자원 현황" 카드 — 이용률 도넛 2(CPU·메모리, 환경 평균 도넛과 동일 컴포넌트·단색 게이지, 단 14일 평균 통계 아닌 현재 스냅샷. 디스크 용량(fill%)은 느린 누적 축이라 실시간 신호에서 제외, 디스크 I/O 이용률은 장치 종류별 신뢰도 편차라(SSD/NVMe 병렬 처리, right-sizing-thresholds.md "Disk IO" 절 Gregg 근거) 환경 평균 도넛으로 안 묶고 아래 부하 표 칼럼 전용) + 신호 도넛 4(실행 큐 임계·페이징·디스크 응답지연 임계·네트워크 혼잡 — 순간 단일신호 임계 초과 호스트 수/표본. 개요·보고서 14일 dual-gate 포화와 다른 정의, 신호명 라벨이지 판정어 아님)
- "서버별 실시간 부하" 카드 — CPU·메모리 이용률/실행 큐/페이징/디스크 이용률/디스크 응답지연/네트워크 7축을 호스트당 1행으로(top-N 절단 없음), 서버 목록·자원 부족 표와 동일 sortable-table 관례(칼럼 클릭 정렬 + 20개 초과 시 더보기/접기). 디스크 이용률(Utilization, 도넛 없이 표 전용)·응답지연(Saturation)은 USE Method상 별개 축 — 이용률 낮은 호스트는 응답지연이 표본 부족("—")이어도 정상, 판정 없이 raw 값만. 페이징은 소수점 2자리 표시(Linux 임계가 "> 0"이라 정수 반올림하면 신호 도넛 카운트와 표 값이 안 맞아 보임). 네트워크 칼럼은 처리량이 아닌 혼잡 판정(net_signal_active — 재전송·드롭·conntrack, 네트워크 혼잡 도넛과 동일 신호)만 정상/혼잡으로 표시 — 처리량은 판정 대상이 아니라 칼럼에서 제외. 특정 축 부하 순 랭킹이 필요하면 그 칼럼을 클릭

답: "지금 이 순간 환경 부하는 어떤가?"

## 환경 성능 추이 (`/environment/metrics`)

환경 단위 시계열 차트 — 전 서버 capacity-weighted 평균 추이. 본문 = `servers/environment_metrics.html`.

- CPU·메모리·스토리지 이용률(capacity-weighted, 0~100% 고정 y축)·네트워크 처리량(rx/tx bytes/s, floating y축) 추이 + CPU 실행 큐 포화 서버 수(recommendation.cpu_saturation_index 와 동일 임계 판정을 SQL 이식 — Linux procs_running·Windows Processor Queue Length 를 각자 임계로 판정한 crossing 서버 수, "윈도우 정규화 보정")·메모리 페이징 압박 서버 수(recommendation.mem_pressure_active 와 동일 원자료·임계 — Linux refault(임계 >0)·Windows Pages Input/sec(임계 20/s) crossing 서버 수)·디스크 I/O 포화 서버 수(`RS_DISKIO_AWAIT_MS` 서버별 판정 crossing 서버 수 — worst-device await 단일값보다 영향 범위가 바로 읽히는 count 로 통일)·네트워크 이상 서버 수(recommendation.assess_network 의 network_congested 와 동일 원자료·임계 — 재전송율>1%·드롭율>0.5%(저트래픽 게이트)·conntrack 고갈>=0.8 OR). 넷 다 강도(연속 지수/worst 단일값)가 아닌 count(임계 넘은 서버 수)로 통일 — 도메인 지식 없이 바로 읽히고 분모(온라인 대수) 변동에 왜곡되지 않는 절대치(리눅스 페이징은 애초에 magnitude 아닌 존재 판정이라 지수화 자체가 불가능해 CPU·디스크·네트워크도 일관성 위해 count 로 맞춤). CPU 분류(User/System/I·O Wait/Nice)는 환경(여러 호스트 혼합) 단위에서 제외 — Windows 가 해당 신호를 아예 미발행이라 "환경" 명목의 차트가 사실상 Linux 전용이 되는 오인 소지(서버 상세 성능 추이는 단일 호스트라 계속 보유). CPU/메모리/디스크 PSI 는 트렌드 차트로는 전 스코프(환경·서버 상세)에서 제외 — 실시간 메트릭 카드의 포화 열 순간값으로만 표시(추이선은 정보 밀도 대비 판단 기여가 낮음). 디스크 IOPS·처리량·네트워크 PPS(합산 절대값)도 제외 — 이기종 장치를 그냥 더한 숫자는 비교 기준선이 없어 해석 불가. 스토리지 사용량(절대 총량)도 서버마다 프로비저닝 용량이 제각각이라 위험도를 못 읽어 사용률(%)로 대체. TCP 재전송율·패킷 드롭율은 두 % 라인이 시각적으로 거의 겹쳐 구분이 안 돼 네트워크 이상 서버 수로 통합.
- 구간(globalRange)·앵커 토글 — `?time_range=` + 기준 시각, 차트 P4 동적 fetch (`AUTO_BUCKET[range]` 동적 bucket, #F10).
- 선택 N대 진입(`?ids=`) 시 "선택 N대 성능 추이" 로 제목·집계 범위 한정.

답: "환경 전체 자원 추이가 시간에 따라 어떻게 변했나?"

## 의사결정 근거

활용률 임계 신호:
- UI badge "warn"(노랑)·"danger"(빨강) 두 단계로 시각 구분
- 서버 badge 임계(`_USAGE_WARN_PCT`·`_USAGE_DANGER_PCT`)와 환경 평균 임계(`_UTIL_LOW_PCT`·`_UTIL_HIGH_PCT`)는 별 도메인 — 값은 `web/services/mappers/shared.py` 단일 진실, 대시보드는 표현만

대시보드 평가 윈도우 14일:
- `recommendation.WINDOW_DAYS`(14일) 단일 진실 (CLAUDE.md #F10) — 분류·평균 활용률·포화 도넛 전부 한 창 통일(#E3 화면 간 정합)
- 환경 개요 홈·성능 추이는 표준 윈도우 고정. 환경 자원 평가 페이지(`/environment/assessment`)만 `?time_range=` override 허용 (기본값 `DIAGNOSTIC_DEFAULT_TIME_RANGE`=14d)
- 실시간 현황 페이지만 창 무관 — 최신 순간 스냅샷

자원 적정성 평가 분류 막대 (환경 자원 평가 페이지):
- 6분류(under/over/idle/shutdown/optimal/insufficient_data) 카운트 막대 — `recommendation.assess` 규칙 분류, `build_risk_donut_segments`
- 분류명은 한국어(LABEL_KO) 단일 진실 — 영어 enum 노출 금지, 보고서·화면 통일
- 막대 색은 게이지 테마 단색 통일 (라벨이 의미 전달) — `UTIL_GAUGE_COLOR`
- 임계 색 단일 진실 — 동일 의미는 동일 hex (활용률·자원 적정성·capacity trigger 일관, CLAUDE.md #E8)

모든 카테고리 항상 노출 (count 0 포함):
- 환경에 자원 부족이 0이어도 카테고리 카드 노출 (옅은 회색)
- 카드 위치 변동이 운영자 인지에 영향 — 슬롯 고정

## 한계

1. 활용률 도넛은 환경 평균만 — 분포(p50·p95)는 미노출. 양극화 환경에서 misleading (`docs/explanation/products/environment-report.md` 한계 #2와 동일 패턴).
2. 행별 권장 단일 라벨 — recommendation 분류 1개만 표시. 다중 신호(예: CPU 정상 + 메모리 부족)는 우선순위 평가 후 1개만.
3. 실시간 현황은 30초 polling 갱신 — server push(SSE/WebSocket) 미도입. 주기 사이 변화는 다음 fetch까지 미반영.

## 관련 문서·코드

- `docs/reference/web/layering.md` — 라우터 흐름·다이어그램
- `docs/reference/web/services.md` — query_service·service_classifier
- `docs/reference/web/view-models.md` — ViewModel 카탈로그·도넛 SVG 상수
- `docs/reference/web/static-assets.md` — list-table.js·차트 P4 규약
- `docs/explanation/products/{environment-report,server-report}.md` — 보고서 산출물 (scope별)
- `docs/explanation/products/install-task.md` — "ZDM Install" column source
- `src/assessment_engine/web/routers/pages/list_page.py` — overview·서버 목록·자원 평가·토폴로지·실시간·성능 추이 라우터
- `src/assessment_engine/web/templating/setup.py` `NAV_GROUPS` — 사이드바 네비 정의 단일 진실
- `src/assessment_engine/web/templates/servers/overview.html` (+ `_environment_overview.html`) — 환경 개요 홈
- `src/assessment_engine/web/templates/servers/assessment.html` (+ `_assessment_result.html`) — 환경 자원 평가
- `src/assessment_engine/web/templates/servers/topology.html` — 네트워크 토폴로지
- `src/assessment_engine/web/templates/servers/realtime.html` (+ `_environment_realtime.html`) — 실시간 현황
- `src/assessment_engine/web/templates/servers/environment_metrics.html` — 환경 성능 추이
- `src/assessment_engine/web/templates/servers/list_table.html` — 서버 목록 테이블
- `src/assessment_engine/web/static/js/pages/{list-table,assessment,realtime}.js` — selection·필터·task cell polling / 자원 평가 swap / 실시간 30초 polling
- CLAUDE.md #E1·#E2·#E3·#E8 — 표시 계층 원칙·데이터 흐름·임계 색 단일 진실
