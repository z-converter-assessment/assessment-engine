# 자원 적정성 평가 임계치와 근거

이 문서는 자원 적정성(right-sizing) 평가에 쓰이는 임계치와 그 근거를, 5개 자원 x USE 3축으로 사람이
읽기 좋게 정리한 단일 참조다. 구현 명세(어느 함수·컬럼·판정 순서)는 `right-sizing.md` 가 담고, 임계
수치·근거·robustness 의 인간가독 정본은 본 문서다. 결정 경위·대안 검토 이력은 `decisions/adr` 아카이브에 있다.

## 무엇을 어떻게 평가하나

- 자원 5개: CPU, Memory, Disk 용량, Disk IO, Network.
- 축 3개 (USE Method): Utilization(얼마나 쓰나), Saturation(부족해서 얼마나 밀리나), Errors(오류·고장).
  - 사이징(증설/감축) 판정은 CPU · Memory · Disk 용량 3자원만. Disk IO 는 "포화 여부"만 표시(증설 숫자 미산출),
    Network 는 별도 혼잡 플래그로만 노출.
  - Errors 는 사이징에 섞지 않고 health(고장) 신호로 따로 보여준다.
- 평가 창: 14일. 대부분의 임계는 그 창의 p95(상위 5% 순간 스파이크를 잘라낸 값)로 비교한다. 용량 runway 만
  가용 이력 전체를 쓴다(누적 신호라 길수록 정확).

## 근거 강도 표기

각 임계에 근거가 어디서 왔는지 정직하게 붙였다.

- 벤더확인: 벤더 문서에 명시된 값 (예 AWS, Microsoft).
- 벤더문서: 벤더 문서 기반이나 표현이 근사한 값.
- 관례: 특정 문서에 딱 박히진 않았지만 업계에서 통상 쓰는 값.
- 판단: 우리가 의식적으로 정한 재량값(근거는 있으나 벤더 고정값은 아님).

수치가 없거나 데이터로 정해야 하는 값(대표적으로 PSI)은 "지금은 안 씀 -> 데이터 쌓인 뒤" 절에 따로 뒀다.

---

## CPU

| 축 | 신호 | 임계 | 근거 |
|----|------|------|------|
| Utilization | 사용률 p95 (1 - idle/total) | under 70% / 목표 70% / 유휴 3% 이하 / per-core 85% 이상 보류 | 벤더문서 (AWS Compute Optimizer, Azure Advisor, Gregg) |
| Saturation | 실행 큐 p95 / 코어수 | Linux 1.0 이상 / Windows 2.0 이상 | 벤더확인 (Gregg vmstat r, MS Processor Queue) |
| Errors | cpu_mce (Machine Check) | 창 내 신규 발생 > 0 = health 경고 | 관례 (Gregg USE, Linux MCE/EDAC) |

- 사용률은 CPU 시간(초 카운터)에서 1 - idle/total 로 계산하고 14일 p95 로 본다. per-core 로 어느 한 코어라도
  p95 85% 이상이면 집계 평균이 낮아도 다운사이즈/유휴 판정을 보류한다(단일스레드 병목 보호).
- 실행 큐(run queue)는 "CPU 를 기다리는 프로세스 수"다. 코어당 1개 이상(Windows 는 ready-only 라 2개 기준)이
  지속되면 포화. load average 는 D-state IO 대기가 섞여 오염되므로 폐기하고 실행 큐로 대체했다.
- steal(가상화에서 하이퍼바이저에 뺏긴 시간)로 인과를 가른다: 실행 큐가 높은데 steal 도 높으면(p95 5% 이상,
  벤더확인 AWS/Datadog) 하이퍼바이저 경합이라 vCPU 를 늘려도 안 풀린다 -> 증설 처방 억제(biased 표시).
  steal 이 낮으면 진짜 vCPU 부족 -> 증설. 이건 오버서브스크립션 fleet 에서 잘못된 증설을 막는 v2 개선이다.
