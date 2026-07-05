# 자원 적정성 분류 — 연역 원칙 (작업 초안)

> 목적: 분류·근거·권고·신뢰도를 각각 따로 튜닝하지 않고, 전제 두 개(근거 범위 / 여유 기준)에서 전부 유도한다.
> 모든 임계와 분기는 두 전제 중 하나로 추적돼야 하고, 추적되지 않는 규칙(뿌리 없는 값)은 결함으로 본다.
> 이 문서는 협의용 임시 초안이다 (docs/temp). 확정되면 ADR + right-sizing.md 로 옮긴다.

## 산출 4축 (전부 근거에서 파생, 직교 독립 아님)

- 분류: 지금 어떤 상태인가 (부족/과다/정상/유휴/종료 권장/표본 부족)
- 근거: 왜 그 상태인가 (자원별 측정 신호·값)
- 권고: 어떤 상태로 옮기라 (현재 -> 목표. 예: 2코어 -> 4코어)
- 신뢰도: 위 판단을 얼마나 믿나 (못 본 축·얇은 표본)

의존 구조 — 근거가 뿌리, 나머지 셋이 파생:
```
근거(자원별 측정)
 ├─ 규칙으로 라벨       -> 분류
 ├─ 빠진 것/얇은 것     -> 신뢰도
 └─ + 여유 기준         -> 권고
```
그래서 뿌리 두 개(근거 범위 / 여유 기준)만 고정하면 넷이 서로 모순 없이 따라 나온다.

이 4축은 호스트 하나가 아니라 자원 5개(CPU · 메모리 · 디스크 용량 · 디스크 I/O · 네트워크) 각각에 적용된다. 자원마다 근거 -> (분류 / 신뢰도 / 권고)를 갖고, 분류·권고의 종류는 자원마다 다르다: CPU·메모리는 under/optimal/over + 코어·RAM 증설, 디스크 용량은 남은 기간 + 스토리지 추가, 디스크 I/O·네트워크는 병목·오류 + 티어 검토. 그 위에 호스트 요약(종합)이 얹히지만 정렬·분류용 파생일 뿐 근본이 아니다 — 조치는 자원별 판정에서 나온다. 언더/오버는 CPU·메모리 전용 어휘고, 나머지 자원은 각자 어휘를 갖는다.

---

## 고려 축 지도 — 완전성 체크리스트

하나씩 빠진 걸 뒤늦게 찾지 않으려고, 완전하려면 봐야 할 축을 위에서 한 번에 편다. 상태: [정함] / [부분] / [빠짐].

자원마다 (CPU · 메모리 · 디스크 I/O):
1. 이용률 — 바쁜가 (선행 신호, 100%에서 막힘). [CPU 정함]
2. 포화 — 얼마나 밀렸나 (크기 신호, 무한대로 열림). 분류는 이진(넘었나), 사이징은 배수(얼마나). [CPU 사이징 반영(목표배수 0.7); 메모리·디스크는 분류·표시]
3. 오류 — 실패하나 (드롭·OOM·디스크 에러·재전송). USE 세 번째. [정함 — 네트워크 품질(드롭·재전송) 전용, "오류축 현실" 절]

평가 전체:
4. 시간·추세 — 성장/주기. 신호가 변동이냐 누적이냐로 갈림. 추세 추정은 누적 신호는 적은 데이터로 신뢰, 변동 신호는 노이즈를 이기려 오랜 이력 필요. 전제=약 1개월 수집: CPU·메모리는 변동이라 추세가 노이즈에 묻혀 무의미 -> 미채택(여유가 성장 완충). 디스크 용량은 누적이라 1개월로도 채워지는 속도 유의미 -> 추세가 곧 모델. [CPU·메모리 정적 확정 / 디스크 추세 필수]
5. 변동성 — 꾸준한가 튀는가. 통계적 정밀도(신뢰도 1)로 흡수 — 분산 크면 p95 추정이 흔들려 신뢰도 하향. [정함 — 신뢰도 축]
6. 자원 간 결합·근본원인 — 메모리 부족 -> 스왑 -> 디스크·CPU 상승. 독립 사이징은 증상에 오진. 종합은 "가장 나쁜 자원 승"이 아니라 "원인 자원 짚기"(procs_blocked·swap·await 판별). [정함 — 종합·근본원인 규칙]

권고 산출:
7. 스케일링 모델 — 크기 -> 완화 관계, 자원별 상이(CPU 선형/암달, 메모리 실사용량, 디스크 추세외삽). [CPU만]
8. 이산성 + 플랫폼 카탈로그(가능 크기). [정함]
9. 신뢰도 — 4종 분해: 통계 정밀도(표본·분산) / 커버리지(측정 공백) / 충실도(계통 편향) / 정상성(추세). [정함]

우선순위:
- (해결됨) 2 포화 사이징(CPU 확정) · 6 근본원인(확정) · 3 오류(네트워크 품질로 확정).
- 정하되 경계: 4 추세(최소 "정적 관측(한 시점)" 한계 명시). (5 변동성은 신뢰도 축으로 확정.)
- 한계 문서화 후 미룸: 코어 성능 이질성(세대·vCPU) · OS 상시 오버헤드 · 윈도우 대표성.

PSI 결정 (가로): PSI(cpu/memory/io)는 커널 4.20+ 에 CONFIG_PSI(일부 배포판은 psi=1 부팅 옵션)까지 필요해 fleet 다수(최소 커널 2.6.32)가 갖지 못한다. "있으면 포화 주신호로 우선"으로 두면 같은 fleet에서 호스트마다 분류 주신호가 갈려 정합성이 깨진다. 게다가 구세대에서도 되는 대체 신호(procs_running/schedstat · pswpin·pswpout · avgqu/await)를 어차피 구세대용으로 갖춰야 한다. 결정: PSI는 분류에서 빼고, 넣더라도 수집만(관측·검증용) — 분류는 단일 대체 경로로 통일.

오류축 현실 (가로): USE 세 번째(오류)는 채택해도 대부분 게스트에서 비어 있다 — oom_kill(4.13+라 구세대엔 없음)·디스크 오류(SMART 게스트 blind)·용량 ENOSPC(중복 미채택)·rx/tx errors(virtio 0). 실질 채워지는 건 네트워크 드롭·TCP 재전송뿐. "USE 3분의 1을 채운다"가 아니라 "오류축은 네트워크 품질 신호 전용"으로 기대치 조정.

범위 밖 (가로): PID/fd/conntrack 고갈, NUMA·hugepage는 건강 신호이거나(사이징 아님) 소형 가상 게스트에서 무의미(하이퍼바이저 평탄화)라 미채택 — 명시적 범위 밖. (단 소켓/conntrack은 네트워크 품질 신호로 검토 — 네트워크 모델 참조.)

## 실측 검증 (fleet 69대)

