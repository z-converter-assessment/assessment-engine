# ADR 0052 — 자원 적정성 분류 원칙 재설계 (전제 기반 유도 + USE 5자원 + tier 근거)

상태: Accepted (2026-07-05) — Supersedes ADR 0029. 구현 대기 — 코드(`recommendation.py`)·구현 상태 문서(`right-sizing.md`)는 구현 단계에서 갱신(문서-코드 정합, CLAUDE.md #F12).

## Context

ADR 0029 가 right-sizing 분류를 OS-aware evidence 기반(`assess`)으로 세웠다. 그 방향(미측정은 미측정으로·OS별 신호 해석·부분 평가 노출)은 유효하나 임계·구조에 세 공백이 남았다:

1. 임계의 근거 부재 — 다수 임계가 무출처 경험값이었다. ADR 0029 자기 정정도 "Windows 페이징 절대 임계(1000 pages/sec) 근거 취약(잠정)"을 한계로 남겼다. mem 80% · disk 85% · paging 1000 이 뿌리 없는 값으로 남아 있었다.
2. CPU/메모리 중심 — `classify` 가 cpu·mem 이용률 + swap/iowait 포화 중심이고, 디스크 용량·디스크 I/O·네트워크가 분류 축의 1급 시민이 아니었다.
3. 종합·근본원인 부재 — 자원이 인과로 결합(메모리 -> 스왑 -> iowait -> CPU)돼도 독립 판정이라, 한 원인에 여러 자원을 삼중 처방할 위험.

목표: 임계를 개별로 튜닝하지 않고 두 전제에서 유도하며, 모든 임계가 (출처, 근거계층)으로 추적되는 자기 정당화 가능한 모델.

## Decision

두 전제에서 네 산출(분류·근거·권고·신뢰도)을 유도한다.

전제 1 — 근거 계층 (무엇을 사실로 받아들이나):
- 계층 1 방법론(USE Method) / 2a 시스템·수학 법칙(큐잉이론) / 2b OS 저작자 자기정의(커널·perfmon) / 3 클라우드 advisor 시작값 / 4 무출처 경험값.
- 규칙: 모든 임계는 (출처, 계층)을 선언. 계층 4(뿌리 없는 값)는 2·3으로 승격하거나 명시 폐기. -> 옛 계층 4 세 값 해소: mem 80 -> Azure Advisor 90(계층 3) · disk 85 -> monitoring 표준(계층 3) · paging 1000 -> 폐기(디스크 대역·워크로드 의존 고정 임계라 "단순 숫자 없음", 이용률로 대체). 뿌리 없는 값 0.

전제 2 — 여유 기준 (손실 비대칭):
- 잘못된 다운사이즈가 최악, 놓친 절감은 회수 가능. 안전이 하한선(제약), 그 위에서 절감 최대화.
- 부족 판정 적극(위험 신호 OR)·과다 판정 보수(전 축 낮을 때만 AND)·증설은 틀려도 안전.

자원 5개를 각각 USE(이용률·포화·오류)로 본다. 확정 임계·근거:
- CPU: 부족 이용률 p95 >= 70%(계층 2 큐잉 무릎 Kleinrock + 계층 3 AWS Compute Optimizer Balanced <70% P95) OR 실행큐/코어 >= 1(계층 1 USE). 사이징 증설·다운사이즈 공통 목표 70%(AWS Balanced, 비대칭 없음), 포화 증설 목표배수 0.7·per-core 보류 85%(여유 기준). procs_blocked 로 IO발 CPU 로드 분리, steal 은 신뢰도 단서.
- 메모리: 부족 이용률 90%(계층 3 Azure Advisor: CPU·메모리 >= SKU 90% 시 resize) · 증설 목표 70%(계층 3 AWS 최보수 30% headroom). swapless(fleet 실측 55%)는 이용률(Available 메모리)이 주신호, swap 호스트는 페이징(pswpin/pswpout, 계층 1). OOM 은 사후 보조. 물리 무릎 없어 임계는 advisor prior, 실측 튜닝 여지.
- 디스크 용량: under/over 아닌 소진까지 남은 시간(Theil-Sen 강건 기울기, 계층 2 통계). runway 목표 30일(여유 기준) · 정적 가드 85%(계층 3 monitoring 표준, 추세 신뢰도 낮을 때 fallback). inode 나란히 계산.
- 디스크 I/O: virtio 병렬 스토리지라 %util·avgqu 임계 무의미, 포화 주신호 await p95 > 20ms(계층 3 VMware read >20ms critical / SQL Server 10-15ms). 증분 불가라 사이징 없음 -> 더 빠른 티어 검토 표시 + IOPS 참고값만.
- 네트워크: 사이징 축 아님(vNIC 링크 속도 부재로 이용률% 불가). 유휴 감지(처리량)·품질 표시(TCP 재전송 > 1% or 드롭 > 0.5%, 계층 3 monitoring 관행). USE 오류축이 5자원 중 여기서 유일하게 채워짐.

신뢰도 축 (가로, 4종 불확실성 — 종류가 다르면 대응도 다름):
- 통계적 정밀도(표본 수·분산 한 축, 계층 2 order statistics + 계층 3 AWS insufficient-data 14일 창 누적 30h floor) / 커버리지(측정 공백 is_partial) / 측정 충실도(virtio 오염·근사 계통 편향, 표본으로 안 줄어듦) / 정상성(추세·계절성, forward-looking 결정에만).

종합·근본원인 규칙 (축 6):
- 인과 사슬(메모리 -> 디스크 I/O -> CPU, 계층 1/2b Gregg: load = CPU 대기 + D-state I/O 블록, swapping = 메모리 saturation) 상류로 root 자원 짚기. 판별 신호 = swap 발생 · procs_blocked(D-state) · await. iowait 는 다중코어 희석으로 강등(계층 2b Percona/Red Hat: D-state 직접 세기). root 에만 처방, 하류(증상)는 "root 해결 후 재평가" 표시. "worst 자원 승" 폐기.

다운사이즈 처방 규칙:
- 과다 "분류"는 저사용이면 늘 발화. 게이트되는 건 "구체적 다운사이즈 처방"뿐: 신뢰도 높음(이력 30h floor 훨씬 위 + 커버리지 온전 + 충실도 온전) AND 상승 추세 아님(보합·하락 OK). 불충족이면 과다 표시하되 권고는 "관찰만".

## Options Considered

1. 전제 기반 유도 + USE 5자원 + tier 근거 — 채택
   - 장점: 모든 임계가 (출처, 계층) 추적 -> 자기 정당화. 5자원 1급. 근본원인 종합으로 삼중 처방 방지. ADR 0029 의 paging 1000 한계 해소.
   - 단점: 구현 범위 큼(agent 신규 신호 발행 + `recommendation.py` 재구성).
2. ADR 0029 유지 + 임계만 tier 근거 보강
   - 장점: 구현 최소.
   - 단점: CPU/mem 중심·근본원인 부재 유지, 디스크 용량·I/O·네트워크가 2급 축으로 남음.
3. 클라우드 advisor 임계 그대로 채택
   - 장점: 근거 즉시.
   - 단점: advisor 는 조치 결정(계층 3)이지 사실(계층 2)이 아님 — 여유 기준(마이그레이션 안전)을 우리 맥락으로 다시 정해야. 그대로 쓰면 우리 손실 비대칭보다 공격적.

옵션 1 채택 — 근거 계층 규율로 뿌리 없는 값을 0으로 만들고, 5자원 USE + 근본원인 종합으로 구조 공백을 메운다.

## Consequences

장점:
- 뿌리 없는 값 0 — 모든 임계가 계층 2/3 앵커 또는 명시 폐기. ADR 0029 가 한계로 남긴 paging 1000 해소(폐기).
- 5자원 독립 판정 + 근본원인 종합 -> 증상 자원 삼중 처방 방지(절감 도구의 자기모순 제거).
- CPU·메모리 사이징이 AWS Balanced 단일 기준으로 통일 — 화면 간 정합.
- 신뢰도가 4종으로 분해돼, virtio 편향(표본으로 안 줄어듦)과 표본 부족(줄어듦)을 구분 대응.

단점·한계:
- 구현 미완 — 본 ADR 은 결정 기록. right-sizing.md(구현 상태 문서)·`recommendation.py`·agent wire 는 구현 단계에서 갱신(문서-코드 정합, F12). 구현 전까지 코드는 ADR 0029 를 따른다.
- Windows 디스크 I/O await 는 구세대 viostor 게스트(fleet 실측 11대 중 5대)에서 미측정 — 게스트 드라이버 계층 문제라 별도 infra/ETW 트랙. 신뢰도 커버리지(포화 미관측)로 정직 노출.
- 물리 무릎 없는 임계(메모리 이용률·await·네트워크 품질)는 계층 3 advisor prior — 실측(OOM·페이징·지연 상관)으로 튜닝 대상.

## 관련 문서·코드

- ADR 0029 — 본 ADR 이 supersede (OS-aware evidence·미측정 노출 방향은 계승, 코드 전환은 0052 구현 시)
- ADR 0043 — counter_agg 사전집계 (사이징 신호 원천 — 카운터 reset 일률 처리)
- `src/assessment_engine/recommendation.py` — 구현 대상 (자원별 판정 + 종합 재구성 예정)
- `docs/reference/right-sizing.md` — 구현 상태 명세 (구현 단계에서 신 모델로 갱신)
- `docs/explanation/tradeoffs.md` T14 — Windows 부분 평가/미측정 한계
- CLAUDE.md #E3 — right-sizing 분류 단일 진실 (구현 시 본 ADR 로 갱신)
