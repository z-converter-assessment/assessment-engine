# 프론트엔드 표현계층 코드리뷰 — 설명·데이터값·맥락·윈도우 (2026-07-11)

성격: 임시 내부 리뷰 자료 (읽기전용 리뷰 산출물, 삭제 자유). 코드는 일절 수정하지 않았다.

리뷰 범위: 실제 렌더 화면(dev 서버 localhost:8000, 70대 데이터 기준 headless 캡처) + 이를 만드는 프론트엔드 로직(mapper -> template -> chart JS -> query/SQL -> recommendation 임계). 테마/폰트/CSS 미관은 대상 아님.

리뷰 축 (요청 우선순위):
1. 표현 설명(캡션·정의·안내문)의 정확성
2. 표현 데이터값의 정확성
3. 페이지 맥락에서 데이터 적절성
4. 필요한 데이터의 누락 여부
5. 앵커-윈도우-버킷 선택 적절성

검증 방식: 화면 캡처로 현상 확인 -> 계산 코드(mapper/SQL/recommendation)로 실제 동작 확인 -> 각 발견을 독립 에이전트로 적대적 재검증(반례 탐색). 아래는 재검증 통과분만.

---

## 0. 한눈 요약

가장 큰 문제는 하나의 뿌리에서 나온다: "포화(saturation)"라는 단어가 화면마다 두 가지 다른 것을 가리킨다.
- 신호(signal): 실행 큐/코어 >= 임계, await > 임계, 페이징 발생 — 게이트1 원시 값.
- 판정(verdict): 위 신호 AND 이용률(dual-gate). CPU/메모리는 이용률이 낮으면 신호가 있어도 포화 아님 (recommendation.cpu_saturated / mem_saturated 가 명시적으로 False 반환).

지금 working-tree 에 올라온 diff(attention.py 헤더 rename, signal-utils.js marker, detail.html 스냅샷 캡션)는 바로 이 혼동을 걷어내려는 작업이다. 그런데 같은 의미의 표현이 트렌드 차트 캡션, 실시간 현황 도넛, 참고자료/보고서 부록, 지표 정의표에는 여전히 옛 프레임("임계 초과 = 포화")으로 남아 있어, 한 앱 안에서 화면끼리 정면 충돌한다. 대표 증상:
- 실시간 현황: "CPU 포화 50 / 70대" · "디스크 I/O 포화 42 / 70대"
- 환경 개요(같은 좌측 모니터링 그룹): "CPU 포화 0 / 70대" · "디스크 I/O 포화 8 / 70대"
- 두 화면 다 CPU 평균 이용률 5% 환경인데 같은 "포화" 라벨에 50 vs 0.

두 번째 뿌리는 윈도우 문서-코드 drift: 환경 개요 활용률 게이지가 실제로는 14일인데 문서·docstring·CLAUDE.md F10·지표 정의표·서버목록 안내문은 24h 라고 서술한다(ADR 0052 에서 14일로 통일했으나 문서·상수 미정리).

세 번째는 독립적 데이터값 이슈: 저부하 디스크 await 인플레이션(2596ms), 실행 큐 inclusive 임계, CPU headline 에 iowait 포함.

검증으로 "이상 없음"이 확인된 것 (요청 축 중 안심해도 되는 부분):
- 앵커-윈도우-버킷 기계 정합: frontend chart-utils.js AUTO_BUCKET 과 backend types.py AUTO_BUCKET 값 완전 일치. 상세 차트 live 15m 기본, 공유 앵커(seq counter + capture-before-await), 보고서 정적 스냅샷, SSR 추이 버킷 매핑 모두 정합.
- 실시간 포화 지수는 OS 정규화됨 (Linux 1.0 / Windows 2.0 임계를 index 로 나눠 반영) — Windows 과다카운트는 없음.
- 메모리 GiB 를 "GB" 라벨로 쓰는 건 RAM=binary 업계 관례로 의도적 문서화(결함 아님).

심각도 분포: HIGH 5건, MEDIUM 7건, LOW 4건, INFO/무결확인 3건.

---

## 1. "포화" 신호 vs 판정 불일치 (지배 테마) — 축1·축3