모델 가정을 실제 fleet 데이터로 확인 — 세 가지가 굳어졌다:
- swapless가 다수: Linux 58대 중 32대(55%)가 스왑 없음(Windows 11대는 전부 pagefile). swapless는 보정 케이스가 아니라 Linux 기본이다 -> 메모리 주 경로를 이용률+OOM+PSI로 재배치. 게다가 구세대(3.10 CentOS7 등)는 oom_kill(4.13+)·PSI(4.20+)도 없어 이용률(MemAvailable<3.14면 근사)만 남는 저신뢰 조합이 실재.
- 커널 분포: <3.14(MemAvailable 부재) 9대(16%), PSI 가능(대략 5.x/6.x) 절반쯤. "PSI 있으면 우선"이면 fleet 절반이 다른 주신호로 갈림 — 실측 확인 -> PSI 분류 제외 결정 정당. 최소 커널 2.6.32 실호스트(el6) 1대 존재 -> 구세대 대응 설계 필요 확인.
- Windows 디스크 IOCTL 절반 실패: Windows 11대 중 disk_queue 채워진 건 6대(5대 diskperf 미부착, 45%). cpu_queue·paging은 11대 전부 OK. 새 await도 같은 IOCTL이라 그 5대에선 미측정 -> Windows 디스크 I/O 포화 약 절반 미관측(QueueDepth 대체도 그 5대는 불가 -> "포화 미관측" 신뢰도).

---

## 전제 1 — 근거 범위 (무엇을 사실로 받아들이나)

근거를 "그 문서가 무엇을 주장하느냐"로 계층화한다. 벤더 정체성이 아니라, 시스템의 상태를 정의하는 문서(사실로 채택)냐 조치를 결정하는 문서(우리가 여유 기준으로 다시 정함)냐로 가른다.

| 계층 | 성격 | 예 | 우리 태도 |
|------|------|-----|-----------|
| 1 | 방법론 (무엇을 재나) | USE Method — 이용률/포화/오류 축 정의 | 뼈대로 채택 (숫자 아님) |
| 2a | 시스템·수학 법칙 (벤더 무관) | 큐잉이론 — 이용률이 한계에 다가가면 응답시간 급증 | 사실로 채택 |
| 2b | 대상 OS 저작자의 자기 정의 | 윈도우=Microsoft perfmon, 리눅스=커널/proc | 사실로 채택 (그 OS 상태의 유일 권위) |
| 3 | 조치 결정 (언제 조정하나) | 클라우드 advisor(AWS/Azure/GCP) 임계 | 시작 참고값. 여유 기준으로 다시 정함 |
| 4 | 무출처 경험값 | mem 80% / disk 85% / paging 1000 | 2·3으로 승격하거나 "잠정 + 실측 대상"으로 격리 |

규칙: 모든 임계는 (출처, 계층)을 선언한다. 계층이 곧 그 임계의 신뢰 수준.

지금 이 계층으로 보면 드러나는 것:
- USE 방법론(계층1)을 뼈대로 받으면 오류(Errors) 축 부재가 "빠진 3분의 1"로 확정된다 — 에이전트가 net rx/tx_errors 를 이미 보내는데 분류에 미사용.
- 옛 계층4 세 값 해소: mem 80 -> 90%(tier 3 Azure) · disk 85 -> tier 3(monitoring 표준) · paging 1000 -> 폐기(고정 임계가 디스크·워크로드 의존이라 못 믿음 — 이용률로 대체). tier-4 뜬 값 0.
- 클라우드 advisor 임계(idle/shutdown/over)는 정당화가 아니라 계층3 시작값으로 재분류.

[확정] 이 계층 틀 채택 — 5자원·축6·신뢰도의 모든 근거 표가 (계층, 출처)로 이 틀을 써 자기검증됨. tier-4 뜬 값 0(전부 tier 2/3 앵커 또는 명시 폐기).

---

## 전제 2 — 여유 기준 (얼마나 여유를 둘 것인가)  [방향 확정, 값 미정]

정의: 같은 측정 부하에서 "괜찮다"와 "더 필요하다"를 가르는 경계를, 부족을 놓치는 위험(장애·성능)과 과다를 놓치는 비용(돈 낭비) 사이에서 어디에 둘지.

확정된 방향 — 손실 비대칭:
- 잘못된 다운사이즈(자원을 낮춰 재프로비저닝한 뒤 장애)가 최악의 결과다. 반대로 놓친 절감은 회수 가능하다(나중에 줄이면 됨).
- 그래서 비용 절감은 목표이되 안전이 하한선(제약)이다. "안전 하한선 위에서 절감 최대화" — 안전과 비용을 반반 저울질하는 게 아니다.
- 증설은 틀려도 안전한 방향이고, 이 도구의 베스트 산출이다.

이 방향에서 유도되는 것:
- 부족(under) 판정은 적극적으로 — 압박 신호 하나라도(OR), 최우선. 놓치면 위험.
- 과다(over) 판정은 보수적으로 — 전 축이 낮을 때만(AND). 섣불리 주장 안 함.
- 권고 세기가 비대칭이다 (아래 유도 절):
  - 부족 -> 자신 있게 처방(N코어로 증설). 틀려도 결과는 증설이라 안전.
  - 과다 -> 신뢰도로 가른다. 높으면(이력 길고 안정적·상승 추세 아님·신호 뚜렷) 다운사이즈 권고(목표 70%, 증설과 동일 기준). 낮으면(이력 짧음·계절 피크 놓쳤을 위험·못 보는 신호·버스티) 관찰만 — 여유는 노이즈만 먹지, 못 본 피크·성장은 못 막으니.

[값 — 5자원 전부 확정 (각 모델 절 참조)]
- 부족 상한선 70% 유지 (계층2 큐잉 무릎).
- CPU 증설·다운사이즈 목표: 70%(P95, AWS Balanced). 아래 CPU 모델 절.
- 과다 관찰 문턱. 액션 경계가 아니라 관찰 신호라, 보수적으로 느슨하게 둬도 리스크가 없다.

---

## 유도 — 세 산출이 두 전제에서 어떻게 나오나

- 근거: 전제1의 상태 근거(계층2)로 자원별 이용률·포화·(오류) 측정. OS별 신호는 통일 축으로 정규화.
- 분류: 근거 + 전제2(여유 기준)의 경계로 자원별 판정 -> 호스트 요약으로 종합. 종합 규칙(축6)은 아래 "종합·근본원인 규칙" 절에 확정 — 인과 판별(procs_blocked·swap·await)로 root 자원을 짚어 그에만 처방한다. 메모리 우선 귀속은 그 특수 케이스다. 메모리 부족 -> 스왑 -> iowait·디스크 지연·CPU 상승의 연쇄라, 독립 처방하면 RAM 하나 문제에 SSD+코어+RAM을 셋 다 권고해 절감 도구가 과다 처방하는 자기모순이 난다.
- 권고: 세기가 비대칭이다.
  - 부족 -> 처방. 전제2 목표 이용률 + 스케일링 가정(선형 + 암달 한계, 명시) + 플랫폼 카탈로그(가능 크기) + 안전 올림으로 목표 크기 계산.
  - 과다 -> 신뢰도 높으면 다운사이즈 권고(목표 70%, 증설과 동일), 낮으면 관찰만.
  처방하는 방향(증설)이 틀려도 안전하다는 손실 비대칭의 직접 귀결.