- 스파이크 방어: 사용률·큐 모두 p95(순간 100% burst 무시). 유휴 판정도 p95(순간 스파이크로 유휴 탈락 방지).

## Memory

| 축 | 신호 | 임계 | 근거 |
|----|------|------|------|
| Utilization | 사용률 p95 (Linux 1-available/limit, Win commit) | under 90% / 목표 80% / Windows 커밋 80% | 벤더문서 (Azure Advisor, AWS Compute Optimizer, MS Perfmon) |
| Saturation | dual-gate: 사용률 높음 AND paging_major 지속 | used 90% 이상 AND 하드폴트 지속, 둘 다 | 관례 (MS Perfmon, Red Hat vmstat) |
| Errors | oom_kill / hardware_corrupted | oom 1건 이상 = under 확정 / 손상 > 0 = health | 관례 (Gregg USE, kernel meminfo) |

- 사용률은 Linux 는 available(캐시 제외 실효 여유) 기준이라 페이지캐시로 부풀지 않는다. 사이징은 near-peak(관측
  피크) 80% 착지(20% 여유) -- AWS 기본과 정합, 비용 최적 방향. 분류(under 90%)는 p95, 사이징은 near-peak 로
  통계가 갈린다. Windows 는 물리 used% + 커밋률 80% 를 함께 본다.
- 포화는 dual-gate(AND) 다: "사용률 90% 이상" 이면서 동시에 "paging_major(하드폴트) 지속" 일 때만 포화로
  본다. 하나만으로는 오탐이 난다 -- 하드폴트만 보면 mmap DB 나 프로그램 시작의 정상 디스크 읽기를 포화로
  오인하고(예: RAM 88GB 남는 서버에 메모리 증설 권고), 사용률만 보면 페이지캐시로 90% 찬 정상 서버를 오인한다.
  둘 다 참이면 "메모리가 빡빡한데 실제로 디스크 폴트 비용을 치르고 있다" = 진짜 압박이다.
- oom_kill(메모리 부족으로 프로세스가 강제 종료된 사후 증거)은 gate 없이 즉시 under 확정. swap 런타임 추이는
  압박 신호로 쓰지 않는다(swappiness 유휴 스왑아웃 오탐) -- swap 은 용량만 보고, 압박은 위 dual-gate 로 판정.
- 스파이크 방어: paging 은 창 전체 delta rate(단일 버킷 무시), dual-gate 로 mmap/swappiness 오탐 이중 차단.

## Disk 용량

| 축 | 신호 | 임계 | 근거 |
|----|------|------|------|
| Utilization | used% / inode used% (worst mount) | 85% 이상 | 관례 (모니터링 표준, df/inode) |
| Saturation | runway (다 찰 때까지 남은 일수) | 30일 미만 = 채워지는 중 | 판단 (프로비저닝 lead time) / 벤더문서 (Gregg ENOSPC) |
| Errors | 파일시스템 오류 (ext4/btrfs/mdraid) | 창 내 신규 > 0 = health | 벤더문서 (Gregg, btrfs stats, mdadm) |

- 용량은 "증설/감축"이 아니라 "다 찰 때까지 남은 시간(runway)"으로 본다. used 추세를 회귀로 외삽해 ENOSPC 도달
  일수를 구하고, 30일 미만이면 채워지는 중으로 표시한다. 30일은 스토리지 프로비저닝 lead time 기준의 판단값.
- inode 도 병렬로 본다: 바이트가 남아도 작은 파일이 폭증하면 inode 가 먼저 소진돼 ENOSPC 가 난다. 단 XFS/btrfs
  는 inode 를 동적 할당해 free 값이 방대·무의미하므로 ext 계열에서만 inode 판정을 켠다.
- 확장여력(v2 개선): 마운트가 LVM 볼륨그룹에 얹혀 있고 VG 에 여유가 있으면, 같은 85% 라도 "새 디스크 없이
  lvextend 로 즉시 확장 가능"이라 긴급도·처방이 다르다. VG 여유 없으면 스토리지 증설 처방.
