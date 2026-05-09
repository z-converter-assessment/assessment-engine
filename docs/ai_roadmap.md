# Assessment 서비스 — AI 활용 로드맵

Phase 2~3 (분석 / 추천 / 비용 / 리포트) 도입 설계 문서.
Phase 1 (수집 / 인벤토리 / 대시보드) 은 별도 문서 참조.

---

## 1. 핵심 원칙

1. **수치는 SQL과 룰이 산출하고, 자연어 설명은 LLM이 담당한다.**
   LLM이 수치를 직접 생성하면 추적·감사가 불가능하고 hallucination 위험이 있다.
2. **AI 모델은 LLM 1종으로 시작한다.** Embedding 모델과 Classical ML 은 필요해진 시점에 추가한다.
3. **모든 임계값은 공식 표준에 근거를 둔다.** 코드 주석에 출처를 명시한다.
4. **외부 의존은 공식 API와 표준 스펙 위주로 한다.** OSS 패키지를 무리하게 도입하지 않는다.

---

## 2. 구성 요소

| 컴포넌트 | 구현 | 비고 |
|---|---|---|
| 통계 산출 (avg / p95 / p99.5 / peak) | TimescaleDB SQL | ML 모델 아님 |
| 추천 판정 | 룰 기반 코드 | 임계값 출처 명시 |
| Pricing 비교 | AWS / Azure / GCP 공식 API | FOCUS 스펙으로 정규화 |
| 리포트 narrative · Q&A | LLM 1종 | 입력 JSON 수치만 인용 |
| 워크로드 분류 *(선택)* | scikit-learn KMeans | 추천 정확도 향상용 |
| 이상치 / 계절성 *(선택)* | STL decomposition | LLM 아님 |
| RAG *(선택, 사례 30건 이상 누적 시)* | pgvector + bge-m3 | 별도 Vector DB 도입 안 함 |

---

## 3. 단계별 작업

### A. 평가 기준 정의

추천의 성공 여부를 측정할 수 없으면 이후 단계의 개선이 불가능하다. 가장 먼저 정의한다.

| 지표 | 정의 | 출처 |
|---|---|---|
| Utilization | 사용량 / 할당량 (시간가중 평균) | FinOps Framework |
| Effective Savings Rate | 1 − (실제 비용 / On-demand 환산 비용) | FinOps Foundation |
| Coverage | 평가 대상 서버 / 전체 서버 | FinOps Framework |
| Recommendation accuracy | 추천 적용 후 30일간 p95 가 60~80% 구간 유지 비율 | 자체 정의 |
| Cost forecast accuracy | MAPE (실제 vs 예측) | 시계열 표준 |

**작업 항목**
- 라벨드 셋 30~50건 구축
- `recommendation_outcome` 테이블 추가 (예측 vs 실제 6개월 추적)

### B. 통계 베이스라인

Brendan Gregg 의 **USE Method** (Utilization / Saturation / Errors) 를 채택한다.

| 리소스 | Utilization | Saturation | Errors |
|---|---|---|---|
| CPU | 1 − idle/total | runqueue, load_1m / cores | — |
| Memory | 1 − available/total | swap 사용량, page faults | OOM kill |
| Disk | iostat %util | await, queue depth | I/O errors |
| Network | bytes/s ÷ 회선 용량 | drops, retransmits | rx/tx errors |

**구현**
- TimescaleDB `time_bucket` + `percentile_cont` 로 일 / 주 / 월 집계
- p95 (Azure 기준) 와 p99.5 (AWS 기준) 둘 다 산출

### C. 룰 기반 추천 엔진

임계값은 AWS Compute Optimizer · Azure Advisor · GCP Recommender · 큐잉 이론을 출처로 한다. 코드 주석에 출처를 명시한다.