- 신뢰도: 4종 불확실성(통계 정밀도·커버리지·충실도·정상성). 분류와 별개의 독립 출력.

---

## CPU 확정 모델 (잠금)

### 신호 (양 OS)
- 이용률(집계): cpu% p95·피크. Linux /proc/stat cpu 라인, Windows GetSystemTimes.
- 이용률(코어별): per-core p95 max. Linux /proc/stat cpu0..N(신규), Windows per-processor. 단일 스레드가 1코어를 100% 물면 집계는 낮게 보여도 이게 잡아 "과다·유휴" 오탐을 억제(구세대 단일스레드 앱 대비). 어느 코어든 p95 >= 85%면 다운사이즈/유휴 판정 보류.
- 포화(주): 실행 큐/코어. Linux는 schedstat(실행 대기 누적, 표본 사이 전부 적분)를 우선으로 — 있으면(CONFIG_SCHEDSTATS). 없으면 procs_running(/proc/stat, R상태만, loadavg IO 오염 없음)로 대체하되 순간값이라 표본 사이 큐 스파이크를 놓친다는 한계 명시(창 p95는 뜬 표본의 분위일 뿐 놓친 포화를 복원 못 함). Windows Processor Queue Length.
- 포화 귀속: Linux procs_blocked(D상태=IO 블록). 높으면 부하가 IO발이라 CPU 아님(근본원인 분리).
- 오류/왜곡: steal(Linux /proc/stat). 가상화 하이퍼바이저 경합 — 사이징을 주도하는 신호 아님(vCPU 늘려도 초과할당 안 풀림). 높으면 이용률·포화가 왜곡됐다는 신뢰도 단서.

### 분류 (under 판정)
이용률 p95 >= 70% OR 포화(실행큐/코어 >= 임계). 하나라도 발화.

### 사이징 (증설 = 두 제약의 큰 쪽)
- 이용률 제약: P95 이용률이 70% 이하가 되는 코어수 = ceil(부하/70), 부하 = p95 이용률 x 코어. 증설·다운사이즈 공통(AWS Balanced, 비대칭 없음 — 근거 그대로 따름). 다운사이즈는 신뢰도 높을 때만.
- 포화 제약: p95(procs_running)/코어를 목표배수 0.7 아래로 내릴 코어수.
- 증설 = max(이용률 제약, 포화 제약). 이용률은 100%에서 막혀 크기를 못 주고, 포화가 초과 수요를 쥔다.
- 근거: 목표 70%(P95) = AWS Balanced(tier 3) + 큐잉 무릎(tier 2). 선형 예측이 낙관적이라 실제가 예측보다 높게 앉을 위험은 포화(run queue) 제약이 max로 흡수(maxed 호스트의 진짜 수요)하고 신뢰도가 보완 — run-queue·per-core·신뢰도가 AWS 대비 우리 추가 안전장치.
- 이산성: 코어가 정수라 목표에 정확히 안 앉음 — ceil로 올려 안전한(더 낮은 이용률) 쪽.
- 한계: 목표 70%(P95, Balanced)는 우리 옛 커스텀(~50%)보다 여유가 작다. 선형 낙관·못 본 피크 위험은 포화·신뢰도로 방어, 남는 위험은 신뢰도로 노출.
- 출력: 착지 지점 + steal 높으면 신뢰도 단서.

### 통계
이용률·포화 모두 p95(지속 적체). max(순간 스파이크)로 과증설 안 함.

### 오류 축 (CPU)
CPU 고유 "실패" 신호는 없다(패킷 드롭 같은 개념 부재). steal은 사이징이 아니라 신뢰도로 흡수 — CPU에서 errors 축은 별도 미채택.

### 남은 값 (여유 기준 묶음, 나중 일괄)
- 확정: 부족 상한선 70%(P95) · 증설·다운사이즈 목표 70%(AWS Balanced, 비대칭 없음) · 포화 목표배수 0.7 · per-core 보류 임계 85%.

### 근거 (계층)
| 판단 | 계층 | 출처 |
|------|------|------|
| 포화 = 실행큐 > 코어 (procs_running) | 1 | USE Method (Gregg) — vmstat "r" > CPU count |
| 이용률 부족 70% (P95) | 2 + 3 | 큐잉이론(Kleinrock) 응답시간 무릎 + AWS Compute Optimizer Balanced(<70%, P95) 일치 |
| steal = 하이퍼바이저 경합 (신뢰도) | 2b | steal time 커널/가상화 정의 |
| 신호 가용 (procs_running 2.5.45+ / steal 2.6.11+) | proc(5) | 확인됨 |
| 증설·다운사이즈 목표 70%(P95) | 3 | AWS Compute Optimizer Balanced |
| 포화배수 0.7 · per-core 85% | 여유 기준 | 우리 결정 |

---

## 메모리 확정 모델 (잠금)

CPU와 다른 점: 절벽(OOM)이라 여유 크게·통계는 피크 / 실사용 메모리라 RAM 늘리면 여유 공간 / 선형 정확(암달 없음). 주신호는 두 모드 — 스왑 있으면 페이징, swapless(Linux 다수)면 이용률.

### 신호 (양 OS)
- 이용률(실사용 메모리): (total - available)/total. Linux MemAvailable(있음). 단 MemAvailable는 커널 3.14+라 그 아래 구세대는 zoneinfo 근사 -> 주 신호가 열화하니 신뢰도 하향. Windows GlobalMemoryStatusEx(있음).
- 포화(스왑 있는 호스트 한정): 스왑 발생 정도 = vmstat pswpin+pswpout(신규). 단 Linux 다수는 swapless라 이 신호가 없다(아래 swapless 절). pgmajfault는 뺀다 — 파일 mmap major fault를 섞어 대용량 파일 호스트(DB 등)를 메모리 압박으로 오판. Windows는 항상 pagefile이라 페이징 rate가 포화 신호이나, 고정 임계(Pages/sec 1000)는 디스크 대역·워크로드 의존이라 못 믿는다(MS: 단순 숫자 없음) — pgmajfault·iowait와 같은 고정임계 함정. 주신호는 이용률(Available 메모리, Linux MemAvailable와 동형), 페이징은 보조(총 Pages/sec 대신 Pages Input/sec 하드폴트).
- 크기: swap_used = swap_total - swap_free(있음). 스왑 사용량이 사이징 크기 신호(메모리에선 이게 CPU 실행 큐 역할).
- 오류: OOM = vmstat oom_kill(4.13+, 없으면 null) — 단 이미 프로세스가 죽은 뒤 남는 기록이라 예방 신호는 아님. Windows 등가 없음.