### 1-1. [HIGH] 트렌드 차트 캡션들이 "임계 초과 = 포화"로 단정 (진행 중 diff 동시 갱신 누락)

위치: `servers/cpu.html:100`, `servers/storage.html:144`, `servers/metrics.html:64`·`:149`, `servers/environment_metrics.html:62`·`:147`

현상: 같은 페이지 상단 스냅샷 영역은 diff 로 "임계 이상은 신호일 뿐, 포화 판정은 이용률 동반(dual-gate, 자원 평가)"라고 고쳤는데, 그 아래 트렌드 차트 캡션은 여전히 "Linux procs_running 1.0 이상 · Windows 2.0 이상 포화", "20ms 초과면 디스크 포화"라고 단정한다. 한 화면 안에서 두 섹션이 반대로 설명한다.

근거: `recommendation.cpu_saturated()`(recommendation.py:169-197)는 실행 큐가 임계를 넘어도 `cpu_p95_pct < RS_CPU_UNDER_PCT(70)`면 False 반환. env-assessment 캡처에서 해당 호스트들은 실행 큐/코어 L 1.20~1.40(임계 초과)인데도 분류는 "과다 할당"(포화 아님)으로 나온다.

영향: 엔지니어가 상세/성능추이 페이지만 보고 "20ms 넘었으니 디스크 포화, 티어 상향"으로 오판. 실제 분류 파이프라인(서버목록·자원평가·보고서)은 같은 호스트를 저활용으로 판정.

수정방향: 여섯 캡션의 "포화" 단정을 스냅샷 영역과 같은 "임계 이상(신호)" 톤으로 통일하고, 포화 판정은 dual-gate(자원 평가)에서만 난다는 문구 추가. (F9 동시 갱신 대상.)

### 1-2. [HIGH] 실시간 현황 "포화" 도넛이 단일게이트 신호를 dual-gate 와 동일 라벨로 표기

위치: `web/services/mappers/attention.py:406-413`(build_environment_realtime) vs `:315-320`(build_environment_overview) / `web/services/query/environment.py:74-81`(dual-gate) / 라벨 매크로 `_shared.html:50-68` / `servers/_environment_realtime.html`

현상: 실시간 도넛은 `cpu_sat_count = sum(cpu_sat_index >= 1.0)` 등 이용률 게이트 없는 순간 신호만 세면서, 환경 개요의 14일 dual-gate 도넛과 완전히 동일한 라벨("CPU 포화"·"메모리 압박"·"디스크 I/O 포화")을 쓴다. 결과: CPU 포화 50 vs 0, 디스크 I/O 포화 42 vs 8. 두 화면 모두 좌측 "모니터링" 그룹이라 운영자가 오가며 상반된 값을 본다.

근거: `cpu_saturation_index`(recommendation.py:200-210)는 (run_queue/cores)/threshold 뿐 — 이용률 게이트 없음. `cpu_saturated`(dual-gate)와 다른 함수. 저활동/1코어에서 수집기 자신의 R-state 만으로 상시 >= 1/core 튀는 노이즈(코드 주석이 명시)를 그대로 "포화"로 카운트.

영향: CPU 평균 5% 환경에서 "70대 중 50대 CPU 포화"는 명백한 오탐. 지금 diff 가 세우는 "신호 != 포화" 원칙을 정면 위반.

수정방향: 실시간 도넛도 순간 이용률로 dual-gate 적용하거나(스냅샷에 cpu_pct 있음), 라벨을 "실행 큐 임계 초과" 같은 신호명으로 재라벨. 디스크 경계도 await > 20ms(strict)로 통일. "포화" 판정어는 dual-gate 에만 쓰도록 단일화.

### 1-3. [HIGH] 참고자료 용어 정의가 dual-gate 를 반대로 서술 — 모든 발행 보고서 부록에 포함

위치: `reports/_thresholds_reference.html:26`(및 :59·:137 under 트리거 나열), `reports/_reference_footer.html:7-9` 경유 `reports/environment.html`·`servers/single_report.html`·`servers/report.html`

