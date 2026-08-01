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

수집은 하지만 판정에 쓰지 않는 신호(대표적으로 PSI)는 "판정에 쓰지 않는 수집 신호" 절에 따로 뒀다.

---

## CPU

| 축 | 신호 | 임계 | 근거 |
|----|------|------|------|
| Utilization | 사용률 p95 (1 - idle/total) | under 70% / 목표 70% / 유휴 3% 이하 / per-core 85% 이상 다운사이즈 보류 | 벤더문서 (AWS Compute Optimizer, Azure Advisor, Gregg) |
| Saturation | 실행 큐 p95 / 코어수 | Linux 1.0 이상 / Windows 2.0 이상 | 벤더확인 (Gregg vmstat r, MS Processor Queue) |
| Errors | cpu_mce (Machine Check) | 창 내 신규 발생 > 0 = health 경고 | 관례 (Gregg USE, Linux MCE/EDAC) |

- 사용률은 CPU 시간(초 카운터)에서 1 - idle/total 로 계산하고 14일 p95 로 본다. per-core 로 어느 한 코어라도
  p95 85% 이상이면 집계 평균이 낮아도 다운사이즈(과다 할당) 판정을 보류한다(단일스레드 병목 보호). 유휴 판정에는
  반영하지 않는다 -- 64코어 중 한 코어만 바쁜 호스트도 집계 p95 가 낮고 나머지 활동 축이 조용하면 유휴로 분류된다.
- 실행 큐(run queue)는 "CPU 를 기다리는 프로세스 수"다. 코어당 1개 이상(Windows 는 ready-only 라 2개 기준)
  이면서 사용률도 70% 이상일 때만 포화로 센다 -- 메모리와 같은 dual-gate 로, 수집기 자신이 R-state 에 잡히는
  저활동 노이즈를 배제한다(사용률 미측정이면 실행 큐 단독 판정). load average 는 D-state IO 대기가 섞여
  오염되므로 쓰지 않는다.
- steal(가상화에서 하이퍼바이저에 뺏긴 시간)로 인과를 가른다: steal p95 5% 이상(벤더확인 AWS/Datadog)이면
  실행 큐가 높아도 하이퍼바이저 경합이라 vCPU 증설로 안 풀리고, 이용률·포화 수치 자체가 오염됐다고 보아
  충실도(biased) 하향을 남긴다(하향 조건은 steal 단독 -- 실행 큐 동반 요구 없음). 증설 처방 자체는 그대로
  낸다(관측된 부족을 누락하지 않는다). biased 가 실제로 막는 것은 다운사이즈 구체 처방이다 -- 오버서브스크립션
  fleet 에서 잘못된 감축을 막는 장치다.
- 유휴는 활동 3축이 전부 조용할 때만이다: CPU p95 3% 이하 AND 네트워크 2Mbps 이하 AND 디스크 baseline 5 IOPS
  이하. CPU·네트워크는 측정값이 있어야 유휴가 되고, 디스크 활동은 미측정이면 유휴를 막지 않는다. 그중 CPU peak
  1% 이하 AND 네트워크 1 kB/s 이하는 확실 유휴로 즉시 종료를, 그 외 유휴는 통합·재배치를 권고한다.
- 스파이크 방어: 사용률·큐 모두 p95(순간 100% burst 무시). 유휴 판정도 p95(순간 스파이크로 유휴 탈락 방지).

## Memory

| 축 | 신호 | 임계 | 근거 |
|----|------|------|------|
| Utilization | 사용률 p95 (양 OS 1 - available/limit) | under 90% / 목표 80% | 벤더문서 (Azure Advisor, AWS Compute Optimizer, MS Perfmon) |
| Saturation | dual-gate: 사용률 높음 AND 하드폴트 발생 | used 90% 이상 AND 하드폴트 발생, 둘 다 | 관례 (MS Perfmon, Red Hat vmstat) |
| Errors | oom_kill / hardware_corrupted | oom 1건 이상 = under 확정 / 손상 > 0 = health | 관례 (Gregg USE, kernel meminfo) |

- 사용률은 available(캐시 제외 실효 여유) 기준이라 페이지캐시로 부풀지 않는다. 사이징은 near-peak(관측
  피크) 80% 착지(20% 여유) -- AWS 기본과 정합, 비용 최적 방향. 분류(under 90%)는 p95, 사이징은 near-peak 로
  통계가 갈린다. 사용률 산식은 OS 를 가르지 않고, 포화 신호원만 OS 별로 갈린다(아래 "OS 차이").
