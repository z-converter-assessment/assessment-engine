# Payload Schema 변경 제안

> 작성일: 2026-04-23
> 대상: payload-schema.md

---

## 변경 원칙

**원칙 1 — 에이전트는 raw 값만 전송한다**
에이전트는 커널/파일시스템에서 읽은 값을 그대로 전송한다.
delta 계산, 비율 산출 등 2차 가공은 consumer 또는 분석 엔진이 담당한다.

**원칙 2 — 서버 식별자는 공통 메타데이터에 하나만 둔다**
모든 메시지 타입에서 동일한 기준으로 서버를 식별할 수 있어야 한다.

---

## 변경 1 — `machine_id` 공통 메타데이터 이동

### 현황
`machine_id`가 `inventory` 메시지에만 포함된다.
`metrics`, `error` 메시지는 `hostname`만으로 서버를 식별해야 한다.

### 문제
- `hostname`은 운영 중 변경될 수 있다.
- inventory 도착 전에 metrics가 먼저 수신되면 서버를 특정할 수 없다.

### 제안
`machine_id`를 공통 메타데이터 필드로 승격한다. `hostname`은 보조 정보로 유지한다.

| 필드 | 변경 전 | 변경 후 |
|------|---------|---------|
| `machine_id` | inventory 전용 | **공통 메타데이터** |

**변경 후 공통 메타데이터**
```
message_type   string   "inventory" / "metrics" / "error"
machine_id     string   /etc/machine-id  ← 추가
agent_version  string
collected_at   string   ISO 8601 UTC
hostname       string
message_id     string   UUID v4
```

---

## 변경 2 — CPU: 퍼센트 → raw 누적 tick

### 현황
```json
"cpu_user_pct":   23.5,
"cpu_system_pct": 4.2,
"cpu_iowait_pct": 1.8
```

### 문제
- `/proc/stat`의 누적 tick에서 delta를 계산한 후 % 변환 → 에이전트가 이전 상태를 보관해야 한다 (stateful).
- `steal`%, `idle`%가 없어 VM 환경 분석(클라우드 자원 경합)에 정보 부족.
- `user + system + iowait`만으로는 나머지 CPU 시간의 성격을 알 수 없다.

### 제안
`/proc/stat` 첫 번째 행(aggregate)의 누적 tick을 그대로 전송한다.
delta 계산 및 % 변환은 분석 엔진이 연속된 두 row의 `collected_at` 차이로 수행한다.

```json
"cpu_stat": {
  "user":    123456789,
  "nice":    0,
  "system":  23456789,
  "idle":    876543210,
  "iowait":  12345678,
  "irq":     0,
  "softirq": 123456,
  "steal":   56789
}
```

| 필드 | 변경 전 | 변경 후 |
|------|---------|---------|
| `cpu_user_pct`, `cpu_system_pct`, `cpu_iowait_pct` | 제거 | → `cpu_stat` 객체로 대체 |
| steal, idle | 없음 | `cpu_stat.steal`, `cpu_stat.idle` 추가 |

---

## 변경 3 — 메모리: 계산값 → raw `/proc/meminfo`

### 현황
```json
"mem_used_mb":      12288,
"mem_available_mb": 4096,ㅇ
"swap_used_mb":     0
```

### 문제
- `mem_used_mb`는 `/proc/meminfo`에 없는 값이다. `MemTotal - MemFree`, `MemTotal - MemAvailable`, `MemTotal - MemFree - Buffers - Cached` 중 어떤 공식인지 정의가 없다.
- `swap_used_mb`는 계산값(`SwapTotal - SwapFree`)이며, 분모인 `SwapTotal`이 inventory에 없어 사용률을 구할 수 없다.
- `swap_total_mb`가 inventory에 없어 swap 설정 여부도 확인 불가하다.

### 제안
`/proc/meminfo`의 raw 값을 전송한다. 단위는 kB 그대로.

```json
"mem_total_kb":     16777216,
"mem_free_kb":      4194304,
"mem_available_kb": 8388608,
"mem_buffers_kb":   524288,
"mem_cached_kb":    3145728,
"swap_total_kb":    2097152,
"swap_free_kb":     2097152
```

| 필드 | 변경 전 | 변경 후 |
|------|---------|---------|
| `mem_used_mb` | 제거 | raw 값으로 대체, 계산은 분석 엔진 |
| `mem_available_mb` | 제거 | `mem_available_kb` |
| `swap_used_mb` | 제거 | `swap_total_kb`, `swap_free_kb`로 대체 |
| swap_total | 없음 (inventory에도 없었음) | `swap_total_kb` 추가 |

> inventory의 `mem_total_mb`도 `mem_total_kb` (raw)로 통일한다.

---

## 변경 4 — Disk I/O: 초당 환산값 → raw 누적값

### 현황
```json
"disk_read_iops":   150,
"disk_write_iops":  85,
"disk_read_bytes":  6291456,
"disk_write_bytes": 3145728
```

### 문제
- `/proc/diskstats`의 누적값에서 delta 계산 후 수집 interval로 나눈 값 → 에이전트 stateful.
- 수집 지연·재시도 시 실제 interval이 60초가 아니면 값이 왜곡된다.
- 디바이스별 분리 없이 집계값만 있어 어느 디스크가 병목인지 알 수 없다.

