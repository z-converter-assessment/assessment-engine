# Right-sizing Thresholds — AWS Compute Optimizer + Azure Advisor

Industry-standard thresholds for right-sizing recommendations, derived from major cloud
providers' guidance. These inform whether a server is over-provisioned, under-provisioned,
idle, or optimal.

## CPU Thresholds

| Source | Over-provisioned | Under-provisioned | Idle |
|--------|------------------|-------------------|------|
| AWS Compute Optimizer | CPU p95 < 30% over 14 days | CPU p95 > 90% over 14 days | CPU p95 < 5% |
| Azure Advisor | CPU p95 < 5% over 7 days = shutdown candidate | CPU p95 > 80% sustained | Below shutdown threshold |
| Industry consensus | CPU p95 < 40% = downsize | CPU p95 > 80% sustained | CPU p95 < 3% sustained |

Window: 14 days is the standard evaluation window (AWS Compute Optimizer default).
Shorter windows (1-3 days) capture transient load; longer windows (14-30 days) capture
business cycles (weekday vs weekend patterns).

## Memory Thresholds

| Source | Watch | Critical |
|--------|-------|----------|
| AWS Compute Optimizer | Memory p95 > 80% | Memory p95 > 90% OR swap active |
| Industry consensus | Memory p95 > 70% over 14 days = consider upsize | Memory p95 > 85% sustained |

Memory thresholds are stricter than CPU because memory pressure cascades quickly into
swap, OOM kills, or page-cache eviction (which then increases disk I/O).

## iowait Thresholds

| Range | Diagnosis |
|-------|-----------|
| iowait p95 < 5% | Healthy — disk not bottleneck |
| iowait p95 5-15% | Watch — disk may be saturated |
| iowait p95 > 15% sustained | Critical — disk is bottleneck, CPU often blocked |

High iowait alongside low CPU utilization indicates the bottleneck is disk, not compute.
Upsizing CPU will not help — upgrade disk (IOPS, throughput) or distribute workload.

## Swap

| State | Interpretation |
|-------|---------------|
| swap_used = 0 KB sustained | Healthy — no memory pressure |
| swap_used spikes briefly | Watch — transient memory pressure |
| swap_used > 0 sustained | Critical — memory under-provisioned, severe performance impact |

Even modest sustained swap usage degrades performance dramatically. Memory should be
upsized immediately when this pattern appears.

## Idle/Shutdown Criteria

| Condition | Action |
|-----------|--------|
| CPU p95 < 3% AND no network activity sustained | Shutdown candidate (Azure Advisor pattern) |
| CPU p95 < 5% AND no business hours pattern | Idle — consider termination or consolidation |
| CPU p95 < 10% with regular usage spikes | Over-provisioned — downsize, do not shutdown |

Shutdown decisions require additional context: is this a backup server? A standby?
A development environment? Pure metric-based shutdown can break business continuity.

## Combined Classification Rules

Right-sizing classification combines multiple signals:

```
if cpu_p95 > 90% OR mem_p95 > 90%:
    -> under_provisioned (upsize)
elif iowait_p95 > 15% sustained:
    -> disk_bottleneck (investigate disk, do not just upsize CPU)
elif swap_used sustained:
    -> memory_under_provisioned (upsize memory)
elif cpu_p95 < 3% AND no network activity:
    -> shutdown (or shutdown_review with manual check)
elif cpu_p95 < 5%:
    -> idle (consolidate or downsize aggressively)
elif cpu_p95 < 30% AND mem_p95 < 50%:
    -> over_provisioned (downsize)
elif cpu_p95 in 30-70% AND mem_p95 in 50-80%:
    -> optimal (no change)
else:
    -> insufficient_data OR mixed_signals (manual review)
```

Insufficient data: when evaluation window has < 7 days of metrics, classification is unreliable.

## Reference

- AWS Compute Optimizer: thresholds derived from public documentation as of 2024.
- Azure Advisor: thresholds derived from public Azure portal recommendations.
- Brendan Gregg's USE Method: framework for combining the three axes (utilization,
  saturation, errors) into actionable diagnosis.