- worst-mount 를 볼 때 작은/가상 파티션(/boot, /var/log, tmpfs, overlay 등)은 제외한다(작은 파티션이 전체
  판정을 지배하는 오탐 방지).
- 스파이크 방어: 2점 fill_rate 는 삭제/생성 튐에 약하므로 창 내 다점 Theil-Sen 강건 회귀로 완화한다. 짧은
  이력(14일 미만)은 1년 목표 외삽을 억제하고 30일 근시 예측만 쓴다.

## Disk IO

포화 여부만 표시한다(증설 숫자 미산출). virtio 게스트 지연은 하이퍼바이저·이웃 간섭 편향이라 표본으로 안
줄어들어 biased 로 강제한다.

| 축 | 신호 | 임계 | 근거 |
|----|------|------|------|
| Utilization | %util p95 (io_time 기반) | 70% 이상 = busy (정보용, 분류 트리거 아님) | 벤더문서 (Gregg) |
| Saturation | await p95 (응답시간) | 20ms 초과 = io_bound | 벤더확인 (VMware, SQL Server, iostat) |
| Errors | disk_error (mdraid/btrfs/ext4/eventlog) | 창 내 신규 > 0 = health | 벤더확인 (btrfs stats, mdraid) |

- await(요청당 평균 응답시간, ms)를 주 신호로 쓴다. 20ms 초과가 지속되면 io_bound. HDD/SSD 차등(SSD 는 10ms)은
  벤더확인 값이나 v2 에 회전/미디어 타입 신호가 없어 지금은 평면 20ms 를 쓴다(rotational 신호 추가 후 차등).
- %util(디스크가 바쁜 시간 비율)은 정보로만 보여주고 단독 분류에 쓰지 않는다 -- SSD/NVMe 는 병렬 처리라 여유가
  있어도 100% 로 나와 오탐한다. Windows 는 IOCTL 응답시간(동일 20ms), 구세대 viostor 는 큐 깊이 2 폴백.
- 스파이크 방어: await p95(순간 지연 max 무시). io_bound 상태가 오가는 플래핑 방지로 hysteresis 를 둔다.

## Network

사이징 축이 아니다(vNIC 은 link_speed 가 없어 이용률 기반 사이징 불가). 혼잡 여부만 별도 플래그로 노출한다.

| 축 | 신호 | 임계 | 근거 |
|----|------|------|------|
| Utilization | throughput / link_speed | 사이징 임계 없음 / 유휴 2Mbps 이하 | 판단 (Gregg, AWS idle) |
| Saturation | drop rate + conntrack 비율 | drop 0.5% 초과 + conntrack 80% 이상(70% warn) | 관례 (Gregg, Prometheus) |
| Errors | rx/tx_errors + tcp 재전송률 | 지속 nonzero = health / 재전송 1% 초과 | 벤더문서 (Gregg, TCP retransmit 관례) |

- 로컬 포화는 두 신호로 본다: drop(NIC 링버퍼·큐가 넘쳐 패킷을 버림 = 호스트가 못 따라감)과 conntrack(연결추적
  테이블이 참 = 신규 연결 못 받음). conntrack 은 80% 진입 / 70% 해제 hysteresis 로 경계 진동을 막는다.
- 재전송(retransmit)은 포화가 아니라 Errors 로 뒀다. 재전송을 유발한 패킷 손실은 원격 라우터·WAN 어디서든 날
  수 있어 이 호스트의 포화가 아닌 경우가 많다. 포화로 세면 원격 혼잡을 이 호스트 탓으로 오귀속한다. Gregg 의
  USE 분류도 dropped=saturation / retransmit=errors 로 나눈다. 재전송은 사라지지 않고 health 에서 계속 보인다.
- Errors 는 전부 신규 발생 > 0 즉시 health 로 노출한다(물리 NIC 의 CRC/프레임 에러는 케이블·SFP 조기신호).
  virtio 게스트는 0 이라 무발화(무해).