### 제안
`/proc/diskstats`의 누적 카운터를 디바이스별로 그대로 전송한다.

```json
"disk_io": [
  {
    "device":           "vda",
    "reads_completed":  1234567,
    "writes_completed": 456789,
    "sectors_read":     9876543,
    "sectors_written":  4321098
  }
]
```

| 필드 | 변경 전 | 변경 후 |
|------|---------|---------|
| `disk_read_iops`, `disk_write_iops`, `disk_read_bytes`, `disk_write_bytes` | 제거 | → `disk_io[]` 배열로 대체 |

---

## 변경 5 — Network I/O: 초당 환산값 → raw 누적값

### 현황
```json
"net_rx_bytes": 1048576,
"net_tx_bytes": 524288
```

### 문제
- Disk I/O와 동일: delta 계산을 에이전트가 수행 → stateful. interval 가정 내포.
- 인터페이스별 분리 없이 집계만 있어 어느 NIC가 포화 상태인지 알 수 없다.

### 제안
`/proc/net/dev`의 누적 카운터를 인터페이스별로 전송한다. loopback(`lo`)은 제외.

```json
"net_io": [
  {
    "interface": "eth0",
    "rx_bytes":  123456789012,
    "tx_bytes":  456789012345
  }
]
```

| 필드 | 변경 전 | 변경 후 |
|------|---------|---------|
| `net_rx_bytes`, `net_tx_bytes` | 제거 | → `net_io[]` 배열로 대체 |

---

## 변경 6 — Disk 사용량: `disk_usage` 이름 충돌 해소 + raw 값

### 현황
`disk_usage` 필드가 inventory와 metrics 양쪽에 존재하는데 구조가 다르다.
- inventory: `{mount, total_mb, used_mb, avail_mb}`
- metrics: `{mount, usage_pct}`

### 문제
- 같은 이름, 다른 shape → 파싱 혼동, 스키마 진화 시 사고 가능.
- `usage_pct`는 statfs 결과를 가공한 값 → 원칙 1 위반.

### 제안
- inventory: `disk_usage` → **`mounts`** 로 rename. raw bytes 전송.
- metrics: `disk_usage` → **`mounts`** 로 rename. raw bytes 전송 (usage_pct 제거).

**inventory `mounts`**
```json
"mounts": [
  { "mount": "/",     "total_bytes": 32212254720, "free_bytes": 19327352832, "avail_bytes": 18253611008 },
  { "mount": "/data", "total_bytes": 107374182400, "free_bytes": 53687091200, "avail_bytes": 53687091200 }
]
```

**metrics `mounts`**
```json
"mounts": [
  { "mount": "/",     "total_bytes": 32212254720, "free_bytes": 16106127360, "avail_bytes": 15032385536 },
  { "mount": "/data", "total_bytes": 107374182400, "free_bytes": 53687091200, "avail_bytes": 53687091200 }
]
```

---

## 변경 7 — Load average: 1m → 1m + 5m + 15m

### 현황
```json
"load_1m": 1.25
```

### 문제
`/proc/loadavg`는 1m, 5m, 15m을 동시에 제공한다. 추가 비용 없이 읽을 수 있는데 1m만 전송하면 단기 스파이크와 지속적 부하를 구분할 수 없다.

### 제안
```json
"load_1m":  1.25,
"load_5m":  1.10,
"load_15m": 0.95
```

---

## 변경 8 — `error` 메시지 timestamp 이중화 제거

### 현황
`error` 메시지에 공통 메타데이터의 `collected_at`과 에러 전용 `timestamp`가 모두 존재.

### 문제
두 필드 중 어느 것이 에러 발생 시각인지 정의가 없다.

### 제안
`timestamp` 필드를 제거하고 `collected_at`을 에러 발생 시각으로 통일한다.

---

## 변경 요약

| # | 대상 | 변경 내용 |
|---|------|----------|
| 1 | 공통 메타데이터 | `machine_id` 추가 |
| 2 | metrics | `cpu_*_pct` 3개 → `cpu_stat` 객체 (raw ticks) |
| 3 | metrics | `mem_*_mb` / `swap_used_mb` → raw `/proc/meminfo` kB 값 |
| 4 | metrics | `disk_read/write_iops/bytes` → `disk_io[]` (raw 누적) |
| 5 | metrics | `net_rx/tx_bytes` → `net_io[]` (raw 누적, 인터페이스별) |
| 6 | inventory + metrics | `disk_usage` → `mounts` rename + usage_pct 제거 |
| 7 | metrics | `load_1m` → `load_1m`, `load_5m`, `load_15m` |
| 8 | error | `timestamp` 제거, `collected_at` 통일 |

## 에이전트 구현 영향

| 항목 | 현재 | 변경 후 |
|------|------|---------|
| 상태 보관 | CPU/Disk/Net delta를 위해 이전 값 보관 필요 | **불필요** — raw 누적값 그대로 전송 |
| 계산 로직 | delta 계산, % 변환, interval 측정 | **불필요** — read → serialize → publish |
| 복잡도 | stateful | **stateless** |