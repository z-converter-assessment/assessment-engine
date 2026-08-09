# 0003. 환경 CPU 서버 수는 사용률 기준으로 집계

## Status

Accepted

## Context

Linux `procs_running`에는 실행 중인 task와 CPU를 기다리는 task가 함께 들어가고, Windows `Processor Queue Length`는 ready queue를 측정한다. 두 값을 OS별 임계값으로 나눠도 원자료의 의미가 같아지지 않아 환경 전체에서 하나의 포화 서버 수로 합산할 수 없다.

## Decision

환경 성능 추이의 CPU 서버 수 지표는 `cpu.high_utilization_hosts`로 둔다. 버킷 안에서 CPU 사용률이 `CPU_UNDER_PCT` 이상인 서버를 센다. 실행 큐는 서버 상세와 실시간 현황에서 OS 표시 및 각 임계값과 함께만 보여 준다.

## Consequences

환경 CPU 서버 수는 Linux와 Windows에서 같은 CPU 시간 카운터 산식을 사용한다. 실행 큐의 OS별 원자료 차이는 환경 합산 지표에 반영하지 않는다.