- 포화는 dual-gate(AND) 다: "사용률 90% 이상" 이면서 동시에 "paging_major(하드폴트) 발생" 일 때만 포화로
  본다. 하나만으로는 오탐이 난다 -- 하드폴트만 보면 mmap DB 나 프로그램 시작의 정상 디스크 읽기를 포화로
  오인하고(예: RAM 88GB 남는 서버에 메모리 증설 권고), 사용률만 보면 페이지캐시로 90% 찬 정상 서버를 오인한다.
  둘 다 참이면 "메모리가 빡빡한데 실제로 디스크 폴트 비용을 치르고 있다" = 진짜 압박이다.
- Linux 하드폴트 신호는 paging_major(디스크에서 페이지를 다시 읽어온 major fault)다. swap 점유량도 swap
  in/out 추이도 압박 신호로 쓰지 않는다(swappiness 유휴 스왑아웃 오탐) -- swap 은 용량만 보고, 압박은 위
  dual-gate 로 판정한다. oom_kill(메모리 부족으로 프로세스가 강제 종료된 사후 증거)은 gate 없이 즉시 under 확정.
- 스파이크 방어: paging 은 창 전체 delta 로 발생 여부만 보고 버킷 수를 세지 않는다. 마이크로버스트 오탐은
  dual-gate(이용률 동반)가 막는다.

## Disk 용량

| 축 | 신호 | 임계 | 근거 |
|----|------|------|------|
| Utilization | used% / inode used% (worst mount) | 85% 이상 | 관례 (모니터링 표준, df/inode) |
| Saturation | runway (다 찰 때까지 남은 일수) | 30일 미만 = 채워지는 중 | 판단 (프로비저닝 lead time) / 벤더문서 (Gregg ENOSPC) |
| Errors | 파일시스템 오류 (ext4/btrfs/mdraid) | 창 내 신규 > 0 = health | 벤더문서 (Gregg, btrfs stats, mdadm) |

- 용량은 "증설/감축"이 아니라 "다 찰 때까지 남은 시간(runway)"으로 본다. 마운트별 이력의 첫 값과 마지막 값으로
  낸 fill rate 를 외삽해 ENOSPC 도달 일수를 구하고, 30일 미만이면 채워지는 중으로 표시한다. 30일은 스토리지
  프로비저닝 lead time 기준의 판단값.
- inode 도 병렬로 본다: 바이트가 남아도 작은 파일이 폭증하면 inode 가 먼저 소진돼 ENOSPC 가 난다. XFS/btrfs 는
  inode 를 동적 할당해 free 값 해석이 약하나, 판정은 fstype 을 가르지 않고 보고된 inode 값을 그대로 쓴다.
- worst-mount 집계에서 빼는 것은 가상 fs(tmpfs·devtmpfs·overlay·squashfs·proc·sysfs 등)와 /boot 접두 마운트뿐이다.
  크기 기준 소형 파티션 배제는 없어서 작은 데이터 파티션이 호스트 판정을 지배할 수 있다.
- 스파이크 방어: 2점 fill rate 라 삭제·생성 튐에 약하고, 방어는 최소 관측 span 게이트뿐이다 -- span 1.25일
  미만이면 runway 를 산출하지 않아 정적 가드만 남고, 14일 미만이면 1년 목표 외삽을 억제하고 30일 근시 예측만
  쓴다. 비단조 추세의 한계는 `docs/explanation/tradeoffs.md` T18.

## Disk IO

포화 여부만 표시한다(증설 숫자 미산출). virtio 게스트 지연은 하이퍼바이저·이웃 간섭 편향이라 표본으로 안
줄어들어 biased 로 강제한다.

| 축 | 신호 | 임계 | 근거 |
|----|------|------|------|
| Utilization | device %util (io_time / wall) | 사용률 50% 미만 버킷은 await 산출에서 제외 / 표시값 자체엔 임계 없음 | 판단 (tick 기반 await 왜곡 방어) |
| Saturation | await p95 (응답시간) | 20ms 초과 = io_bound | 벤더확인 (VMware, SQL Server, iostat) |
| Errors | disk_error (mdraid/btrfs/ext4/eventlog) | 창 내 신규 > 0 = health | 벤더확인 (btrfs stats, mdraid) |