```python
# 관찰 윈도우
WINDOW_DAYS = 14            # AWS Compute Optimizer 기본값

# 다운사이즈 임계값
CPU_DOWNSIZE_P95 = 30       # AWS Compute Optimizer "Over-provisioned" 패턴
MEM_DOWNSIZE_P95 = 50
HEADROOM_PCT     = 30       # GCP Recommender 마진 (보수값)

# 업사이즈 임계값
CPU_UPSIZE_P95 = 70         # Kleinrock 큐잉 이론, Google SRE Book
MEM_UPSIZE_P95 = 80         # Linux page cache 압박 시작점

# Idle 판정
IDLE_CPU_MAX_PCT  = 1       # AWS Compute Optimizer
IDLE_NET_KBPS     = 1
IDLE_DURATION_DAYS = 14

# Shutdown 권장
SHUTDOWN_CPU_P95  = 3       # Azure Advisor
SHUTDOWN_NET_MBPS = 2
```

**판정 순서**
1. Idle (peak 기준)
2. Swap 사용 (메모리 부족)
3. Over-provisioned (다운사이즈)
4. Under-provisioned (업사이즈)
5. Optimal

### D. Pricing Adapter

- AWS Pricing List API · Azure Retail Prices API · GCP Cloud Billing Catalog API 를 직접 호출한다.
- 응답을 **FOCUS 1.x** 컬럼으로 정규화한다 (`instance_type`, `region`, `vCPU`, `memory_gb`, `unit_price_usd`).
- 환율은 한국은행 ECOS API 를 사용한다.
- Redis 24 시간 캐시.

### E. 워크로드 분류 *(선택 단계)*

C 단계 정확도가 부족할 때 진입한다.

- 입력 feature: 시간대별 CPU 패턴, mem/cpu 비율, IO read/write 비율, 네트워크 burst
- 알고리즘: scikit-learn KMeans, 5~7 개 클러스터
- 활용: 클러스터별로 C 단계 임계값을 차등 적용 (예: 야간 배치형은 평균 utilization 상한을 높임)

### F. 템플릿 리포트

AWS **Well-Architected — Cost Optimization Pillar** 의 Best Practice 목차를 차용한다.

| Pillar 항목 | 리포트 섹션 |
|---|---|
| Practice cloud financial management | Executive Summary (절감액 · ROI) |
| Expenditure and usage awareness | 현재 사용 현황 (USE Method 차트) |
| Cost-effective resources | Right-sizing 추천 |
| Manage demand and supply | 워크로드 패턴 분석 |
| Optimize over time | 6 개월 후 재평가 권고 |

**구현**
- Jinja2 → HTML / Markdown
- WeasyPrint → PDF

### G. LLM 내러티브

- **입력**: C 단계 산출 JSON (수치 포함)
- **출력**: Executive Summary, 권장 사유, 위험 요인 narrative
- **제약**: 입력 JSON 에 존재하는 수치 외에는 어떤 숫자도 생성하지 않는다.
- **검증**: LLM 응답에서 숫자 토큰을 정규식 추출하여 입력 JSON 에 존재하는지 확인한다. 미존재 시 거부 후 재생성한다.
- **모델**:
  - 외부 API 허용 시 Claude Haiku 4.5
  - 온프레 강제 시 Llama 3.3 70B (Ollama / vLLM)

### H. RAG 인프라 *(선택 단계, 사례 30건 이상 누적 후)*

| 항목 | 선택 | 이유 |
|---|---|---|
| 저장소 | pgvector | 기존 PostgreSQL 운영 중. 추가 인프라 없음 |
| 임베딩 | bge-m3 | 다국어 지원, 온프레 가능 |
| 인덱스 | HNSW | pgvector 기본 |

**인덱싱 단위**: 1 케이스 = 워크로드 메타데이터 + 추천 결과 + **사후 outcome**.
사후 outcome 까지 인덱싱해야 "유사 사례 + 그 추천이 성공했는지" 검색이 가능하다.

### I. RAG 활용

LLM 컨텍스트에 유사 사례 N 건을 다음 형식으로 주입한다.

```
유사 사례:
  - case#42: 워크로드=web상시, vCPU 8→4 다운사이즈, 6개월 p95 65%, 안정
  - case#107: 워크로드=야간배치, 메모리 16→8GB, 3개월 후 OOM 1회
```