현상: 정의표 Saturation 행이 "발생 시 이용률이 낮아도 증설 신호"라고 명시. 실제 CPU/메모리는 이용률이 낮으면 신호가 있어도 포화 아님(정반대). :59/:137 under 트리거 목록도 "실행 큐 포화 OR ..."로 나열해 이용률 동반 조건을 빼먹는다.

영향: 고객/엔지니어에게 배포되는 정식 보고서 부록이 시스템 핵심 판정 로직을 반대로 설명. 독자가 "포화=이용률 무관 즉시 증설"로 이해하면 본문 표의 실제 분류(저활용이면 포화 아님)와 논리적으로 안 맞아 신뢰도 훼손.

수정방향: CPU/메모리는 "이용률 임계(70%/90%) 동반 시에만 포화(dual-gate)", 디스크 I/O 는 "디바이스 활동 buckets 기반 await 라 낮은 호스트 이용률에서도 발생 가능"으로 축별 분리 서술.

### 1-4. [MEDIUM] 진행 중 diff 의 새 캡션조차 디스크 I/O 절에서는 틀림

위치: `servers/detail.html:309`, `storage.html:63`, `cpu.html:30`, `memory.html:44` (diff 로 추가된 동일 캡션)

현상: diff 가 CPU/메모리/디스크 I/O 세 섹션에 "포화 판정은 이용률 동반(dual-gate)"을 획일 적용했다. 그런데 `recommendation.disk_io_saturated()`(recommendation.py:147-165)는 `await_p95 > RS_DISKIO_AWAIT_MS` 단일 임계로, 이용률 AND 조건이 없다. diff 가 attention.py:65-68 에 추가한 주석조차 dual-gate 예시로 cpu_saturated/mem_saturated 둘만 들고 disk_io 는 뺐다 — 작성자도 알고 있으나 캡션만 3축 획일.

영향: 디스크 I/O 섹션 독자가 "await 임계 넘어도 이용률 동반해야 포화"로 오해 -> 실제 I/O 병목 과소평가. (단 분류 파이프라인은 저-IOPS await 를 util-gate 로 거른다 — 3-1 참조. 표시 계층만 게이트가 없어 방향이 반대.)

수정방향: 캡션 자원별 분기 — 디스크 I/O 절은 dual-gate 표현 제거, "await 단일 임계 판정 + 저활동 device 는 참고" 로.

### 1-5. [MEDIUM] 환경 자원 평가/보고서 표: 헤더 임계 숫자와 셀 강조 기준 불일치, 안내 없음

위치: `web/services/mappers/attention.py:446-449`·`:467-472`, `reports/_resource_tables.html:31`, `servers/_assessment_result.html`, `reports/_env_report_body.html:290-292`

현상: rename 된 헤더는 "실행 큐/코어 (L>=1.0 · W>=2.0)"로 원시 임계를 병기. 그런데 셀 강조색(active, 굵게+빨강)은 `"cpu_saturation" in hit` = dual-gate 트리거 결과다. 저활동 호스트가 실행 큐/코어 1.4(헤더 임계 초과)여도 CPU p95<70% 면 강조 없이 회색 평문. 헤더 숫자와 눈에 보이는 강조가 어긋나는데 이 표에는 설명 캡션이 없다(상세 탭에는 diff 로 추가됨).

영향: 헤더 숫자만 보고 "임계 초과면 강조돼야 한다"고 기대한 운영자가 회색 셀을 "정상"으로 오독.

수정방향: 이 표에도 상세 페이지 취지의 캡션/범례 추가(강조 = dual-gate 판정, 헤더 괄호 = 원시 신호 참고).

### 1-6. [MEDIUM] 지표 정의표가 실시간 6도넛 "포화"를 dual-gate 처럼 서술

위치: `reports/_metric_definitions.html:83`(vs :81 환경개요 행)

현상: 실시간 현황 6도넛 행이 환경개요 14일 행과 똑같이 "CPU 포화·메모리 압박·디스크 I/O 포화 호스트 수/표본"으로 서술하고, 차이를 "순간 vs 14일 창"으로만 밝힌다. single-gate vs dual-gate 라는 정의 차이는 미공개. 화면 라벨이 이상해 참고자료를 펼쳐봐도 같은 오도를 만나 교차검증이 안 된다.