- await(요청당 평균 응답시간, ms)를 주 신호로 쓴다. p95 가 20ms 를 넘으면 io_bound. HDD/SSD 차등(SSD 는 10ms)은
  벤더확인 값이나 임계를 미디어별로 가르지 않아 평면 20ms 다 -- rotational 은 인벤토리에 수집·표시되지만 await
  임계 분기에는 연결되어 있지 않다.
- %util(디스크가 바쁜 시간 비율)은 값 자체로 분류하지 않는다 -- SSD/NVMe 는 병렬 처리라 여유가 있어도 100% 로
  나와 오탐한다. 대신 await 채택 게이트로 쓴다: 사용률 50% 미만 버킷의 await 는 writeback 큐 잔류로 폭증해도
  병목이 아니라 산출에서 뺀다(그래서 저활동 device 는 await 미산출 -> io_ok). Windows 는 IOCTL 응답시간
  (동일 20ms), 구세대 viostor 는 큐 깊이 2 폴백.
- 스파이크 방어: await p95(순간 지연 max 무시) + 사용률 50% 미만 버킷 제외.

## Network

사이징 축이 아니다(vNIC 은 link_speed 가 없어 이용률 기반 사이징 불가). 혼잡 여부만 별도 플래그로 노출한다.

| 축 | 신호 | 임계 | 근거 |
|----|------|------|------|
| Utilization | throughput / link_speed | 사이징 임계 없음 / 유휴 2Mbps 이하 | 판단 (Gregg, AWS idle) |
| Saturation | drop rate + conntrack 비율 | drop 0.5% 초과 / conntrack 80% 이상 | 관례 (Gregg, Prometheus) |
| Errors | rx/tx_errors + tcp 재전송률 | 지속 nonzero = health / 재전송 1% 초과 | 벤더문서 (Gregg, TCP retransmit 관례) |

- 로컬 포화는 두 신호로 본다: drop(NIC 링버퍼·큐가 넘쳐 패킷을 버림 = 호스트가 못 따라감)과 conntrack(연결추적
  테이블이 참 = 신규 연결 못 받음). conntrack 은 80% 단일 임계다(진입/해제 분리 없음).
- 재전송·드롭은 창 평균 트래픽 10 kB/s 미만이면 판정을 보류한다 -- 저트래픽 호스트에서는 부팅기 소수 이벤트가
  비율을 지배해 분모가 무너진다. conntrack 은 트래픽량과 무관한 절대 신호라 이 게이트를 받지 않는다.
- 재전송(retransmit)은 포화가 아니라 Errors 로 뒀다. 재전송을 유발한 패킷 손실은 원격 라우터·WAN 어디서든 날
  수 있어 이 호스트의 포화가 아닌 경우가 많다. 포화로 세면 원격 혼잡을 이 호스트 탓으로 오귀속한다. Gregg 의
  USE 분류도 dropped=saturation / retransmit=errors 로 나눈다. 재전송은 사라지지 않고 health 에서 계속 보인다.
- Errors 는 전부 신규 발생 > 0 즉시 health 로 노출한다(물리 NIC 의 CRC/프레임 에러는 케이블·SFP 조기신호).
  virtio 게스트는 0 이라 무발화(무해).
- 스파이크 방어: counter 신호(drop/재전송)는 창 전체 delta 합계로 비율을 내고(버킷 수를 세지 않는다), 순간
  마이크로버스트(정상 TCP 혼잡제어) 오탐은 위 저트래픽 게이트가 막는다.

---

## 권고(처방) 방법 — 몇으로 늘려/줄일까

분류(under/over)가 "부족/과잉"을 가리면, 처방은 "그럼 몇으로"를 낸다. 방법은 목표 이용률 착지
(target-utilization sizing)다: 관측된 p95 이용률이 목표 %에 앉도록 크기를 역산한다.

- CPU: 목표 코어 = max(이용률 주도, 포화 주도) 중 큰 쪽.
  - 이용률 주도 = ceil(util_p95 x 현재코어 / 목표%). 예: 4코어에 util 90% -> ceil(90x4/70) = 6코어.
  - 포화 주도 = ceil(실행큐 / (포화선 x 0.7)). 실행큐를 포화선의 0.7배 아래로 떨어뜨릴 코어 수.