LLM 출력에 `[case#N]` 인용을 강제한다.

### J. Q&A 인터페이스

자연어 질의에 대한 응답을 LLM + tool use 패턴으로 구현한다.

- LLM 이 직접 SQL 을 작성하지 않는다. 사전 정의된 함수만 호출한다:
  - `get_metric_stats(server_id, metric, window)`
  - `get_recommendation_basis(server_id)`
  - `compare_csp_pricing(spec)`
- Row-level security 미들웨어로 다른 고객사 데이터 접근을 차단한다.

### K. 이상치 / 계절성 탐지 *(선택 단계)*

- STL decomposition (Cleveland 1990) 를 사용한다.
- 결과는 USE Method 의 Saturation 차원에 보강 입력으로 추가한다.
- LLM 이 결과를 자연어로 요약한다 (수치 생성 아님).

### L. 보안 / 규정

| 표준 | 적용 범위 |
|---|---|
| ISO/IEC 27001 | 정보보안경영 — 전사 |
| ISO/IEC 27017 | 클라우드 컨트롤 — Pricing / CSP 연동 |
| ISO/IEC 27018 | 클라우드 PII — 고객사 식별자 보호 |
| CSAP (KISA) | 공공 영업 시 필수 |

**데이터 거주성 정책**
- Engine · Consumer · DB 는 고객사 네트워크 내부에 배치한다.
- 외부 LLM API 호출은 계약 단위 토글로 제어한다 (default off).
- 외부 LLM 사용 시 식별자 (machine_id, hostname, IP) 를 마스킹한다.

---

## 4. 진행 순서

| # | 작업 | 예상 기간 |
|---|---|---|
| 1 | A + B (평가 기준 + USE Method 베이스라인) | 1~2 주 |
| 2 | C (룰 기반 추천) | 1~2 주 |
| 3 | D (Pricing Adapter) | 1 주 |
| 4 | F (템플릿 리포트) | 1 주 |
| 5 | G (LLM 내러티브) | 1~2 주 |
| 6 | E / H / I / J / K | 사례 누적 및 필요 시점에 진입 |

1~5 만으로 출시 가능하다. 6 은 차별화 단계다.

---

## 5. 평가 루프

```
추천 → 적용 → 30 / 90 / 180일 후 outcome 측정 (USE Method 재측정)
    ↓
recommendation_outcome 테이블에 라벨 누적
    ↓
분기별
  - 룰 임계값 재조정 (실측 분포 기반)
  - LLM 프롬프트 회귀 테스트 (eval set 통과율)
  - RAG 인덱스 재빌드 (신규 outcome 반영)
```

FinOps Framework 의 "Optimize over time" 원칙에 따른다. 1 회성 산출물이 아니다.

---

## 6. 주의사항

1. LLM 이 수치를 직접 생성하지 않도록 강제한다. 항상 룰 결과 JSON 만 인용한다.
2. 사례 30 건 미만 시점에 RAG 를 활성화하지 않는다. 검색 품질이 신뢰 가능 수준에 도달하지 못한다.
3. 평가 셋(eval set) 없이 모델 또는 프롬프트를 교체하지 않는다. 개선 여부를 측정할 수 없다.
4. MCP 등 미래 추상화는 실제 외부 통합 요구가 발생한 시점에 도입한다.

---

## 7. 참고 자료

**임계값 / 방법론**
- AWS Compute Optimizer 사용자 가이드
- AWS Well-Architected — Cost Optimization Pillar
- Azure Advisor — Cost recommendations
- GCP Recommender — VM rightsizing

**표준**
- FinOps Framework (finops.org)
- FOCUS Specification 1.x (focus.finops.org)
- ISO/IEC 27001, 27017, 27018
- CSAP (한국 KISA)

**참고 서적 / 논문**
- Brendan Gregg — *Systems Performance* (2nd ed.)
- Google SRE Book / SRE Workbook
- Kleinrock — *Queueing Systems* (1975)
- Cleveland et al. — STL decomposition (1990)