수정방향: 83행에 "실시간 포화 도넛은 순간 단일신호 카운트, 개요/보고서 14일 dual-gate 와 다른 정의" caveat 추가(또는 1-2 재라벨과 동시).

### 1-7. [LOW] CPU 사용률 캡션이 "포화"를 이용률=100% 뜻으로 재사용

위치: `servers/metrics.html:39` ("100% = 전 코어 포화")

현상: 여기 "포화"는 "다 찼다(Utilization 100%)"는 구어적 뜻. 같은 페이지 인접 실행 큐 차트는 "포화"를 USE Saturation 축(대기열)으로 쓴다. 한 페이지 안 동일 단어 두 의미, 용어 해설 링크 없음.

수정방향: CPU 사용률 캡션의 "포화"를 "풀가동"/"전 코어 100%" 등 비-Saturation 표현으로 교체.

### 1-8. [LOW] 네트워크 재전송율 캡션에 저트래픽 억제 게이트 미고지

위치: `servers/network.html:20`·`:99`, `web/services/metrics_calculator.py:137-145`(_ratio_signal) vs `recommendation.py:786-793`(assess_network)

현상: 캡션은 "1% 초과면 네트워크 품질 저하(혼잡·손실)"라 단정. 그런데 assess_network 는 `net_avg_kbytes_per_s < RS_NET_MIN_TRAFFIC_KBPS(10.0)`면 재전송/드롭 초과와 무관하게 congested 를 억제한다(분모 붕괴 방어). 서버 상세 네트워크 경로(_ratio_signal)에는 이 게이트가 배선 안 됨 — val >= threshold 만으로 "임계 이상" 표식. cpu/mem/disk 섹션엔 있는 caveat 이 네트워크엔 없다.

영향: 야간 유휴 호스트에서 소수 재전송만으로 3~5% 튀어 "임계 이상"으로 뜨는데 분류(보고서)는 정상 — 화면마다 다른 결론. (이것도 신호 vs 판정 불일치의 네트워크판.)

수정방향: network.html 캡션에 "트래픽 10KB/s 미만이면 재전송률 신호 억제" caveat 추가, 또는 저트래픽 여부 노출.

---

## 2. 데이터값 정확성 — 축2

### 2-1. [HIGH] 디스크 await 스냅샷·추이 차트가 util-gate 없이 계산돼 저-IOPS 인플레이션 노출

위치: `db/repositories/query/metric.py:263`(스냅샷 await_ms), `metric.py:586-592`(disk.io_saturation 추이) vs `db/repositories/query/report.py:160-166`(분류용, util-gate 적용)

현상: 스냅샷/추이 SQL 은 `t_delta/ops_delta*1000` 을 `ops_delta > 0` 만으로 계산(이용률 게이트 없음). 저-IOPS(캡처 실측 0.5 read/9.2 write)에서 io_time 벽시계 잔류를 극소 ops 로 나눠 await 가 2596.8ms(임계 20ms 의 130배), 추이 차트는 3500ms 까지 치솟는다. 반면 분류용 report_aggregate 는 `delta(io_time)/time_delta >= RS_DISKIO_UTIL_MIN(0.5)` 게이트로 이 버킷을 제외한다(주석: "유휴 device 의 writeback 큐 잔류로 폭증하나 병목 아님").

영향: 같은 호스트가 화면엔 "응답 지연 2596.8ms 임계 이상", 분류엔 io_ok/과다할당 — 자기모순. 운영자가 실재하지 않는 스토리지 병목으로 오판. 분류 코드가 의도적으로 제거하는 바로 그 아티팩트를 모니터링 화면이 그대로 보여준다.

수정방향: 스냅샷·추이 await 계산에도 report_aggregate 와 동일한 device 사용률 게이트를 적용하거나, 게이트 없는 값임을 캡션에 명시하고 저활동 device 표시 억제.

### 2-2. [MEDIUM] 실행 큐 "임계 이상" 배지가 inclusive >= 1.0 이라 1코어 정상부하를 상시 표식