### swapless가 기본 (실측 55%)
Linux 다수(58대 중 32대)가 swapless라 스왑이 없다 -> 페이징(pswpin/pswpout)이 항상 0이라 그 포화 신호가 없다. 대신 이 경우 이용률이 오히려 직접적인 신호가 된다: MemAvailable로 캐시를 뺀 실사용량이 100%에 다가가면 스왑으로 숨을 데가 없어 곧 OOM 임박이다(스왑 있는 호스트는 캐시가 섞여 이용률이 무뎌지지만 swapless는 아니다). 그래서 "약한 신호"가 아니라 이용률이 주신호가 되는 다른 모드다.
- swapless(swap_total==0): 주신호 = 실사용량 이용률(미리 잡는 신호). oom_kill 있으면 보조로, 단 이미 터진 뒤 기록이라 예방은 못 함. 이용률 임계는 스왑 있는 경우와 동일 90%(Azure Advisor 단일 임계) — swapless는 캐시 혼입이 없어 이용률이 더 깨끗한 신호라는 점만 다르다.
- 크기: swap_used(초과분)를 못 재니 "더 필요"까지만, 정확한 증설량은 신뢰도 하향.
- 유일한 선행 신호(이용률)가 구세대(<3.14, MemAvailable 근사)에서 열화하는 건 신뢰도로 정직하게 노출.

### 분류 (under)
스왑 있는 호스트: 페이징 포화(확정) OR 이용률 높음(선행). swapless(다수): 이용률 높음(주신호, 캐시 뺀 실사용량) OR OOM(사후). 이용률 임계는 계층3(Azure Advisor, 물리 무릎 부재)지만 swapless에선 이게 주신호라 오히려 더 의미 있다.

### 사이징 (증설)
- 수요 = 물리 실사용 메모리 + swap_used(초과분). total 필요 = 수요 / 목표이용률.
- 페이징 속도 = 압박 트리거, swap_used = 크기.
- 선형 정확(보정 불요), 절벽이라 여유 CPU보다 크게(목표이용률 낮게). 통계 = 피크/p99(피크가 벽 넘으면 단 한 번에 OOM).
- 이산성: RAM 크기 고정, 올림.

### 오류 축
OOM(vmstat oom_kill, 기회) = 메모리 실패 정점. 있으면 강한 under 신호. Windows 등가 부재.

### 값 (tier 3 — Azure/AWS 관행 인용, 실측 튜닝 여지)
- 부족 이용률 임계: 스왑 호스트 90%(선행 보조) · swapless 90%(주신호). 증설 착지 목표 70%(절벽이라 여유 크게).
- 근거(tier 3): 부족 임계 90% = Azure Advisor(CPU·메모리 >= SKU 90% 시 증설 권고). 증설 목표 70% = AWS Compute Optimizer 최보수 옵션(30% headroom). 물리 무릎은 없으니 advisor prior — 진짜 탐지는 페이징·OOM(tier 1/2b)이 담당, 이 임계는 실측 OOM/페이징과 상관시켜 튜닝 여지.

### 근거 (계층)
| 판단 | 계층 | 출처 |
|------|------|------|
| 포화 = 스와핑/페이징 (주 신호) | 1 | USE Method (Gregg) — si/so, scanning, OOM |
| OOM = 하드 실패 / swap_used = 초과분 | 2b | 커널 MM 동작 |
| 실사용 메모리 = (total-available), RAM 늘리면 여유 공간 | 2 | Denning working-set model |
| 신호 가용 (pswpin/pswpout 2.6.0+) | proc(5) | 확인됨 |
| 이용률 임계 90% · 증설 목표 70% | 3 | Azure Advisor(mem>=90% resize) · AWS(30% headroom) |
| 여유·목표이용률 | 여유 기준 | 우리 결정 (문서 아님) |

### 한계
- Windows 메모리 사이징 크기 신호 부재: pagefile이 여유 RAM에도 상시 쓰여 상시 사용분이 섞임 -> swap_used 같은 깨끗한 초과분 없음 -> Windows 메모리 사이징 정밀도 낮음.
- swapless Linux도 같은 저정밀 상태(위 swapless 절) — 초과분 못 재고 페이징 신호도 없음.
- 구세대(<3.14)는 MemAvailable 부재로 이용률 근사 -> 신뢰도 하향.
- 메모리 이용률 임계는 물리 무릎이 없음 -> 포화 중심, 임계는 약한 선행으로만.

---

## 디스크 확정 모델 (잠금)

디스크는 두 하위자원이라 갈래가 하나 많다. 용량과 I/O는 물리가 아예 달라 따로 모델링한다.

### A. 용량 (space) — 차오름/누적 자원
성능 무릎이 없다 (Gregg USE: 포화 "once it's full, ENOSPC" — 사실상 없음). 그래서 under/over 분류가 아니라 "소진까지 남은 시간" 예측이 산출이다. CPU/메모리의 크기 조정와 다른 별개 액션(스토리지 추가)이라 자원별 독립 판정 원칙과 정합.

신호 (양 OS):
- 이용률(용량): used/total per mount. Linux statvfs(있음), Windows GetDiskFreeSpaceExW(있음). 남은 용량은 f_bavail(비특권 여유, root 예약 5% 제외)로 — 실제 소진 시점과 맞음.
- 이용률(inode): Linux 한정, statvfs f_files/f_ffree(같은 호출이라 공짜, 신규 발행). 작은 파일 폭증이면 바이트가 남아도 inode가 먼저 소진돼 ENOSPC. 바이트 용량과 나란히 같은 추세 방법 적용. Windows는 NTFS MFT가 동적이라 해당 없음.
- 추세(모델 본체): used(바이트·inode) 시계열 기울기 = 채워지는 속도. 엔진이 기존 mount 이력에서 계산.
- 오류: ENOSPC — used%+추세가 "곧 참"을 이미 알려줘 중복, 미채택.

산출:
- 남은 시간 = f_bavail / 채워지는 속도 (바이트·inode 각각 계산). 남은 시간이 목표 기간 30일보다 짧으면 "스토리지 추가" 권고.
- 정적 가드 85%: 데이터가 짧아 추세 신뢰도가 낮은 동안(배포 초기·단명 호스트)엔 이 정적 값이 실질 주신호다. 데이터가 쌓여 추세 신뢰도가 오르면 추세가 주도, 85%는 보조로.
- (정적 가드 85%는 계층 3 — 업계 표준 monitoring 임계(major 85% / critical 95%), 물리 무릎은 아님.)

