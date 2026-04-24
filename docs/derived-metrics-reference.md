# Derived Metrics Reference

> Raw 수집값으로부터 분석 엔진이 수행할 주요 연산 목록.
> consumer 또는 별도 분석 레이어에서 구현 예정.

---

## CPU

소스: `cpu_stat` (누적 tick)
두 연속 snapshot의 delta로 계산. `collected_at` 차이를 interval로 사용.

```
total_delta  = Σ(all tick fields delta)
user_pct     = user_delta   / total_delta * 100
system_pct   = system_delta / total_delta * 100
iowait_pct   = iowait_delta / total_delta * 100
steal_pct    = steal_delta  / total_delta * 100   # VM 자원 경합 지표
idle_pct     = idle_delta   / total_delta * 100
usage_pct    = (total_delta - idle_delta) / total_delta * 100
```

> **주의**: 멀티코어 환경에서 `/proc/stat` 첫 행은 모든 코어의 합산값.
> per-core 분석이 필요하면 `cpu0`, `cpu1`, … 행을 별도 수집해야 함.

---

## Memory

소스: `mem_*_kb`, `swap_*_kb` (raw `/proc/meminfo`)
단일 snapshot으로 계산 가능.

```
mem_used_kb      = mem_total_kb - mem_free_kb - mem_buffers_kb - mem_cached_kb
mem_used_pct     = mem_used_kb      / mem_total_kb  * 100
mem_available_pct= mem_available_kb / mem_total_kb  * 100

swap_used_kb     = swap_total_kb - swap_free_kb
swap_used_pct    = swap_used_kb / swap_total_kb * 100   # swap_total_kb == 0 이면 skip
```

> `mem_used_kb` 정의: `MemTotal - MemFree - Buffers - Cached`
> (`MemTotal - MemAvailable`은 커널 내부 회계 방식 차이로 값이 다를 수 있음)

---

## Disk I/O

소스: `disk_io[]` (디바이스별 누적 카운터, `/proc/diskstats`)
두 연속 snapshot의 delta. `/proc/diskstats` sector = 512 bytes.

```
interval_sec      = (collected_at[n] - collected_at[n-1]).total_seconds()

reads_delta       = reads_completed[n]  - reads_completed[n-1]
writes_delta      = writes_completed[n] - writes_completed[n-1]
sectors_r_delta   = sectors_read[n]     - sectors_read[n-1]
sectors_w_delta   = sectors_written[n]  - sectors_written[n-1]

read_iops         = reads_delta  / interval_sec
write_iops        = writes_delta / interval_sec
read_bytes_sec    = sectors_r_delta * 512 / interval_sec
write_bytes_sec   = sectors_w_delta * 512 / interval_sec
```

> 누적 카운터는 32-bit overflow 가능 (오랜 uptime). delta가 음수면 overflow로 간주하고 해당 구간 skip.

---

## Network I/O

소스: `net_io[]` (인터페이스별 누적 바이트, `/proc/net/dev`)
두 연속 snapshot의 delta.

```
interval_sec  = (collected_at[n] - collected_at[n-1]).total_seconds()

rx_bytes_sec  = (rx_bytes[n] - rx_bytes[n-1]) / interval_sec
tx_bytes_sec  = (tx_bytes[n] - tx_bytes[n-1]) / interval_sec
```

> loopback(`lo`)은 수집 제외.
> 64-bit 카운터이므로 overflow 가능성 낮으나, 재부팅 후 카운터 리셋에 주의 (delta 음수 → skip).

---

## Disk Usage

소스: `mounts[]` (statfs raw bytes)
단일 snapshot으로 계산 가능.

```
used_bytes  = total_bytes - avail_bytes
usage_pct   = used_bytes / total_bytes * 100
```

> `free_bytes` ≠ `avail_bytes`: `free_bytes`는 root 예약 블록 포함, `avail_bytes`는 일반 사용자 기준.
> usage_pct 계산에는 `avail_bytes` 사용 (실제 사용 가능 기준).

---

## Load Average

소스: `load_1m`, `load_5m`, `load_15m`
단일 snapshot, 변환 없음.

```
load_per_core = load_1m / cpu_cores   # 1.0 초과 시 포화 신호
```

> 5m, 15m 추세 비교로 부하 증가/감소 방향 판단.

---

## 주요 분석 지표 (Over/Under Provisioning)

| 지표 | 연산 | 판단 기준 |
|------|------|----------|
| CPU 과부하 | `usage_pct` avg/p95 | p95 > 80% → CPU 부족 |
| CPU steal | `steal_pct` avg | > 5% → 호스트 자원 경합 |
| 메모리 여유 | `mem_available_pct` avg | < 20% → 메모리 부족 |
| Swap 사용 | `swap_used_pct` avg | > 0% → 메모리 압박 신호 |
| 디스크 포화 | `usage_pct` per mount | > 85% → 용량 부족 |
| I/O 병목 | `iowait_pct` avg | > 10% → 디스크 병목 |
| NW 대역폭 | `rx/tx_bytes_sec` peak | NIC 용량 대비 % |