위치: `web/services/metrics_calculator.py:159-168`, `recommendation.py:55`(PROCS_RUNNING_PER_CORE_SATURATION=1.0)

현상: rq_idx = (procs_running/cores)/1.0, `saturated=(rq_idx >= 1.0)`. 1코어에서 procs_running=1(현재 실행 중 1개, 대기열 0)이면 정확히 1.0 -> True. procs_running 은 실행 중 태스크(수집기 자신 포함)를 세므로 뭔가 돌기만 하면 발화. 코드 주석 자신이 "저활동 시스템(특히 1코어)에서 상시 >= 1/core 노이즈"라 명시.

영향: 대기 없는 정상 부하(runnable 1 = 실행 중 1, 큐 0)에 오렌지 "임계 이상" 배지. 전통적 포화는 run queue > 코어수(대기 발생). 상세 페이지는 캡션으로 완화하나 실시간 aggregate(1-2)가 이 상시-온 신호를 "포화"로 증폭. 배지 정보가치 거의 0.

수정방향: 대기(runnable 초과) 관점으로 재정의(예: > 1.0 exclusive 또는 실행중 보정), 최소한 1코어/저활동 상시발화를 배지에서 억제(dual-gate 연동).

### 2-3. [MEDIUM] CPU 사용률 headline 이 I/O Wait 를 포함 — 실 컴퓨트 수요 과대표시

위치: `web/services/metrics_calculator.py:320-322`, `db/repositories/query/report.py:52-53`(cpu_p95 동일 정의)

현상: `usage_pct = 100 - idle_pct` — idle 만 제외해 iowait 가 usage 에 포함. 캡처 실측 detail-cpu: CPU 사용률 17.3%인데 User 0.0% / System 1.6% / I/O Wait 13.8%. 즉 실제 compute(user+system) ~1.6%인데 headline 17.3%.

영향: iowait 는 CPU 가 IO 완료를 기다리는 유휴지 연산 부하 아님. headline 이 IO 대기로 부풀려짐. cpu_p95(분류 입력)도 동일이라 IO바운드 저컴퓨트 호스트의 CPU 이용률이 과대 -> 다운사이즈 억제 방향 편향 여지(현 데이터셋에선 오분류로 이어지진 않음). 우측에 iowait 를 따로 보여주지만 usage 에 이미 합산돼 이중 계상 인상.

수정방향: headline/cpu_p95 를 iowait 제외(compute busy = user+system+irq+softirq+steal)로 산출할지 검토하거나, "CPU 사용률(iowait 포함)" 정의를 캡션 명시. right-sizing 입력에 iowait 포함이 의도인지 `docs/reference/right-sizing.md` 대조 필요.

### 2-4. [MEDIUM] 환경 자원 평가 "전체보기" 카운트가 인쇄용 중복 tbody 까지 세어 3배 부풀림

위치: `web/static/js/pages/assessment.js:31`, `reports/_resource_tables.html:12-75`, `servers/_assessment_result.html:19-27`

현상: `wrap.querySelectorAll('tbody tr')` 가 매크로 action_targets_table 이 렌더한 화면표(no-print) + 인쇄용 2분할표(print-only) 3개 tbody 를 전부 센다. #under-wrap 을 감싸는 section 이 no-print 라 인쇄표는 이 페이지에선 죽은 DOM 인데도 카운트에 잡힌다. 70대 -> "전체보기 (20/210)". 주석 "서버당 1 표 행"이 실제와 어긋남.

영향: (1) 카운트 라벨 3배 오류(210 표기, 실제 70). (2) 7~20대 환경에서 3N>20 이라 clip 오발동 — 화면 실제행은 다 보이는데 무의미한 "더보기" + 틀린 카운트. 실제로 보이는 표 데이터 자체는 정확(화면 tbody 가 DOM 앞쪽). 환경 보고서는 assessment.js 미로드라 영향 없음.

수정방향: 셀렉터를 화면표 하나로 스코프(예: no-print 표에 고유 id 부여, 또는 `.table-scroll.no-print tbody tr`).

### 2-5. [INFO] 메모리 GiB 를 "GB" 라벨로 표기 — 의도적 관례, 결함 아님

위치: `web/services/unit_converter.py`, `attention.py:327-328`

