# 보고서 고객/엔지니어(view) 분류 정책

## 0. 위상
- 협의·정리용 (temp). 보고서 리팩토링 진행 중 customer/engineer 차등의 원칙·항목을 단일 참조로 고정.
- 확정 후 docs/products(환경·서버 보고서)·CLAUDE.md(#E 표시 계층)에 반영 검토. temp 파일은 그때 폐기.
- 본 문서는 "무엇을 어느 view 에 어떻게 보여줄지"의 규칙만 다룬다. 분류 알고리즘·임계값은 right-sizing 단일 진실(recommendation 모듈 / right_sizing_thresholds 참고자료)을 인용만 한다.

## 1. 두 독자 (왜 갈리나)
| view | 독자 | 답해야 할 질문 | 성격 |
|------|------|----------------|------|
| customer (양식 A) | 고객·의사결정자 | 이 환경/서버는 무엇이고, 무엇을 해야 하나 | 결론·행동 중심. 의미 단위 |
| engineer (양식 B) | 운영자·엔지니어 | 그 판정이 어떤 근거로 나왔나 | 사실·정량·근거 중심 |

같은 데이터를 다른 추상화 수준으로 제시한다. 새 데이터를 만들지 않는다 — 노출 깊이만 다르다.

## 2. 공통 불변 규칙 (양 view 동일 — 절대)

R1. 단일 분류 체계.
- right-sizing 분류만 쓴다. 어휘는 recommendation.LABEL_KO 단일 진실:
  리소스 부족 / 과다 프로비저닝 / 유휴 / 종료 권장 / 정상 / 데이터 부족.
- 평행 어휘 금지 — "위험도/검토 권장/자원 부족/효율화 권장" 같은 분류를 가리는 별도 라벨을 새로 만들지 않는다.
- 영어 enum(under_provisioned 등) 화면 노출 금지. CSS class·내부 key 로만 사용.
- 행동(증설/효율화/종료)은 분류에 종속된 조치 설명으로만 표현 — 별도 분류축이 아니다.

R2. 정보 계층 — 구성 -> 활용 -> 평가 순서.
- 구성(무엇): OS·워크로드·구동 서비스·자원 규모.
- 활용(어떻게 쓰이나): CPU/메모리/IO 실사용.
- 평가(어떻게 해야 하나): right-sizing 분류·조치·운영 신호.

R3. 발화 일관성 (discoverability, #E9) — 전 보고서 일괄 적용.
- 발화(조건부) 항목은 반드시 "제목(h2/h3) + 내용 또는 placeholder" 패턴. 데이터 0 이어도 제목과 자리를 남겨 "이 항목이 존재한다"를 보인다. {% if %} 로 항목 통째 숨김 금지.
- 한 줄짜리 항목도 소제목을 부여한다 — 제목 없이 본문만 두면 0 일 때 항목 존재 자체가 사라진다.
- placeholder 는 의미색 없이 회색 통일(empty_state). 정상/미수집 구분 안 함 (불필요한 복잡성 제거).

R4. 인쇄 — 웹 우선 + 인쇄 보정.
- 화면 링크 드릴다운은 인쇄본에서 죽으므로, 인쇄본에 식별 정보(목차·식별자) 보존.
- 판정 근거(참고자료)는 인쇄본 말미에 전문 임베드 — 단독 검토 가능.
- 색 단독으로 의미 전달 금지 — 텍스트 라벨 병행(흑백 생존).

R5. 시각 단순성.
- 강조는 단일 채널(굵기 하나 또는 단일 표식). 색 가짓수·폰트 종류 늘리지 않는다.
- 의미 인코딩을 다중 색/폰트로 하지 않는다.

## 3. view 차등 축 (R1~R5 위에서 갈리는 부분)
| 축 | customer (의미) | engineer (사실) |
|----|------------------|------------------|
| 분류 표현 | 분류 분포 + 우선 조치 한 줄 | 분포 + 호스트별 정량·자동 판단 |
| 구동 서비스 | 카테고리별 제품명 (예: web: nginx) — 포트 숨김 | unit·카테고리·귀속 포트 + listen 포트 전체 |
| 정량 지표 | 평균·요약값 (CPU/MEM 평균) | p95·peak·변동성·saturation·iowait·disk/net IO |
| 운영 신호 | 고객 영향 핵심만 (필요 시) | OS EOL·재부팅·에이전트 재시작·수집 끊김 전체 |
| 위험 강조 | 분류 색 + 우선순위 정렬 | 분류 색 + 정량 근거 동반 |
| 참고자료 | 분류 판정 기준만 특정 | + 보조 지표 정의·운영 신호 임계 |

원칙: 추상 단계(분류명·카테고리)는 양 view 공유. 구체 단계(포트·unit·정량 raw·근거)는 engineer 에서만 완전 노출.

## 4. 보고서별 섹션 매트릭스 (목표 상태)

### 환경 보고서 (scope=environment)
| 계층 | 섹션 | customer | engineer |
|------|------|----------|----------|
| 머리 | 요약 | 규모 + 분류 분포 + 우선 조치 | 규모 + 분류 분포 + 기술 신호 |
| 구성 | 환경 구성 (OS·워크로드 막대) | 노출 | 노출 |
| 구성 | 환경 요약 (자원 규모) | 노출 | 노출 |
| 활용 | 평균 활용률 | 노출(placeholder) | 노출(placeholder) |
| 평가(축1) | Right-sizing 평가 | 분포 + 평가 커버리지 + 효율화 대상 규모 + 조치 필요 호스트(리소스 부족) | 분포 + 호스트 상세 정량 표 |
| 평가(축1) | 언더 프로비저닝 상세 (trigger) | 미노출 | 노출 |
| 평가(축1) | 디스크 capacity 임박 | right-sizing 흡수 — 조치 필요 호스트(under)에 포함, 별도 카드 없음 | 별도 섹션 |
| 평가(축1) | 평가 표본 부족 | 미노출 | 노출 |
| 운영신호(축2) | OS 지원 종료 | OS 지원 종료 카드 (단일, 발화 placeholder) | OS 지원 종료 표 |
| 운영신호(축2) | 통신 끊김 · 에이전트 재시작 | 미표시 (C1: 윈도우 의미 불일치) | 미표시 / 시스템 안정성 컬럼 |
| 꼬리 | 참고자료 | 분류 기준 특정 | 전체(지표·신호 포함) |

### 서버 보고서 (scope=server)
선택 N대 표 + 개별 서버 보고서 두 양식. (드릴다운: 표 -> 개별)
| 계층 | 섹션 | customer | engineer |
|------|------|----------|----------|
| 머리 | 선택 맥락 (N대 OS·워크로드 요약) | 노출 | 노출 |
| 구성 | 구동 서비스 (개별 보고서) | 카테고리별 제품명 | unit·포트 + listen 전체 |
| 활용 | 사용률 | 평균 | p95·peak·변동성·IO·net |
| 평가 | 분류 / 판단 | 분류 + 권고 | 분류 + USE 자동 판단 |
| 평가 | 운영 신호 | 미노출/핵심 | OS EOL·시스템 안정성 |
| 드릴다운 | 개별 보고서 연결 | 행 링크 + 인쇄 부록 목차 | 동일 |
| 꼬리 | 참고자료 | 분류 기준 특정 | 전체 |

## 5. 분류 축 — 2축 + 운영신호 표시 정책 (엄밀)

프로젝트에는 독립된 2개 평가 축이 있다. 섞으면 안 된다 (코드 근거: AttentionSignals docstring "USE Method 와 완전 분리").

### 축 1 — USE Method Right-sizing (자원 평가)
- 단일 진실: recommendation 모듈 (`assess`) + right_sizing_thresholds(참고자료).
- 6 분류: 리소스 부족 / 과다 프로비저닝 / 유휴 / 종료 권장 / 정상 / 데이터 부족.
- under_provisioned 유발 trigger 6종: cpu_util · cpu_saturation · mem_util · mem_saturation · disk_capacity(worst mount >= 85%) · disk_io(iowait >= 20%).
- 디스크 capacity·디스크 IO 는 본 축에 통합. 디스크 capacity 임박(30일 projection)도 본 축의 연장 — 운영 신호 아님.

### 축 2 — 운영 신호 (AttentionSignals) — USE Method 와 완전 분리
- 3종 (이게 운영 신호의 전부): 통신 끊김(gap) · OS 지원 종료(os_eol) · 에이전트 재시작(agent_unstable).
- "USE Method 에서 다루지 못하는 인프라/모니터링 이상만". 디스크(capacity·IO)는 축 1 통합이라 여기서 제외.

### 보고서 운영신호 표시 정책 (CLAUDE.md C1)
- 보고서에는 OS 지원 종료(os_eol)만 표시.
- 통신 끊김(gap)·에이전트 재시작(agent_unstable)은 전역·실시간 신호라 보고서의 평가 윈도우와 의미가 불일치 -> 보고서 미표시 (화면 컨텍스트 가드, #E9). 에이전트 재시작은 engineer 호스트 상세 표 "시스템 안정성" 컬럼으로만.
- 결론: 보고서의 "운영 신호" = OS 지원 종료 단일. 디스크 capacity 는 축 1(right-sizing)에 흡수 — 별도 운영신호 카드로 만들지 않는다.

### 금지
- "운영 리스크" 같은 체계에 없는 신조어로 축 1·축 2 를 한 묶음으로 섞지 않는다.
- 본 정책 문서는 분류를 "어느 view 에 어떻게 표시할지"만 규정. 임계·알고리즘은 위 단일 진실을 인용만 한다.

## 6. 진행 현황 + 다음 세션 핸드오프

다음 세션은 서버 보고서(2-1/2-2)부터 시작한다. 본 문서(R1~R5 + 5절 2축)와 아래 핸드오프를 먼저 읽고, 환경 보고서에서 확립한 패턴을 서버 보고서에 적용한다.

### 완료 (환경 영역 — 1-1 / 1-2 / 시계열 / 대시보드 연동)
- 공통 인프라: 참고자료 단일 partial `reports/_thresholds_reference.html` + 전 보고서 꼬리 `reports/_reference_footer.html` (화면 링크 / 인쇄 전문 임베드, view 분기).
- 환경 customer (1-1): 분류 어휘 LABEL_KO 통일(영어 enum·평행 박스 제거), 구성 계층(OS family·워크로드 단일색 막대), 평가 커버리지·효율화 대상 규모·OS 지원 종료 카드, 발화 placeholder, 참고자료 특정.
- 환경 engineer (1-2): 등록서버 표기 customer 통일, "환경 현황" 카드(인벤토리/메트릭 소제목 + p95 막대 + 에이전트 버전), 효율화 여지(customer 동일 기준), 호스트 상세(판단 칼럼 제거=분류 중복·빨간폰트 제거·분류 한글), AI 진단 실패 문구 정리, 디스크 capacity 별도 카드 제거(right-sizing 흡수).
- 환경 부하 추이 (E): 보고서=정적 스냅샷 inline / 대시보드=live(자동갱신). CPU·메모리·디스크 3축. repo `environment_metric_trend`(metric.py, 서버 동등가중) 1개를 보고서·대시보드·API(`/api/servers/environment/metrics-chart`)가 공유. 차트 JS `pages/environment-trend.js`. 읽기전용 라벨(구간/버킷/수집기준), 범례 아래·클릭토글 제거, 가이드선은 대시보드만(data-grid).
- 대시보드 연동: right-sizing 도넛 범례 한글(LABEL_KO)·code/desc 제거, 추이·토폴로지는 별도 카드(세로 1행 1개).
- 인쇄: 참고자료 전문 임베드(page-break), 막대 print-color-adjust + 트랙 테두리, 차트 폭 보정(#env-trend-chart).

### 남은 (서버 보고서 — 2-1 customer / 2-2 engineer)
- 대상 파일: `docs/products/server-report.md`, `templates/servers/report.html`(선택 N대 표), `templates/servers/single_report.html`(개별 1대).
- 서버 보고서는 1차 구현됨(구동 서비스 차등 `workload_groups`/`service_units`/`listen_ports_detail`, 선택 맥락 `build_selection_context`, 위험 우선 정렬 `sort_rows_for_report`, 인쇄 드릴다운 부록). 이를 환경 보고서 수준으로 다듬는 작업:
  - 어휘 = LABEL_KO 단일, 영어 enum·`<code>` 금지 (report.html 표의 `r.recommendation` 등 점검).
  - 발화 항목 = 제목 + placeholder 패턴 (R3).
  - customer=의미(제품명·분류·행동) / engineer=사실(unit·포트·정량) 차등(3절·5절).
  - 운영 신호 = OS 지원 종료만(2축 정책), 디스크 capacity는 right-sizing 흡수.
  - 인쇄 = no-print/print-only/bar-fill/참고자료 임베드 일관.
- 핵심 코드: `view_models/report.py`(ReportRowItem·ReportSummary) / `mappers/report.py`(to_report_row_item·build_*) / `query_service.get_report` / `report_serializer`(정적 스냅샷 라운드트립).

### 확정 결정 로그 (서버 보고서에도 동일 적용)
- D1 ReportRow에 listen_ports 유입(스냅샷 포함). D2 비교=정렬+단일채널 강조. D3 참고자료 단일 partial 전문. D4 단일색 막대. D5 강조 단일 채널.