- 스파이크 방어: counter 신호(drop/재전송)는 창 전체 delta rate + 지속(다수 버킷) 요건으로 순간 마이크로버스트
  (정상 TCP 혼잡제어)를 배제한다.

---

## 권고(처방) 방법 — 몇으로 늘려/줄일까

분류(under/over)가 "부족/과잉"을 가리면, 처방은 "그럼 몇으로"를 낸다. 방법은 목표 이용률 착지
(target-utilization sizing)다: 관측된 p95 이용률이 목표 %에 앉도록 크기를 역산한다.

- CPU: 목표 코어 = max(이용률 주도, 포화 주도) 중 큰 쪽.
  - 이용률 주도 = ceil(util_p95 x 현재코어 / 목표%). 예: 4코어에 util 90% -> ceil(90x4/70) = 6코어.
  - 포화 주도 = ceil(실행큐 / (포화선 x 0.7)). 실행큐를 포화선의 0.7배 아래로 떨어뜨릴 코어 수.
- Memory: 목표 = ceil(현재총량 x near_peak / 목표%). near_peak = 5분 버킷 max 의 p95(관측 피크 대표) — 메모리는 비탄력·OOM 위험이라 이용률 평균/eval-p95(버킷 avg 기반)이 아닌 피크 통계(버킷 max 의 p95)로 역산한다. p99.9 는 ~210 표본에서 절대 max 와 사실상 동일(단발 스파이크 지배)이라 견고하지 못해 p95 로 이상치 제외. 포화(swap/oom)인데 near-peak 관측이 없으면 현재총량 x 1.30(30% 상향).
- Disk 용량: 현재 성장률로 365일 버티는 총 GB(확장 목표). 이력이 짧으면 30일 예측을 70% 착지시키는 용량.
- Disk IO / Network: 사이징 숫자 없음 — 증분 불가(IO)·사이징 축 아님(Network)이라 "티어 상향 검토"·"혼잡" 표시만.

통계는 14일 창으로 잡는다: 분류·CPU 사이징은 p95, 메모리 사이징은 near-peak(버킷별 max 의 p95). max·avg 단독은
안 쓴다. 다운사이즈 구체 처방은 신뢰도가 높고 관측이 충분할 때(창 대비 70% 이상)만 낸다 -- 표본 부족한데
줄이라고 하지 않는다.

목표·여유값(조정 가능한 knob):

| 값 | 현재 | 근거 |
|----|------|------|
| CPU 목표 이용률 | 70% | 벤더문서 (AWS Balanced) |
| Memory 사이징 통계·목표 | near-peak(버킷별 max 의 p95) 80% 착지 | AWS 기본 + 비탄력 피크 대표 |
| CPU 포화 headroom 배수 | 0.7 | 판단 |
| per-core 다운사이즈 보류 | 85% | 판단 |
| Memory 포화 상향 headroom | 30% | 벤더문서 (AWS/GCP prior) |
| Disk 확장 수명 목표 | 365일 | 판단 |

설계 결정 (본 설계 과정에서 확정): 방법 구조(목표 이용률 착지)와 아래 세부 전부 지금 확정(운영 후가 아님).
- 반올림 = 정수 코어/MB 올림(ceil) 확정. 실제 인스턴스 SKU 스냅은 대상 인스턴스 카탈로그가 정의되면 후속.
- 목표 형태 = 단일 목표%(CPU p95 70 / 메모리 near-peak 80) 확정. 대역+데드밴드는 채택 안 함 -- 분류가 이미 창
  p95라 소폭 변동에 강건해 데드밴드 불요.
- 여유·판단값 = 현행 유지 확정: CPU 포화 headroom 0.7 · per-core hold 85% · 메모리 포화 headroom 30% · 디스크
  확장 목표 365일. 각각 근거(여유 판단 · advisor prior) 있음.
운영 데이터로 미세조정하는 것은 PSI 같은 캘리브레이션 신호에 한한다(아래 Deferred 절).

---

## 스파이크 오분류 방어 (공통 원칙)