현상: 환경 요약 "메모리 151.6 GB"는 GiB(2^30) 값, "디스크 2.7 TB"는 decimal(10^9). 같은 카드 행에 이진/십진 혼용. 단 unit_converter.py 가 "RAM=binary 관례 / 디스크=decimal 산업표준"을 3곳에 명시 문서화한 의도적 설계.

판정: 결함 아님(업계 통용). 정밀 대조 시 혼동 소지가 걱정되면 메모리 라벨 "GiB" 또는 툴팁 진법 병기 정도.

---

## 3. 페이지 맥락 적합성 — 축3

### 3-1. 실시간 도넛에 윈도우/정의 캡션 부재 (1-2 의 표시측 원인)

위치: `servers/_environment_realtime.html:8-10`, `_shared.html:52-68`(saturation_donut 매크로)

현상: 실시간 카드 헤더 메타는 "온라인 N · 오프라인 N · 온라인만 유효 표본"뿐 — 포화 정의·윈도우 표기 없음. 반면 개요 카드는 window_meta("70대 기준 · 최근 14일")를 붙인다. 같은 도넛 컴포넌트가 한 화면엔 캡션과 함께, 실시간엔 캡션 없이 쓰인다. [MEDIUM]

영향: 운영자가 "CPU 이용률 5.0%"와 "CPU 포화 50/70"을 나란히 보면서 이게 순간 스냅샷·단일신호라는 맥락을 알 길이 없다. 1-2 의 값 불일치를 캡션으로도 완화 못 함.

수정방향: 실시간 포화 카드에 정의·기준 캡션 추가(예: "최신 스냅샷 · 신호 임계 초과 호스트/표본") 또는 참고자료 링크. 1-2 를 재라벨로 풀면 캡션도 그 의미로.

### 3-2. [무결 확인] 실시간 포화 지수는 OS 정규화되어 OS-aware 가 맞음

위치: `recommendation.py:200-223`, `web/services/query/environment.py:307-313`

내용: 리뷰 착수 시 "realtime 이 Windows(임계 2.0)에도 1.0 을 적용해 과다카운트하는가"를 의심했으나, `cpu_saturation_index` 가 os_family 로 threshold(Linux 1.0/Windows 2.0)를 나눠 index 로 정규화하므로 index>=1.0 은 OS별 임계와 동치. 과다카운트 없음. (단 1-2 의 단일게이트 라벨 문제는 OS 무관하게 유효.)

---

## 4. 누락/맥락 데이터 — 축4

### 4-1. [MEDIUM] 실시간 현황에 네트워크 축이 통째로 부재

위치: `web/view_models/attention.py:258-274`(EnvironmentRealtime), `mappers/attention.py:348-423`(build_environment_realtime), `servers/_environment_realtime.html`

현상: 실시간 "현재 자원 현황"은 이용률 3(CPU/메모리/디스크) + 포화 3(CPU/메모리/디스크 I/O) 6도넛뿐, "부하 상위"도 3칼럼뿐. 환경 개요에는 항상 있는 "네트워크 혼잡" 도넛(7번째)이 실시간엔 없다. EnvironmentRealtime 데이터클래스에 network 필드 자체가 없다. latest_saturation SQL 은 이미 retrans/drop/conntrack 을 조회하지만 _assemble_realtime 이 참조하지 않아 전달 안 됨.

영향: "지금 이 순간" 함대 상태를 보는 페이지인데 네트워크 혼잡이 실시간 발생해도 어떤 신호도 안 뜬다. 운영자가 "현재 이상 없음"으로 오판. E9(발화 가능 정보 노출) 위반 — 카테고리 자체가 없어 네트워크 신호가 있을 수 있다는 사실조차 모른다.

수정방향: 실시간 스냅샷의 net_retrans/drop 으로 네트워크 이용·혼잡 도넛(+ 필요시 부하 상위 네트워크 열) 추가, 최소한 "네트워크" 카테고리를 회색 빈 슬롯으로라도 노출.

### 4-2. [LOW] 구커널 PSI 를 "수집 대기"(no_data)로 표기 — Windows 처럼 "미지원 N/A" 분기 없음

