# USE Method — Performance Analysis Framework

USE Method (Utilization, Saturation, Errors) is a systematic performance analysis framework
developed by Brendan Gregg. It examines every resource for these three axes — providing a
checklist for diagnosing performance bottlenecks.

## Three Axes

### Utilization

Percentage of time the resource was busy servicing work.

- CPU utilization: percent CPU time spent in non-idle states (user + system + iowait + steal).
- Memory utilization: ratio of used pages to total pages.
- Disk utilization: percent time the disk was busy (active queue).
- Network utilization: ratio of measured throughput to interface capacity.

Healthy: utilization below moderate thresholds (e.g., CPU p95 below 60-70%).
Concerning: sustained high utilization indicates the resource is the bottleneck.

### Saturation

Degree to which the resource has extra work queued, beyond its current capacity.

- CPU saturation: run queue length (load average > CPU count).
- Memory saturation: swap-in/out activity or out-of-memory kills.
- Disk saturation: I/O wait time (iowait), queue depth.
- Network saturation: dropped packets, retransmits.

Saturation often appears before utilization reaches 100%, due to queuing dynamics.

### Errors

Count of error events emitted by the resource.

- CPU errors: machine check exceptions (rare).
- Memory errors: ECC corrections, OOM events.
- Disk errors: I/O failures, device errors.
- Network errors: TX/RX errors, dropped frames.

Even one error per minute can indicate hardware failure trajectory.

## Application to Right-sizing

Right-sizing decisions combine all three axes:

- High utilization + high saturation = under-provisioned (upsize)
- Low utilization + no saturation = over-provisioned (downsize)
- Low utilization + low saturation + no recent activity = idle (shutdown candidate)
- Errors > 0 = investigate before sizing (could mask real load)

## Reference Thresholds (industry consensus)

| Metric | Healthy | Watch | Critical |
|--------|---------|-------|----------|
| CPU p95 | < 60% | 60-80% | > 80% |
| Memory p95 | < 70% | 70-85% | > 85% |
| iowait p95 | < 5% | 5-15% | > 15% |
| Swap used | False (0 KB) | brief spikes | sustained > 0 |
| Load average / CPU count | < 0.7 | 0.7-1.0 | > 1.0 |

These thresholds are starting points — workload-specific tuning (database vs web vs batch) refines them.