### 추세 방법 (명세)
- 표본: mount별 used를 일 단위로 다운샘플(하루 중앙값 used_bytes — 하루 안 노이즈에 안정). used는 천천히 변하는 수준값이라 5분 해상도 불요.
- 기울기: Theil-Sen 추정(모든 점쌍 기울기의 중앙값). 정리로 인한 급락은 비단조지만 소수 점쌍이라 중앙값이 걸러내 근본 증가 추세만 남는다 — 최소제곱은 급락 한 번에 휘둘려 부적합. 기울기가 0 이하(수평·감소)면 안 차는 것이라 남은 시간 없음(정상).
- mount 단위: mount마다 따로 계산. 호스트 판정·정렬은 가장 빨리 차는 mount로, 단 근거에는 목표 기간 안에 차는 mount를 전부 나열(worst만 보고하면 조치 후 다음 것이 튀어나오는 반복 회피).
- 데이터 기간 반영: 데이터 기간은 보존이 아니라 수집한 시간이라 가변이다(실측 dev 2일 / 운영 전제 약 1개월 / 시간 따라 증가). 하한을 따로 두지 않는다 — runway는 있는 데이터로 늘 산출하되, 신뢰도가 데이터 일수에 따라 스케일한다(며칠치면 낮고 길수록 높음). 하드 컷 없이 신뢰도 축이 흡수한다.
- 가속: 최근 속도가 계속된다고 가정하고 본격 모델링은 하지 않는다. 월간 계절성(로테이션)은 약 1개월 데이터로는 놓칠 수 있다(한계).

근거 (계층):
| 판단 | 계층 | 출처 |
|------|------|------|
| 이용률 = used/total | 1 | USE (Gregg) — df -h |
| 포화 없음(차면 ENOSPC) | 1 | USE (Gregg) — "once full, ENOSPC" |
| 추세 = 채워지는 속도 -> 남은 시간 | 2(operational) | 용량 계획 표준 관행 — USE 밖 예측, 단일 공식 논문 없음 |
| 안정적 기울기 = Theil-Sen | 2 | 통계 — 이상치에 강한 표준 추정 |
| 데이터 기간 -> 신뢰도 스케일(하한 없음) | 우리 결정 | dev 2일 실측 -> 하드 컷 폐기 |
| 정적 가드 85% (추세 fallback) | 3 | 업계 표준 monitoring (major 85% / critical 95%) |
| runway 목표 30일 | 여유 기준 | lead time — 추세 예측 자체는 best practice |
| 신호 가용 | POSIX / MS 공식 문서 | statvfs POSIX.1-2001(musl 지원·에이전트 사용) / GetDiskFreeSpaceExW Server 2003(NT5.2) |

한계: 추세 방법·데이터 기간·계절성은 위 "추세 방법" 절에 명세. 그 외 — Windows 물리디스크-마운트 매핑이 불완전(환경 합산 주의, 개별 마운트는 신뢰).

### B. I/O (throughput/latency) — 흐름·대기 자원 (CPU 계열)
USE 그대로. CPU와 평행이나 사이징만 다르다.

신호 (양 OS):
- 이용률: %util(장치 busy 비율). Linux io_ticks(diskstats 13, 파싱·버림 -> 발행), Windows IdleTime+QueryTime(IOCTL_DISK_PERFORMANCE, 미발행 -> 발행). MS 공식 문서 확인: Server 2003(NT5.2) 지원, 필드 존재.
- 포화(주): 큐 + await. Linux avgqu-sz=weighted(14)/경과시간, in_flight(12) 순간큐, await=(time_reading(7)+time_writing(11))/IO수 — 전부 파싱·버림, 발행만. Windows QueueDepth(있음) + ReadTime/WriteTime/ReadCount/WriteCount(IOCTL, 미발행 -> 발행).
- 수요 참고값: IOPS = reads+writes_completed(있음, 양 OS).
- 오류: IO 에러(/sys ioerr_cnt·SMART) — 수집 난이도(per-device sysfs·smartctl)로 미채택, USE errors 공백 명시.
- in_flight(12) 순간 큐는 avgqu-sz(14, 시간평균)와 겹쳐 노이즈만 더하니 수집만, 분류엔 avgqu-sz만.

### 파생 산식 (명세)
전부 누적 카운터라 연속 두 측정값의 차이로 계산(엔진, Linux disk_io 기존 방식과 동일):
- %util: Linux delta(io_ticks)/delta(경과시간) · Windows 1 - delta(IdleTime)/delta(QueryTime). Windows는 IdleTime·QueryTime이 같은 단위라 비율이라 단위를 몰라도 정확.
- await: (delta 읽기시간 + delta 쓰기시간) / (delta 읽기수 + delta 쓰기수). Linux time_reading(7)+time_writing(11) · Windows ReadTime+WriteTime. Windows 시간 단위(100나노초)는 MS 페이지 미명시이나 관례 — 절대 ms 환산 시 적용(raw 발행 후 엔진 해석).
- avgqu-sz: Linux delta(weighted 14)/delta(경과시간). Windows는 QueueDepth(순간 큐)로 대체.
- Windows는 QueryTime을 델타 분모로 같이 발행(수집 주기가 아니라 IOCTL 조회 구간이 정확).

디스크 단위: 디스크마다 계산, 호스트 판정 = 가장 포화된 디스크(worst disk). 용량의 worst mount와 같은 방식.

분류(포화) — virtio 보정: 이 fleet은 virtio 다중큐·SSD·Ceph 백엔드라 "%util 100% · avgqu>1" 임계가 안 맞는다. 병렬 스토리지는 100% busy여도 포화가 아니고(Gregg 본인이 가상·병렬 장치에서 %util 무의미라 경고), 큐 깊이 1은 아무것도 아니다. 그래서 포화 주 신호는 await(I/O 응답 지연) 절대값 — p95 > 20ms면 병목(VMware read >20ms critical, SQL Server ~10-15ms). %util·avgqu는 참고로만(물리 스핀들 기준 임계라 재보정 대상). 게스트 await엔 하이퍼바이저 큐잉·이웃 VM 간섭이 섞이므로 신뢰도 단서로 동반.
- Windows await 위험: virtio-blk/vioscsi 드라이버가 DISK_PERFORMANCE의 time 필드(Read/WriteTime·IdleTime)를 안 채울 수 있다(빈 disk queue 전례). 그럼 await가 조용히 0/garbage -> 실제 virtio 게스트에서 채워지는지 사전 검증, 미채워지면 QueueDepth만 + "포화 미관측" 신뢰도 마커.

사이징 — CPU·메모리와 결정적으로 다름:
- 디스크 I/O는 증분 불가다(코어·RAM처럼 "더 넣기" 못 함). 완화 = 디스크 타입 변경(HDD->SSD)·IOPS 티어 상향·부하 분산.
- 그래서 권고는 계산된 목표가 아니라 관찰/표시 + 크기 참고값: "I/O 바운드(현재 X IOPS에서 포화) — 더 빠른 티어 검토". 정밀 목표 없음(티어 이산+워크로드 의존) -> 근거 우선으로 표시+참고값만. 깨끗한 크기 신호(코어수·GB)가 디스크 I/O엔 없음을 정직 명시.
- 통계: 포화·await p95(지속).

