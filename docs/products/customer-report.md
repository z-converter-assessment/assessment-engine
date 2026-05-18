# 고객 보고서 (양식 A)

본 문서는 고객 보고서(`/servers/report?view=customer`)의 존재 의의·산출 정보·의사결정 근거를 정리한다. 라우터·서비스·템플릿 deep dive는 `docs/architecture/web/routers.md`·`services.md`·`view-models.md`·`static-assets.md` 별도.

## 위치

- URL: `GET /servers/report?ids=<public_id,...>&period_days=14&view=customer`
- 진입점: 대시보드 list 페이지에서 N대 선택 → "고객 보고서 (N)" 버튼
- 산출물 형태: HTML SSR. 브라우저 인쇄로 PDF/PPT 캡처 (백엔드 PDF export 미도입 — `docs/tradeoffs.md` T 참조)

## 존재 의의

본 보고서는 컨설턴트가 고객 미팅·내부 보고에 들고 가는 한 장짜리 환경 자원 요약. 다음 3개 질문에 답한다.

질문 1: "지금 우리 환경은 안전한가?"

상단 KPI(대상 서버·온라인·주의·고위험·평균 활용률)로 위험도 분포를 즉시 노출. 고객이 자세한 메트릭 없이도 "고위험 5대 있음" 같은 한 줄 결론을 가져간다.

질문 2: "어디부터 조치해야 하는가?"

표 본문 + 자동 정성 요약(summary_bullets)이 고위험 hostname·디스크 임박 hostname·OS EOL hostname을 명시. 고객이 회의에서 "이 서버부터 스케일업 검토"처럼 다음 행동을 결정.

질문 3: "마이그레이션 전 자원 현황은 어떻게 되는가?"

총 vCPU·메모리·디스크 합계로 환경 자원 규모 노출. 다만 실제 instance type 매핑(t3.medium → m5.large 등)은 본 보고서 범위 밖 — JSON Export 산출물이 담당 (`docs/architecture/inventory-export.md`).

## 산출 정보

| 영역 | 내용 | 데이터 source |
|------|------|--------------|
| KPI 6개 | 대상 서버 / 온라인 / 주의 필요 / 고위험 / 평균 CPU p95 / 평균 메모리 p95 | service KPI 집계 (period_days 윈도우) |
| 환경 총 자원 | 총 vCPU / 메모리 / 디스크 | inventory 합산 |
| 역할 분포 | service_classifier 카테고리별 카운트 | inventory + 분류 |
| 메인 표 (8 컬럼) | SERVER · ROLE · OS · CPU p95 · MEM p95 · 위험도 · 상태 · 진단 | repo report_aggregate + recommendation.classify |
| 위험도 source note | 임계값 명시 (CPU p95 70%·메모리 p95 80% 등) | recommendation.py 상수 |
| 자동 요약 bullets | 행동 가능 시그널만 (고위험·주의·디스크 임박·I/O 병목·재부팅·OS EOL) | mapper.build_report_summary_bullets(view="customer") |

평가 윈도우: 14일 default (`recommendation.WINDOW_DAYS`). URL `?period_days=N`으로 override 가능 (1~90일).

## 위험도 분류 근거

| 위험도 | 트리거 조건 | 출처 |
|--------|-----------|------|
| 고위험 | CPU p95 70% 이상 또는 메모리 p95 80% 이상 또는 swap 발생 | Kleinrock 큐잉 이론 + Linux page cache 운영 통념 |
| 주의 필요 | CPU p95 30% 이하 + 메모리 p95 50% 이하 (over-provisioned) 또는 거의 미사용(idle) | AWS Compute Optimizer "over-provisioned"·Azure Advisor "underutilized" |
| 정상 | 그 외 (optimal) | residual |

평가 윈도우 14일 출처:
- AWS Compute Optimizer right-sizing 권장의 표준 윈도우.
- Azure Advisor도 7일·14일 사용.
- 사용량의 일·주 단위 주기성(주중·주말)을 평탄화하기에 충분.

## 양식 A vs 양식 B 분기 의도

본 보고서는 고객 의사결정 직결 정보만 담는다 — "행동 가능한 시그널"이 기준. 다음 시그널은 의도적으로 제외 (양식 B 엔지니어 보고서에만 노출):

- 역할별 평균 CPU 최고치 — 자원 집약 역할 식별. 엔지니어 분석용.
- Saturation (load_15m_max > cpu_cores) — Kleinrock 큐잉 이론 시그널. 엔지니어 sizing 전략용.
- CPU 변동성 (peak/p95 >= 1.5) — sizing 전략 영향. 엔지니어 검토용.

이 시그널들은 고객에게 직관적 행동을 유도하지 못하고, "왜 이게 문제인가" 추가 설명을 요구. 보고서 한 장의 의도(고객이 바로 다음 행동 결정)와 충돌.

## 한계

1. 위험도 3단계 압축 — `recommendation.classify` 5분류(under/over/idle/optimal/insufficient_data)를 high/attention/normal 3단계로 압축. shutdown(거의 미사용)·idle·over_provisioned가 모두 "주의 필요"로 묶임. 고객에게 더 세분된 행동을 제시하지 못함.
2. 평균 활용률 KPI는 산술 평균 — 환경 안 서버 부하 분포가 양극화(절반 고부하·절반 저부하)되면 평균은 misleading. p50·p95 분포 표시도 검토 후보.
3. 워크로드 역할 무관 임계 — DB·캐시·앱서버 모두 같은 70%/80% 임계. DB는 메모리 압박이 정상 운영일 수 있는데 "고위험"으로 잡힐 가능성. 향후 역할별 임계 분기 시 위험도 정밀도 ↑.
4. 인쇄 색상 — 브라우저 인쇄 시 색 처리가 브라우저별 다름. 흑백 PDF에서 위험도 색이 비슷해 보일 수 있음. `print` CSS에서 별도 처리 (`docs/architecture/web/static-assets.md` 참조).
5. URL 길이 한계 — `ids` query string에 N개 public_id 넣음. N이 매우 크면 (~수백 대) URL 한계. 추후 POST + session 도입 검토.

## 관련 문서·코드

- `docs/architecture/web/routers.md` — `pages.py` 보고서 라우터·view 분기
- `docs/architecture/web/services.md` "Recommendation 분류" — USE Method 임계값 출처
- `docs/architecture/web/static-assets.md` "report.html print CSS" — 인쇄 색 처리
- `src/assessment_engine/recommendation.py` — 분류 임계값 상수
- `src/assessment_engine/web/services/query_service.py::get_report` — KPI 집계 + view 분기
- `src/assessment_engine/web/services/mappers.py::build_report_summary_bullets` — 자동 정성 요약 (view 분기)
- `src/assessment_engine/web/templates/servers/report.html` — 양식 A·B 분기 템플릿
- ADR 0010 — 진단 규칙 기반 한정 (보고서 진단 컬럼의 어휘 결정)