- Memory: 목표 = ceil(현재총량 x near_peak / 목표%). near_peak = 5분 버킷 max 의 p95(관측 피크 대표) — 메모리는 비탄력·OOM 위험이라 이용률 평균/eval-p95(버킷 avg 기반)이 아닌 피크 통계(버킷 max 의 p95)로 역산한다. p99.9 는 ~210 표본에서 절대 max 와 사실상 동일(단발 스파이크 지배)이라 견고하지 못해 p95 로 이상치 제외. 포화(하드폴트/OOM)면 현재총량 x 1.30(30% 상향)도 후보에 넣어 near-peak 기반 목표와 큰 쪽을 택한다. under 인데 현재 총량을 넘는 목표가 안 나오면 같은 1.30 상향값을 안전 하한으로 낸다. near-peak 미측정이면 사이징 통계는 p95 로 폴백한다.
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

목표 크기는 정수 코어/MB 올림(ceil)으로 낸다. 실제 인스턴스 SKU 스냅은 하지 않는다.

---

## 스파이크 오분류 방어 (공통 원칙)

임계 수치만큼 중요한 게 "순간 튀는 값에 속지 않기"다. 전 자원 공통으로 적용한다.

- 집계는 p95 기본: 게이지 신호(실행 큐, await)는 상위 5% 순간값을 잘라낸 p95 로 비교한다. conntrack 은 고갈
  임박 신호라 창 최대값으로 본다. 유휴 판정은 CPU p95 + 네트워크·디스크 평균 조합이고, 유휴 강도만 CPU peak 를 쓴다.
- 카운터는 delta: 오류·oom·재전송·drop·paging 같은 누적 카운터는 창 안의 증가분(delta)으로 본다. 절대
  nonzero 를 쓰면 과거 1회 이벤트가 영원히 켜져 있는 오탐이 난다. 재부팅/재시작은 counter_agg 가 흡수한다.
- 오탐 방어는 버킷 수가 아니라 gate 로 한다: 실행 큐·paging 은 이용률 동반(dual-gate)일 때만 포화로 세고,
  재전송·드롭은 창 전체 delta 합계 비율에 저트래픽 게이트를, await 는 device 사용률 게이트를 건다.
- 표본 충분성: 관측이 30시간 미만이면 신뢰도를 낮추고(하드 컷 아님), 다운사이즈는 창 대비 관측 비율 70%
  이상일 때만 처방한다. 측정 안 된 축은 "판정 불가"로 두고 없는 값을 지어내지 않는다.

## 판정에 쓰지 않는 수집 신호

아래는 수집·저장하되 판정 입력으로는 쓰지 않는다. 미사용 사유는 신호마다 다르다.

- PSI (Pressure Stall Information, Linux 4.20+): "자원이 부족해 태스크가 멈춘 시간"을 직접 재는 신호로 개념상
  포화의 정본이다. 하지만 "stall 몇 %부터 포화"라는 임계가 벤더 문서에 없고, 레거시 커널·Windows 에는 신호
  자체가 없어 마이그레이션 fleet 커버리지도 낮다. 판정은 위의 고전 신호로만 한다.
- rotational(HDD/SSD): 인벤토리에 수집돼 서버 상세와 assessment API 에 표시되지만 await 임계를 가르지 않는다.
- LVM 볼륨그룹 미할당 여유(`lvm_vgs`): 서버 상세 스토리지 트리와 assessment API 재현에 표시만 하고, 용량 판정·
  처방에는 반영하지 않는다.

## OS 차이

판정을 고전 신호로만 하므로 PSI 가 없는 Windows·구커널도 같은 축으로 판정된다(커버리지 공백 없음).

```
AXIS          | LINUX (all)                 | WINDOWS / LEGACY
--------------+-----------------------------+------------------------------------
CPU sat       | run queue / cores >= 1.0    | Processor Queue Length / cores >= 2.0
MEM sat       | paging_major + used (AND)   | Pages Input/sec p95 >= 20 + used (AND)
DISK-IO sat   | await > 20ms                | await (IOCTL), queue >= 2 fallback
DISK err      | ext4/btrfs/mdraid           | eventlog
inode         | fstype 무관 (보고된 값)      | NTFS (inode 개념 없음)
conntrack     | nf_conntrack (로드 시)       | 없음 (WFP 별개)
PSI (전 자원) | 저장만, 판정 미사용          | 원래 없음
```

Windows 임계가 Linux 와 다른 것은 모집단 차이지 임계 불일치가 아니다(각 OS 정본 임계 그대로). steal 은 양 OS
가상화 게스트에서만 유의하다(베어메탈은 0).