근거 (계층):
| 판단 | 계층 | 출처 |
|------|------|------|
| 이용률 = %util(io_ticks) | 1 | USE (Gregg) — iostat %util |
| 포화 = avgqu-sz>1, await | 1 | USE (Gregg) — iostat avgqu-sz/await |
| 신호 가용(Linux 14필드 whole disk) | iostats.rst | diskstats 2.5.69+, 전 필드 2.6+ — floor 이하 |
| 신호 가용(Windows) | MS 공식 문서 | DISK_PERFORMANCE — Server 2003(NT5.2) 지원, 필드 확인 |
| 파생 산식(%util·await·avgqu 델타) | 2 | iostat/Windows perf 표준 산식 |
| 포화 임계 await p95 > 20ms | 3 | VMware(read >20ms critical) · SQL Server(~10-15ms) |
| IO 사이징 = 표시(계산 아님) | 구조 | 디스크 I/O 증분 불가 |
| IO 오류축 | 미채택 | 수집 난이도 — USE errors 공백 |

### 디스크 종합 산출
한 호스트의 디스크는 두 독립 산출을 낸다: (1) 용량 -> 남은 시간/스토리지 추가 (2) I/O -> 포화 표시/티어 검토. 서로 다른 액션이라 종합에서 별도 노출(자원별 독립 원칙).

### 확정 상태
- I/O 오류축 미채택 확정 — 사이징이 아닌 건강 신호 + 가상화 게스트가 물리 디스크 건강을 못 봄(SMART·ioerr는 하이퍼바이저/스토리지 층) + 수집 난이도. 스토리지 건강은 하이퍼바이저 몫. USE 오류축은 이유와 함께 공백으로 둠.
- I/O 사이징 = 표시 + IOPS 크기 참고 확정. IOPS는 분류기·정밀 목표가 아니라, 포화 판정에 딸린 수요 크기 참고값.
- 확정: 용량 runway 목표 30일 · 정적 가드 85%(tier 3) · I/O await 임계 20ms(p95).

---

## 네트워크 확정 모델 (잠금)

사이징 축이 아니다: vNIC 용량(링크 속도)을 모른다(virtio는 /sys/class/net/speed = -1) -> 이용률% 산출 불가 -> under/over 없음. vNIC를 코어·RAM처럼 정밀하게 크기 조정하지도 않는다. 세 역할만 한다: 유휴 감지(처리량) · 품질 표시(드롭·재전송) · 오류(virtio 대부분 0).

### 신호 (양 OS)
- 처리량(이용률): rx/tx bytes(걷음). 이용률%는 링크 속도가 필요한데 virtio가 안 줌 -> 처리량 raw로 유휴/종료 감지에만.
- 포화: 드롭(Linux rx/tx drops — /proc/net/dev, 지금 %*d로 버림, 신규; Windows In/OutDiscards — MIB_IFROW, 이미 읽는 구조체) + TCP 재전송(Linux /proc/net/snmp RetransSegs, 신규 read; Windows GetTcpStatistics dwRetransSegs). virtio 게스트에서도 유의미(링 버퍼 오버런·경로 손실).
- 소켓/연결 고갈: Linux /proc/net/sockstat(TIME_WAIT 적체) · nf_conntrack_count 대비 _max(conntrack 모듈 로드 시). 바쁜 서버·프록시의 실 장애 모드라 품질 신호로 추가(파일 하나 read). Windows는 GetTcpStatistics 연결 수로 근사.
- 오류: rx/tx errors(걷음, 양 OS). virtio는 물리 NIC 부재로 대부분 0 — 값어치 낮음.

### 분류
- under/over 없음.
- 유휴/종료: 처리량 낮음(기존 net_avg_kbps 역할 유지, CPU 낮음과 함께).
- 품질 표시: TCP 재전송률 > 1% 또는 드롭률 > 0.5%면 "네트워크 혼잡/스트레스, 조사"(건강 신호, 사이징 아님). 둘 다 비율 기준이라 트래픽 많은 호스트도 공정.

### 사이징
없음. 표시만(디스크 I/O보다도 신호가 약함).

### 오류 축 (5자원 중 유일하게 채워짐)
드롭·TCP 재전송·rx/tx errors. 값어치는 드롭·재전송(virtio 유의미) > rx/tx errors(virtio 0). USE 세 번째가 여기서 처음 산다.

### 근거 (계층)
| 판단 | 계층 | 출처 |
|------|------|------|
| 이용률 = 처리량 | 1 | USE (Gregg) — /proc/net/dev bytes |
| 포화 = 드롭·overrun·TCP 재전송 | 1 | USE (Gregg) — drops/overruns, 재전송 |
| 품질 임계 재전송 1% · 드롭 0.5% | 3 | 모니터링 관행 — oneuptime(재전송 >1% 성능 영향) · nojitter(드롭 <0.5% 비즈니스 앱) |
| 오류 = rx/tx errors | 1 | USE (Gregg) — /proc/net/dev errs |
| 신호 가용(Linux) | proc/표준 | /proc/net/dev(2.6+, 이미 파싱) · /proc/net/snmp RetransSegs |
| 신호 가용(Windows) | MS 공식 문서 | GetTcpStatistics Win2000+ / MIB_IFROW discards(이미 사용 구조체) |
| 이용률% 불가 | 한계 | virtio 링크 속도 부재 |

### 한계
- 이용률%는 링크 속도가 있어야 하는데 virtio가 안 줌 -> 처리량 raw만, %-of-capacity 없음.
- rx/tx errors는 virtio 물리계층 부재로 대부분 0(디스크 SMART처럼 게스트가 못 보는 것) — 드롭·재전송이 더 유의미.

---

## 종합·근본원인 규칙 (축 6 — 전 자원 공통)

자원 5개 판정을 호스트 하나로 종합할 때 "가장 나쁜 자원 승"은 틀리다 — 자원이 인과로 결합돼 있어 증상 자원에 오진·과다처방(RAM 하나 문제에 RAM+SSD+코어 삼중 권고)한다. 인과 사슬을 거슬러 원인(root) 자원을 짚는다.

인과 사슬 (OS 메커니즘, tier 1/2b):
- 메모리 압박 -> swap 발생 -> swap I/O -> 디스크 지연·부하 (Gregg: swapping이 보이면 메모리 saturation)
- 디스크 I/O 포화 -> 프로세스 D-state 블록 -> load 상승 -> CPU 로드 높아 보임 (Gregg: Linux load는 CPU 대기 + uninterruptible I/O 블록을 함께 포함, wait I/O = 디스크 병목)
- CPU 포화 -> 자체 (다른 자원 이용률로 안 번짐)

상류 -> 하류: 메모리 -> 디스크 I/O -> CPU. 하류는 상류의 증상일 수 있다.