위치: `web/services/metrics_calculator.py:124-134`(_psi_signal), `web/view_models/metric.py:14-31`

현상: PSI 는 Linux 4.20+ 만 발행. centos7(kernel 3.10) 등 EL6/7·SLES11-12·Debian10 은 구조적으로 영원히 PSI 를 못 낸다. 그런데 _psi_signal 은 `if win: N/A / elif psi_val is None: no_data` 로만 분기하고 커널 버전을 안 본다. 결과: 구커널 PSI 가 "수집 대기"(곧 채워질 인상)로 영구 고정. Windows 는 "미지원 N/A"로 명확한데 구커널 Linux 는 아님.

영향: PSI 는 표시 전용(판정 미사용)이라 분류 영향 0. 다만 "수집 대기"가 에이전트/파이프라인 문제로 오인시키거나 다음 새로고침을 기대하게 만듦.

수정방향: _psi_signal 에 kernel_version(또는 psi_supported 플래그)을 받아 4.20 미만이면 Windows 와 동일하게 state=not_applicable("구커널 미지원").

---

## 5. 앵커-윈도우-버킷 — 축5

### 5-1. [무결 확인] 앵커/버킷 기계 정합은 전부 정상

- AUTO_BUCKET 동기화: `chart-utils.js:11` {15m:1m, 1h:5m, 6h:15m, 24h:30m, 7d:3h, 14d:6h, 30d:12h} == `db/repositories/query/types.py` AUTO_BUCKET 완전 일치.
- 상세 차트 기본 range 15m(live): cpu/storage/memory/network.js pageTimeControl(...,'15m',...), metrics/environment-metrics.js globalRange='15m' — F10 정합.
- 공유 앵커: pageTimeControl 이 한 range+한 anchor 로 페이지 전 차트 구동, 각 로더 seq counter + capture-before-await 로 stale 폐기, anchor 비면 live/지정 시 과거.
- 보고서 정적 스냅샷: job -> 저장 스냅샷 렌더(재계산 0). SSR 부하추이 bucket = 발행 range 의 AUTO_BUCKET(report.py 와 env-trend.js 일치).
- 미세 caveat(무해): env-trend.js range 미상 fallback '6h' vs backend '1h' 불일치 — time_range 가 검증된 Literal 이라 실제 도달 불가(dead path).

즉 앵커/윈도우/버킷의 "기계적 선택"은 문제없다. 문제는 아래 "윈도우가 무엇인지에 대한 설명"이 실제와 어긋난 것.

### 5-2. [MEDIUM] 지표 정의표가 "환경 개요·자원평가 모니터링 카드 = 최근 24시간"이라 안내하나 실제 14일

위치: `reports/_metric_definitions.html:21`

현상: 사용자·보고서에 노출되는 지표 용어집이 "모니터링 현황 카드(환경 개요·환경 자원 평가)는 최근 24시간"이라 서술. 실제: 환경 개요 util = WINDOW_DAYS(14일, environment.py:342), 환경 자원 평가 기본 = DIAGNOSTIC_DEFAULT_TIME_RANGE=14d. 같은 파일 :81·:87 은 "14일"이라 서술해 문서 자체로 모순.

영향: 운영자가 활용률 도넛을 "최근 24시간 스냅샷"으로 해석 — 실제 14일 평균과 시간 규모가 6~14배 달라 판단 왜곡(최근 급증을 24h 값으로 오독). 사용자 대면 설명이라 축1·축3 직접 영향.

수정방향: 문구를 "환경 개요·자원평가 = 14일 표준 창(앵커/구간 override 가능)"로 정정. "현재 스냅샷"은 실시간 현황 카드에만 해당함을 명확히.

### 5-3. [MEDIUM] 서버 목록 안내문이 "환경 자원 평가는 최근 24시간"이라 명시하나 실제 14일

위치: `servers/list_table.html:110`(및 주석 :108)

현상: "자원 적정성 분류는 최근 14일 표준 윈도우 기준 (환경 자원 평가는 최근 24시간)." 실제 자원평가 라우터 기본값도 14d. 오인 방지용 라벨이 오히려 틀린 값을 주장.