임계 수치만큼 중요한 게 "순간 튀는 값에 속지 않기"다. 전 자원 공통으로 적용한다.

- 집계는 p95 기본: 게이지 신호(실행 큐, conntrack, %util, await)는 상위 5% 순간값을 잘라낸 p95 로 비교한다.
  순간 max 는 쓰지 않는다. 유휴 판정만 avg(활동이 없음을 확인).
- 카운터는 delta rate: 오류·oom·재전송·drop·paging 같은 누적 카운터는 창 안의 증가분(delta)으로 본다. 절대
  nonzero 를 쓰면 과거 1회 이벤트가 영원히 켜져 있는 오탐이 난다. 재부팅/재시작은 counter_agg 가 흡수한다.
- 지속(sustained) 요건: 재전송·drop·paging 은 창 안 다수 버킷에서 초과할 때만 발화. 단일 표본 마이크로버스트는
  정상(TCP 혼잡제어·writeback·swappiness)이라 배제한다.
- hysteresis: 상태가 오갈 위험이 있는 경계(conntrack 80/70, io_bound 진입/해제)는 진입·해제 임계를 벌린다.
- 표본 충분성: 관측이 30시간 미만이면 신뢰도를 낮추고(하드 컷 아님), 다운사이즈는 창 대비 관측 비율 70%
  이상일 때만 처방한다. 측정 안 된 축은 "판정 불가"로 두고 없는 값을 지어내지 않는다.

## 지금은 안 씀 -> 데이터 쌓인 뒤 (Deferred)

아래는 수집은 하지만 지금 판정에는 쓰지 않는다. 근거(벤더 임계)나 운영 데이터가 부족해 지어낼 수 없기 때문.
데이터가 쌓이면 근거를 갖고 별도 결정으로 도입한다. 지금부터 저장하므로 도입 시 재수집 공백은 없다.

- PSI (Pressure Stall Information, Linux 4.20+): "자원이 부족해 태스크가 멈춘 시간"을 직접 재는 최신 신호로,
  개념상 포화의 정본이다. 하지만 "stall 몇 %부터 포화"라는 임계를 어떤 벤더도 문서화하지 않았고, 좋은 값은
  우리 fleet 데이터로 캘리브레이션해야 하는데 아직 운영 데이터가 없다. 또 레거시 커널·Windows 에는 아예 없어
  마이그레이션 fleet 커버리지가 낮다. 그래서 판정은 위의 고전 신호로만 하고, PSI 는 저장만 해둔다.
- HDD/SSD await 차등: SSD 10ms / HDD 20ms 는 벤더확인 값이나 v2 에 회전/미디어 타입 신호가 없다. agent 가
  rotational flag 를 발행하게 되면 차등 적용한다. 그 전까지 평면 20ms.
- MCE 심각도(정정 가능 CE vs 불가 UCE) 분리, paging 절대 rate 세부값: 정보/데이터 부족으로 보류.

## OS 차이

판정을 고전 신호로만 하므로 PSI 가 없는 Windows·구커널도 같은 축으로 판정된다(커버리지 공백 없음).

```
AXIS          | LINUX (all)                 | WINDOWS / LEGACY
--------------+-----------------------------+------------------------------------
CPU sat       | run queue / cores >= 1.0    | Processor Queue Length / cores >= 2.0
MEM sat       | paging_major + used (AND)   | Pages Input/sec + commit
DISK-IO sat   | await > 20ms                | await (IOCTL), queue >= 2 fallback
DISK err      | ext4/btrfs/mdraid           | eventlog
inode / LVM   | ext 계열 / lvm_vgs          | NTFS (inode 개념 없음)
conntrack     | nf_conntrack (로드 시)       | 없음 (WFP 별개)
PSI (전 자원) | 저장만, 판정 미사용          | 원래 없음
```

Windows 임계가 Linux 와 다른 것은 모집단 차이지 임계 불일치가 아니다(각 OS 정본 임계 그대로). steal 은 양 OS
가상화 게스트에서만 유의하다(베어메탈은 0).