방향 판별 신호 (근거順, 이미 수집):
- 메모리발: swap 발생(pswpin/pswpout > 0). 있으면 동반 디스크 I/O는 swap 트래픽(증상), root = 메모리.
- 디스크발: procs_blocked(D-state, vmstat "b") 높음 + await 포화. CPU 로드는 증상, root = 디스크 I/O.
- CPU발: run queue 높음 + procs_blocked 낮음 + swap 없음. root = CPU.
- iowait 강등: 다중코어에서 희석돼 못 믿는다(IO 병목이 한 자리% iowait로 숨음). 주 판별에서 빼고 약한 보강만 — Gregg "D-state 직접 세라, iowait로 gate 말라".

규칙:
1. 여러 자원 동시 부족 시 위 신호로 상류 root를 짚는다.
2. root에만 처방. 하류(증상)는 "root 해결 후 재평가"로 표시 — 독립 처방 안 함(삼중 처방 방지).
3. 결합 신호 없이 각자 부족이면 각자 처방(진짜 독립 문제).

호스트 요약 = root 자원 + 그 처방. 정렬은 root 심각도. "worst 자원 승" 폐기.

한계: 인과 방향 추정이라 오귀속 가능(메모리 압박과 우연히 독립적인 디스크 I/O 동시). 판별 신호로 상당수 가르나 완벽 아님 -> 신뢰도로 노출.

| 판단 | 계층 | 출처 |
|------|------|------|
| load = CPU 대기 + D-state I/O, wait I/O = 디스크 병목 | 1/2b | Brendan Gregg — Linux Perf 60s |
| swapping = 메모리 saturation | 1 | USE Method (Gregg) |
| iowait 다중코어 희석 -> D-state 직접 세기 | 2b | Percona / Red Hat iowait 해석 |
| 상류 우선·root만 처방 | 우리 결정 | 위 메커니즘의 합성 |

---

## 신뢰도 축 (가로 — 전 자원 공통) + 다운사이즈 처방 규칙

신뢰도는 4축 중 하나로, 분류와 별개로 "측정된 값을 얼마나 믿나"를 모든 자원·모든 호스트에 붙인다 (다운사이즈 전용이 아님 — 증설·병목·유휴 판정에도 동일하게 따라붙는다). 측정 불확실성을 네 종류로 가른다 — 종류가 다르면 원인도 대응도 다르다:

1. 통계적 정밀도 — 표본 수(이력 길이)와 분산(버스티)이 한 축이다. 분위수 추정의 표준오차가 n과 분산에 함께 의존하니까(tier 2, 표본 통계 — order statistics). 데이터가 적거나 튈수록 p95가 흔들려 하향. 바닥(floor): 최소 데이터 미달이면 분류·권고를 아예 안 내고 "표본 부족". AWS Compute Optimizer도 14일 창에 누적 30시간 미만이면 "insufficient data"로 권고 안 냄(tier 3). 그 위로는 데이터량·분산에 따라 연속 스케일(하드 컷 없음).
2. 커버리지 — 필요한 축이 다 측정됐나(is_partial). 이진. 예: Windows disk await 미측정 -> 포화 축 공백. 측정된 축만으로 분류하되 "포화 미관측" 마커 노출.
3. 측정 충실도(계통 편향) — virtio 오염(steal·게스트 await엔 하이퍼바이저·이웃 VM 간섭), 근사(구세대 <3.14 MemAvailable). 1번(통계적 흔들림)과 다른 종류 — 표본을 늘려도 안 줄어드는 편향(bias). 값이 오염됐다는 단서로 하향.
4. 정상성 — 이력이 미래를 대변하나(추세·계절성). "현재 상태" 신뢰엔 무관하고, forward-looking 결정(다운사이즈·용량 runway)에만 걸린다.

이 신뢰도는 화면·보고서에 고/저 마커로 그대로 노출된다 — 잘못 믿고 조치하지 않게.

다운사이즈 처방 규칙 — 잘못된 다운사이즈가 최악이라(전제 2), 신뢰도가 높고 + 상승 추세가 아닐 때만 처방. 아니면 관찰만. (과다 "분류"는 사용률 낮으면 늘 뜬다 — 게이트하는 건 분류가 아니라 "구체적 다운사이즈 처방". 불충족이면 과다로 표시하되 권고는 "관찰만".)
- 신뢰도 높음 = 1 정밀도 충분(바닥 30h 훨씬 위 + 안 버스티, p95/median <= 2) + 2 커버리지 온전(측정 공백 없음) + 3 충실도 온전(심한 오염 없음). 다운사이즈는 위험 방향이라 바닥(30h)이 아니라 넉넉한 이력을 요구 — 우리 보수 문턱.
- 상승 추세 아님 (보합·하락 OK) — 4 정상성. Theil-Sen slope가 유의한 상승이면 관찰만(저사용이어도 곧 커질 수 있으니). 절대 수준(보합인데 높음)은 분류가 이미 걸러(과다=저사용) 여기 오지 않는다.

| 판단 | 계층 | 출처 |
|------|------|------|
| 표본 부족 바닥 = 14일 창 누적 30h 미만 | 3 | AWS Compute Optimizer insufficient-data 기준 |
| 분위수 추정 정밀도 ~ n·분산 | 2 | 표본 통계 (order statistics 표준오차) |
| 버스티 p95/median <= 2 · 상승추세 · 다운사이즈 이력 문턱 | 여유 기준 | 우리 결정 |

---

## 신호 x OS 소스 매트릭스 (에이전트 수집 명세로 누적)

최소 지원 버전: Linux 커널 2.6.32, Windows NT5.2(Server 2003). 각 신호가 최소 지원 버전에서 수집 가능함을 확인하며 채운다.

### CPU (확정)
| USE 축 | 신호 | Linux 소스 | Windows 소스 | 최소버전 지원 | 현재 수집 |
|--------|------|-----------|-------------|----------|----------|
| 이용률 | cpu% p95·피크 | /proc/stat cpu | GetSystemTimes | OK | 있음 |
| 포화 | 실행큐/코어 | /proc/stat procs_running (schedstat 있으면 우선) | Processor Queue Length | OK | Linux 신규 |
| 이용률(코어별) | per-core max | /proc/stat cpu0..N | per-processor | OK | 신규 |
| 포화 귀속 | IO 블록 | /proc/stat procs_blocked | 불요(Win 큐 깨끗) | OK | Linux 신규 |
| 오류/왜곡 | steal | /proc/stat steal | null | OK | 있음 |
| 포화(기회) | PSI cpu | /proc/pressure/cpu | 없음 | 4.20+ 만, 없으면 null | 신규(기회) |

가용 확정(proc(5)): procs_running·procs_blocked "Linux 2.5.45 onward", steal "since Linux 2.6.11", iowait "since Linux 2.5.41" — 전부 최소 커널 2.6.32 이전. Windows PQL·GetSystemTimes는 에이전트가 이미 수집(코드 확인).
CPU 신규 수집 = procs_running · procs_blocked · per-core(cpu0..N) (전부 이미 읽는 /proc/stat, 발행만) + schedstat(있으면 우선). PSI cpu는 수집만.