영향: 목록 분류(14일)와 자원평가(실제 14일)가 같은 창인데 다르다고 안내 -> 사용자가 두 화면 분류 차이를 잘못된 이유(윈도우 차이)로 귀속. (실제 24h 를 쓰는 건 환경 개요 활용률 게이지가 아니라... 그것도 실제론 14일 — 5-4 참조.)

수정방향: 괄호 문구를 "자원 평가도 14일 표준(구간 선택 가능)"으로 정정.

### 5-4. [LOW, 문서 drift] 환경 개요 활용률 게이지 24h vs 14일 — 문서/상수/CLAUDE.md F10 이 코드와 어긋남

위치: `web/services/query/environment.py:326-346`(docstring vs 코드), `:33-34`(DASHBOARD_TIME_RANGE/DASHBOARD_WINDOW_DAYS), `_shared.html:9`(window_meta 주석), `docs/explanation/products/dashboard.md:40-46`, `.claude/CLAUDE.md` F10

현상: get_dashboard_overview 실제 코드는 util = environment_utilization(period_days=WINDOW_DAYS=14). 화면 라벨도 "최근 14일"이라 화면 자체는 정합. 그러나 같은 함수 docstring, CLAUDE.md F10("모니터링 활용률 게이지 = 24h(DASHBOARD_TIME_RANGE)"), dashboard.md, window_meta 매크로 주석은 24h 라 서술. DASHBOARD_TIME_RANGE/DASHBOARD_WINDOW_DAYS 상수는 정의·export 되나 활용률 계산에 미사용(고아 상수). ADR 0052 에서 14일로 통일하며 문서·상수 미정리.

영향: 화면 오표시는 없으나(라벨 14일 == 계산 14일), 단일 진실로 지정한 F10·dashboard.md·docstring·매크로 주석이 실제 구현과 어긋나 향후 유지보수자가 "24h 게이지"를 근거로 오판·회귀할 위험. F12(현황 선언성)·F9 위반.

수정방향: 14일 통일이 확정이면 F10·dashboard.md·services.md·docstring·window_meta 주석을 14일로 일괄 정정 + DASHBOARD_TIME_RANGE/DASHBOARD_WINDOW_DAYS 고아 상수 제거. (24h 분리가 의도면 반대로 코드를 되돌림.) 어느 쪽이든 코드/문서/라벨 3자 단일화.

주: 5-2·5-3 은 이 drift 의 사용자 대면 발현(지표 정의표·목록 안내문이 24h 라 잘못 안내), 5-4 는 문서/상수 계층. 근본은 하나 — ADR 0052 후속 문서 정리 누락.

---

## 6. 우선순위 제안

즉시(사용자 오판 직결):
- 1-2 실시간 "포화" 도넛 재라벨/재판정 (50 vs 0 오경보).
- 1-3 참고자료 Saturation 정의 정정 (발행 보고서에 반대 설명 실림).
- 2-1 디스크 await util-gate (실재하지 않는 병목 표시).
- 1-1 트렌드 차트 캡션 6곳 "포화" -> "신호" (diff 동시 갱신 누락 마무리).

그 다음(설명·표시 정합):
- 1-4/1-5/1-6/1-7/1-8 포화 용어 잔여 정리, 5-2/5-3 윈도우 안내문 정정, 2-3 CPU headline iowait 정의 명시, 4-1 실시간 네트워크 축.

정리성(문서/코드 위생):
- 2-2 실행 큐 임계, 2-4 clip 카운트, 4-2 구커널 PSI, 5-4 24h/14일 문서·상수 drift.

한 줄 총평: 개별 위젯의 수치·버킷·앵커 기계는 견고하다(5-1·3-2·2-5 로 확인). 문제는 거의 전부 "그 수치가 무슨 뜻인지"를 설명하는 계층 — 특히 "포화"라는 단어가 신호와 판정 두 의미로 화면마다 갈리는 것 — 에 몰려 있다. 진행 중 diff 가 그 정리의 시작이니, 같은 기준을 트렌드 캡션·실시간 도넛·참고문서·보고서 부록까지 확장하면 대부분 해소된다.