### 메모리 (확정)
| USE 축 | 신호 | Linux 소스 | Windows 소스 | 최소버전 지원 | 현재 |
|--------|------|-----------|-------------|----------|------|
| 이용률 | 실사용 메모리 (total-available)/total | /proc/meminfo MemAvailable | GlobalMemoryStatusEx | OK(<3.14 대체 계산) | 있음 |
| 포화(주) | 스왑 발생 | /proc/vmstat pswpin·pswpout | Memory\Pages/sec | OK(vmstat 2.6.0) | Linux 신규 |
| 크기 | 초과분 | swap_total-swap_free | pagefile(부적합) | OK | 있음 |
| 오류 | OOM | /proc/vmstat oom_kill | 없음 | 4.13+, 없으면 null | 신규(기회) |
| 포화(기회) | PSI memory | /proc/pressure/memory | 없음 | 4.20+ | 신규(기회) |

가용 확정(proc(5)): pswpin·pswpout·pgmajfault "since Linux 2.6.0" — 최소 커널 2.6.32 이전. oom_kill man page 부재 = 4.13+(기회).
메모리 신규 수집 = vmstat pswpin·pswpout(스왑 발생 정도) + oom_kill(4.13+). pgmajfault는 미채택(파일 fault 혼동), PSI는 수집만. vmstat 파일 하나 신규 read.

### 디스크 (초안)
| 하위자원 | USE 축 | 신호 | Linux 소스 | Windows 소스 | 최소버전 지원 | 현재 |
|----------|--------|------|-----------|-------------|----------|------|
| 용량 | 이용률(바이트) | used/total, 남은=f_bavail | statvfs (mounts) | GetDiskFreeSpaceExW | OK | 있음 |
| 용량 | 이용률(inode) | f_files/f_ffree | statvfs (같은 호출) | 해당 없음(NTFS MFT 동적) | OK | Linux 신규 |
| 용량 | 추세 | 채워지는 속도(바이트·inode) | 엔진이 mount 이력서 계산 | 동일 | OK | 신규(엔진, 수집 0) |
| I/O | 포화(주) | await (virtio라 %util·avgqu 아님) | diskstats time_rd/wr(7,11) | IOCTL Read/WriteTime(+QueueDepth) | OK(2.6+) | 신규 (Win time필드 미채워짐 위험) |
| I/O | 참고 | %util·avgqu | diskstats io_ticks(13)·weighted(14) | IOCTL IdleTime | OK(2.6+) | 신규(참고·재보정) |
| I/O | 수요 | IOPS | diskstats reads/writes | disk_io reads/writes | OK | 있음 |
| I/O | (수집만) | in_flight·PSI io | diskstats(12)·/proc/pressure/io | 없음 | - | 수집만 |

가용 확정: diskstats 2.5.69+(man), whole-disk 14필드 2.6+(iostats.rst) — floor 2.6.32 이하. 에이전트는 /sys/block 필터로 whole-disk만 보고 이 필드들을 이미 파싱·버림. Windows IOCTL_DISK_PERFORMANCE는 NT5.2+, QueueDepth 뽑는 그 호출에 IdleTime·ReadTime·WriteTime 동봉.
디스크 신규 수집:
- Linux: diskstats time_reading(7)·time_writing(11)·io_ticks(13)·weighted(14) 발행(이미 파싱) + statvfs f_ffree(inode, 같은 호출). in_flight(12)·PSI io는 수집만.
- Windows: IOCTL_DISK_PERFORMANCE의 IdleTime·ReadTime·WriteTime 발행(QueueDepth와 같은 호출) — 단 virtio 드라이버가 time 필드를 안 채울 수 있어 사전 검증.
- 용량: 바이트·inode 추세 모두 엔진이 기존 mount 이력에서 계산(신규 수집은 f_ffree 하나).

### 네트워크 (확정)
| USE 축 | 신호 | Linux 소스 | Windows 소스 | 최소버전 지원 | 현재 |
|--------|------|-----------|-------------|----------|------|
| 이용률 | 처리량 | /proc/net/dev rx/tx bytes | GetIfTable rx/tx bytes | OK | 있음 |
| 포화 | 드롭 | /proc/net/dev rx/tx drops | MIB_IFROW In/OutDiscards | OK | 신규(Linux 발행 / Win 구조체 동봉) |
| 포화 | TCP 재전송 | /proc/net/snmp RetransSegs | GetTcpStatistics dwRetransSegs | OK(Win2000+) | 신규 |
| 품질 | 소켓/연결 고갈 | /proc/net/sockstat · nf_conntrack | GetTcpStatistics 연결수 | OK | 신규 |
| 오류 | rx/tx errors | /proc/net/dev rx/tx errs | MIB_IFROW dwIn/OutErrors | OK | 있음(분류 미가중) |

가용 확정: Linux /proc/net/dev 드롭은 2.6+(이미 파싱)·/proc/net/snmp RetransSegs 표준. Windows GetTcpStatistics Win2000+(MS 공식 문서), In/OutDiscards는 errors 걷는 MIB_IFROW 동봉(코드 확인).
네트워크 신규 수집:
- Linux: /proc/net/dev rx/tx drops(이미 파싱, 발행만) + /proc/net/snmp TCP RetransSegs + /proc/net/sockstat·nf_conntrack(품질, 파일 read).
- Windows: MIB_IFROW dwInDiscards/dwOutDiscards(이미 읽는 구조체, 발행만) + GetTcpStatistics dwRetransSegs(신규 call).

## 검증 규칙 (엄밀성 테스트)

모든 임계·분기가 전제1 계층 또는 전제2로 추적되는가? 추적 안 되면 뿌리 없는 값 -> 승격하거나 제거한다.

엄밀성 테스트 결과 (전부 해소):
- 오류 축 부재 -> 해소: 네트워크 품질(드롭·재전송) 전용으로 채택.
- mem 80 / disk 85 / paging 1000 (계층4) -> 해소: mem·disk tier 3 앵커, paging 폐기.
- over가 디스크 안 봄 -> 해소: 축6 종합·근본원인 규칙.
- util% -> 코어수 스케일링 가정 -> 해소: CPU 모델에 선형+암달·ceil(부하/70) 명시.

---

## 다음 단계

1. CPU 확정·잠금 (완료, 근거 태그 포함).
2. 메모리 확정·잠금 (완료, 근거 태그 포함).
3. 디스크(용량=차오름, IO=포화) 확정·잠금 (완료).
4. 네트워크(오류·포화·유휴) 확정·잠금 (완료).
5. 가로 결정: 근본원인 종합(축6)·신뢰도 축(축5 흡수) (완료).
6. 매트릭스 완성 -> 에이전트 wire 설계·양 OS 수정 (한 방).
7. 여유 기준 값 일괄 확정 (완료 — 5자원 전부).
8. recommendation.py 재구성(자원별 판정 + 종합) -> 보고서·표현 연쇄.
